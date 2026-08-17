import re
import time

from .. import host
from .base import Backend, Package, parse_human_size

_LOG_PATH = "/var/log/pacman.log"
# [2026-07-19T19:18:50+0300] [ALPM] installed glibc (2.43-2)
_LOG_LINE_RE = re.compile(r"^\[([^\]]+)\] \[ALPM\] installed (\S+) ")


class PacmanBackend(Backend):
    """Arch Linux, CachyOS, Manjaro, EndeavourOS, ..."""

    id = "pacman"
    display_name = "Pacman"
    needs_root = True

    def is_available(self) -> bool:
        return host.command_exists("pacman")

    def list_packages(self) -> list[Package]:
        # LC_ALL=C keeps the field names ("Name", "Installed Size")
        # stable regardless of the system locale.
        proc = host.run(["env", "LC_ALL=C", "pacman", "-Qi"], timeout=300)
        if proc.returncode != 0:
            return []

        self._repos = self._repo_map()
        self._foreign = self._foreign_set()
        self._true_install_dates = self._parse_log_install_dates()
        packages = []
        current: dict[str, str] = {}
        for line in proc.stdout.splitlines():
            if not line.strip():
                if current.get("Name"):
                    packages.append(self._to_package(current))
                current = {}
                continue
            if line.startswith(" "):  # continuation of the previous field
                continue
            key, sep, value = line.partition(":")
            if sep:
                current[key.strip()] = value.strip()
        if current.get("Name"):
            packages.append(self._to_package(current))
        return packages

    def _to_package(self, fields: dict[str, str]) -> Package:
        name = fields["Name"]
        if name in self._foreign:
            origin = "AUR / elle"  # resmî depolarda yok: AUR, GitHub, yerel paket
        else:
            origin = self._repos.get(name, "")
        return Package(
            id=name,
            name=name,
            version=fields.get("Version", ""),
            size=parse_human_size(fields.get("Installed Size", "")),
            description=fields.get("Description", ""),
            source=self.id,
            publisher=self._publisher(fields),
            origin=origin,
            install_date=self._true_install_dates.get(name)
            or self._install_date(fields),
            install_reason=self._install_reason(fields),
            required_by=self._required_by(fields),
            license=fields.get("Licenses", "").replace("  ", " ").strip(),
        )

    @staticmethod
    def _install_reason(fields: dict[str, str]) -> str:
        reason = fields.get("Install Reason", "")
        if reason.startswith("Explicitly"):
            return "explicit"
        if reason.startswith("Installed as a dependency"):
            return "dependency"
        return ""

    @staticmethod
    def _required_by(fields: dict[str, str]) -> int | None:
        raw = fields.get("Required By", "").strip()
        if not raw:
            return None
        parts = raw.split()
        if parts == ["None"]:
            return 0
        return len(parts)

    @staticmethod
    def _install_date(fields: dict[str, str]) -> float | None:
        # Yalnızca pacman.log okunamazsa ya da o pakete dair kayıt
        # yoksa devreye giren yedek: -Qi'nin kendi "Install Date"i
        # doğrulandığı gibi her yükseltmede yenileniyor, ilk kurulum
        # değil son işlem tarihini verir.
        raw = fields.get("Install Date", "")
        if not raw:
            return None
        try:
            return time.mktime(time.strptime(raw, "%a %b %d %H:%M:%S %Y"))
        except ValueError:
            return None

    @staticmethod
    def _parse_log_install_dates() -> dict[str, float]:
        """paket adı -> pacman.log'daki EN SON "installed" olayının zamanı.

        -Qi'nin "Install Date" alanı her yükseltmede yenileniyor
        (gerçek veriyle doğrulandı: glibc 19 Temmuz'da kuruldu, 27
        Temmuz'da yükseltildi, ama -Qi hâlâ 27 Temmuz'u gösteriyor).
        pacman'ın kendi eklemeli (append-only) günlüğü gerçek ilk
        kurulum anını zaten tutuyor — "upgraded" değil "installed"
        satırları aranarak yükseltmeler otomatik atlanıyor. Log
        döndürülmüş/kısaltılmışsa (çok eski bir paket için hiç kayıt
        yoksa) çağıran -Qi'nin alanına düşer."""
        dates: dict[str, float] = {}
        try:
            with open(_LOG_PATH, encoding="utf-8", errors="replace") as f:
                for line in f:
                    match = _LOG_LINE_RE.match(line)
                    if not match:
                        continue
                    timestamp_str, name = match.groups()
                    try:
                        dt = time.strptime(timestamp_str[:19], "%Y-%m-%dT%H:%M:%S")
                    except ValueError:
                        continue
                    dates[name] = time.mktime(dt)  # üzerine yaz, en son kalsın
        except OSError:
            pass
        return dates

    def _repo_map(self) -> dict[str, str]:
        """Paket adı → geldiği depo (core, extra, multilib, cachyos, ...)."""
        proc = host.run(["pacman", "-Sl"], timeout=120)
        if proc.returncode != 0:
            return {}
        repos: dict[str, str] = {}
        for line in proc.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                repos.setdefault(parts[1], parts[0])
        return repos

    def _foreign_set(self) -> set[str]:
        """Hiçbir senkron depoda olmayan paketler (AUR, GitHub, yerel)."""
        proc = host.run(["pacman", "-Qmq"], timeout=60)
        if proc.returncode != 0:
            return set()
        return set(proc.stdout.split())

    @staticmethod
    def _publisher(fields: dict[str, str]) -> str:
        # Projenin kendi adresi yayıncıyı en iyi anlatır (winehq.org gibi);
        # yoksa paketi derleyenin adı gösterilir (CachyOS, Arch Linux).
        url = fields.get("URL", "")
        if "//" in url:
            domain = url.split("//", 1)[1].split("/", 1)[0]
            domain = domain.removeprefix("www.")
            if domain:
                return domain
        packager = fields.get("Packager", "")
        return packager.split("<", 1)[0].strip()

    def remove_argv(self, ids: list[str]) -> list[str]:
        # -Rns: also remove unneeded dependencies and config backups
        return ["pacman", "-Rns", "--noconfirm"] + ids
