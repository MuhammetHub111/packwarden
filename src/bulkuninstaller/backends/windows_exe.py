"""Kurulmamış, diskte duran tekil .exe dosyaları (İndirilenler vb.).

AppImage backend'iyle birebir aynı mantık: paket yöneticisine hiç
kayıtlı değiller, bilinen klasörler taranarak bulunuyorlar. "Kaldırmak"
burada dosyayı silmek demek — bunlar henüz kurulmamış, sadece diskte
duran bir kurulum dosyası veya taşınabilir program. Gerçekten Wine
üzerinden KURULMUŞ programlarla (backends/wine.py, "Program Ekle/
Kaldır" kaydı üzerinden) karışmasınlar diye ayrı bir kaynak — biri
"diskte duran dosya", diğeri "kurulu program".

İkon çıkarma wine.py'deki aynı yerel (ağa çıkmayan) mekanizmayı
kullanır — .exe dosyası zaten aynı formatta.
"""

import os

from .. import host
from .base import Backend, Package, RemoveResult
from .wine import _extract_icon

SCAN_DIRS = (
    "~/Downloads",
    "~/İndirilenler",
    "~/Desktop",
    "~/Masaüstü",
)

_DEFAULT_PREFIX = os.path.expanduser("~/.wine")


class WindowsExeBackend(Backend):
    """Kurulmamış, diskte duran tekil .exe dosyaları."""

    id = "winexe"
    display_name = "Windows Installer"
    needs_root = False

    def is_available(self) -> bool:
        return bool(self._scan()) and host.command_exists("wine")

    def list_packages(self) -> list[Package]:
        packages = []
        for path in self._scan():
            name = os.path.basename(path)
            if name.lower().endswith(".exe"):
                name = name[:-4]
            try:
                stat = os.stat(path)
                size = stat.st_size
                # Kurulum tarihi yok (henüz kurulmadı) — dosyanın
                # indirilme/kopyalanma zamanına en yakın karşılık ctime
                install_date = stat.st_ctime
            except OSError:
                size = 0
                install_date = None
            packages.append(Package(
                id=path,  # kaldırma/başlatma doğrudan dosya yoluyla
                name=name,
                version="",
                size=size,
                description=path,
                source=self.id,
                origin=os.path.basename(os.path.dirname(path)),
                install_date=install_date,
                install_path=path,
                icon_path=_extract_icon(path, path.replace(os.sep, "_")) or "",
            ))
        return packages

    def _scan(self) -> list[str]:
        found = []
        for base in SCAN_DIRS:
            base_dir = os.path.expanduser(base)
            try:
                entries = os.listdir(base_dir)
            except OSError:
                continue
            for entry in entries:
                if entry.lower().endswith(".exe"):
                    found.append(os.path.join(base_dir, entry))
        return sorted(found)

    def remove_argv(self, ids: list[str]) -> list[str]:
        return ["rm", "--"] + ids  # kullanılmıyor; remove() ezildi

    def remove(self, ids: list[str]) -> RemoveResult:
        errors = []
        for path in ids:
            try:
                os.remove(path)
            except OSError as exc:
                errors.append(f"{path}: {exc}")
        return RemoveResult(
            ok=not errors,
            output="\n".join(errors),
            failed_ids=[e.split(":", 1)[0] for e in errors],
        )
