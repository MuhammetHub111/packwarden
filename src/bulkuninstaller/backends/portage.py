import os
import re

from .. import host
from .base import Backend, Package

_VERSION_RE = re.compile(r"-(\d[^/]*)$")


class PortageBackend(Backend):
    """Gentoo, Funtoo."""

    id = "portage"
    display_name = "Portage"
    needs_root = True

    def is_available(self) -> bool:
        return host.command_exists("emerge")

    def list_packages(self) -> list[Package]:
        # Kurulu paket veritabanı: /var/db/pkg/<kategori>/<ad-sürüm>/
        proc = host.run(
            ["sh", "-c", "ls -d /var/db/pkg/*/* 2>/dev/null"], timeout=120
        )
        if proc.returncode != 0:
            return []

        rows = []
        for line in proc.stdout.splitlines():
            dir_path = line.rstrip("/")
            parts = dir_path.split("/")
            if len(parts) < 2:
                continue
            category, pkgver = parts[-2], parts[-1]
            match = _VERSION_RE.search(pkgver)
            if match:
                name = pkgver[: match.start()]
                version = match.group(1)
            else:
                name, version = pkgver, ""
            rows.append((dir_path, category, name, version))

        # SLOT: Gentoo'da aynı category/name'in birden fazla sürümü
        # (slotu) aynı anda kurulu kalabilir (ör. dev-lang/spidermonkey
        # hem :78 hem :115 ile) - version dizin adından geldiği için bu
        # satırlar birbirinden ayrı kalır, ama id yalnızca category/name
        # olduğundan İKİSİ DE AYNI id'yi üretirdi; kaldırma komutuna
        # slot'suz bir atom gidince emerge hangi slotu hedefleyeceğini
        # bilemez. SLOT bilgisi dizin ADINDA değil, dizinin İÇİNDEKİ ayrı
        # bir "SLOT" dosyasında tutulur (CONTENTS/CATEGORY/PF gibi diğer
        # VDB dosyalarıyla aynı yerde). Yalnızca GERÇEKTEN çakışan
        # (name_counts > 1) adlarda VE slot dosyası güvenilir biçimde
        # okunabildiğinde "category/name:slot" biçimine geçiliyor -
        # tek-slotlu sıradan paketlerin id'si hiç değişmiyor. Slot
        # okunamazsa/boşsa uydurma bir "ad:" kimliği ÜRETİLMİYOR - bu
        # satır eski (çakışan ama en azından var olan) haline düşer;
        # emerge'in slot'suz atomu nasıl çözümlediği (bkz. inceleme
        # raporu) bu turda değiştirilmiyor.
        name_counts: dict[str, int] = {}
        for _dir_path, category, name, _version in rows:
            candidate = f"{category}/{name}"
            name_counts[candidate] = name_counts.get(candidate, 0) + 1

        packages = []
        for dir_path, category, name, version in rows:
            candidate = f"{category}/{name}"
            display_id = candidate
            if name_counts[candidate] > 1:
                slot = self._read_slot(dir_path)
                if slot:
                    display_id = f"{candidate}:{slot}"
            try:
                # Paketin veritabanı kaydı emerge sırasında oluşturulur;
                # dizin mtime'ı kurulum zamanının en yakın karşılığı
                install_date = os.stat(dir_path).st_mtime
            except OSError:
                install_date = None
            packages.append(Package(
                id=display_id,
                name=display_id,
                version=version,
                size=0,
                description="",
                source=self.id,
                origin=category,
                install_date=install_date,
            ))
        return packages

    @staticmethod
    def _read_slot(dir_path: str) -> str:
        # SLOT dosyası bazen "0/4" gibi bir alt-slot de içerebilir - bu
        # ayrıca YORUMLANMAYA/BÖLÜNMEYE çalışılmıyor, dosyanın içeriği
        # ham hâliyle kullanılıyor. Bunu kendimiz ayrıştırıp yanlış
        # kısaltırsak (doğrulamadan) yanlış bir atom üretme riski
        # taşırdı - emerge zaten "slot/alt-slot" söz dizimini kendi atom
        # biçiminde destekliyor.
        try:
            with open(
                os.path.join(dir_path, "SLOT"), encoding="utf-8", errors="replace"
            ) as f:
                return f.read().strip()
        except OSError:
            return ""

    def remove_argv(self, ids: list[str]) -> list[str]:
        return ["emerge", "--unmerge", "--ask=n"] + ids
