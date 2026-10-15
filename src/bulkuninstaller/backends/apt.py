import os
import re
import time

from .. import host
from .base import Backend, Package

_DPKG_INFO_DIR = "/var/lib/dpkg/info"
_DPKG_LOG_PATH = "/var/log/dpkg.log"
# 2026-07-19 19:18:50 install glibc:amd64 <none> 2.35-1
_DPKG_LOG_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) install (\S+?)(?::\S+)? "
)


class AptBackend(Backend):
    """Debian, Ubuntu, Linux Mint, Pop!_OS, ..."""

    id = "apt"
    display_name = "APT"
    needs_root = True

    def is_available(self) -> bool:
        return host.command_exists("apt-get") and host.command_exists("dpkg-query")

    def list_packages(self) -> list[Package]:
        proc = host.run(
            [
                "dpkg-query", "-W",
                "-f=${Package}\\t${Architecture}\\t${Version}\\t"
                "${Installed-Size}\\t${binary:Summary}\\t${Maintainer}\\n",
            ],
            timeout=300,
        )
        if proc.returncode != 0:
            return []

        # apt kendi kurulum nedenini (elle mi, bağımlılık olarak mı)
        # ayrıca izliyor — tek toplu komutla, paket başına çağrı yok.
        explicit = self._explicit_set()
        true_install_dates = self._parse_log_install_dates()

        rows = [
            line.split("\t") for line in proc.stdout.splitlines()
        ]
        rows = [parts for parts in rows if len(parts) >= 2 and parts[0]]

        # Multiarch: aynı paket adı birden fazla mimaride ayrı ayrı kurulu
        # olabilir (ör. libc6:amd64 + libc6:i386 - 32-bit uyumluluk/Steam/
        # Wine kurulumlarında yaygın). dpkg-query bunları İKİ AYRI satır
        # olarak basar, ikisi de ${Package}'de aynı çıplak adı verir -
        # mimari eklenmezse iki paket birebir aynı id/name ile arayüzde
        # ayırt edilemez hale gelir VE kaldırma komutu hangi mimariyi
        # hedefleyeceğini bilemez. Yalnızca GERÇEKTEN çakışan adlarda
        # (name_counts[name] > 1) ve mimari alanı gerçekten doluysa
        # "ad:mimari" biçimine geçiliyor - sıradan tek-mimarili paketlerin
        # id'si hiç değişmiyor.
        name_counts: dict[str, int] = {}
        for parts in rows:
            name_counts[parts[0]] = name_counts.get(parts[0], 0) + 1

        packages = []
        for parts in rows:
            name, arch = parts[0], parts[1] if len(parts) > 1 else ""
            if name_counts[name] > 1 and arch:
                display_id = f"{name}:{arch}"
            else:
                # Mimari alanı boş/bozuksa (olması beklenmez ama
                # dpkg-query çıktısı başka bir şekilde bozulursa diye)
                # belirsiz bir "ad:" kimliği UYDURMAK yerine önceki
                # (çıplak ad) davranışına düşülür - en kötü ihtimalle
                # önceden olduğu kadar belirsiz kalır, asla DAHA KÖTÜ
                # ya da var olmayan bir pakete işaret eder hale gelmez.
                display_id = name
            try:
                size = int(parts[3]) * 1024  # Installed-Size is in KiB
            except (IndexError, ValueError):
                size = 0
            maintainer = parts[5].split("<", 1)[0].strip() if len(parts) > 5 else ""
            install_reason = ""
            if explicit is not None:
                install_reason = "explicit" if name in explicit else "dependency"
            packages.append(Package(
                id=display_id,
                name=display_id,
                version=parts[2] if len(parts) > 2 else "",
                size=size,
                description=parts[4] if len(parts) > 4 else "",
                source=self.id,
                publisher=maintainer,
                install_date=true_install_dates.get(name) or self._install_date(name),
                install_reason=install_reason,
            ))
        return packages

    @staticmethod
    def _explicit_set() -> set[str] | None:
        proc = host.run(["apt-mark", "showmanual"], timeout=60)
        if proc.returncode != 0:
            return None
        return set(proc.stdout.split())

    @staticmethod
    def _install_date(name: str) -> float | None:
        # Yalnızca dpkg.log okunamazsa/döndürülmüşse devreye giren
        # yedek: .list dosyası her kurulum/yükseltmede yeniden
        # yazıldığından mtime'ı gerçek ilk kurulum değil, son işlem
        # tarihini verir (pacman'in "Install Date" alanında doğrulanan
        # aynı sorun).
        path = os.path.join(_DPKG_INFO_DIR, f"{name}.list")
        try:
            return os.stat(path).st_mtime
        except OSError:
            return None

    @staticmethod
    def _parse_log_install_dates() -> dict[str, float]:
        """paket adı -> dpkg.log'daki EN SON "install" olayının zamanı.

        dpkg, pacman gibi eklemeli (append-only) bir günlük tutuyor —
        "upgrade" satırları değil yalnızca "install" satırları aranarak
        yükseltmeler atlanır, gerçek ilk kurulum anı yakalanır. Günlük
        döndürülmüşse (çok eski bir paket için hiç kayıt yoksa) çağıran
        .list dosyasının değişim zamanına düşer."""
        dates: dict[str, float] = {}
        try:
            with open(_DPKG_LOG_PATH, encoding="utf-8", errors="replace") as f:
                for line in f:
                    match = _DPKG_LOG_LINE_RE.match(line)
                    if not match:
                        continue
                    timestamp_str, name = match.groups()
                    try:
                        dt = time.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        continue
                    dates[name] = time.mktime(dt)  # üzerine yaz, en son kalsın
        except OSError:
            pass
        return dates

    def remove_argv(self, ids: list[str]) -> list[str]:
        return [
            "env", "DEBIAN_FRONTEND=noninteractive",
            "apt-get", "purge", "-y", "--autoremove",
        ] + ids
