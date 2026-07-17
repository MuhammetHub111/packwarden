from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from .. import host


@dataclass
class Package:
    id: str            # identifier the package manager understands
    name: str          # human-readable name
    version: str
    size: int          # installed size in bytes, 0 if unknown
    description: str
    source: str        # backend id, e.g. "pacman"
    publisher: str = ""  # who makes/ships the app (project site, vendor)
    origin: str = ""     # repo/remote the package came from (extra, flathub, AUR)


@dataclass
class RemoveResult:
    ok: bool
    output: str
    cancelled: bool = False  # user dismissed the polkit dialog
    failed_ids: list[str] = field(default_factory=list)


class Backend(ABC):
    """One package source (pacman, apt, flatpak, ...).

    Adding support for a new distro means adding one subclass.
    """

    id: str = ""
    display_name: str = ""
    needs_root: bool = True

    @abstractmethod
    def is_available(self) -> bool:
        """Whether this package manager exists on the host."""

    @abstractmethod
    def list_packages(self) -> list[Package]:
        ...

    @abstractmethod
    def remove_argv(self, ids: list[str]) -> list[str]:
        """Host command that removes the given packages non-interactively."""

    def remove(self, ids: list[str]) -> RemoveResult:
        argv = self.remove_argv(ids)
        try:
            if self.needs_root:
                proc = host.run_privileged(argv)
            else:
                proc = host.run(argv, timeout=1800)
        except Exception as exc:  # timeout, portal failure, ...
            return RemoveResult(False, str(exc), failed_ids=ids)

        output = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode == 0:
            return RemoveResult(True, output)
        cancelled = self.needs_root and proc.returncode in (126, 127)
        return RemoveResult(False, output, cancelled=cancelled, failed_ids=ids)


def parse_human_size(text: str) -> int:
    """'4.31 MiB' / '1.2 GB' -> bytes. Returns 0 when unparseable."""
    parts = text.strip().split()
    if not parts:
        return 0
    try:
        value = float(parts[0].replace(",", "."))
    except ValueError:
        return 0
    unit = parts[1].lower() if len(parts) > 1 else "b"
    multipliers = {
        "b": 1,
        "kb": 1000, "kib": 1024,
        "mb": 1000**2, "mib": 1024**2,
        "gb": 1000**3, "gib": 1024**3,
        "tb": 1000**4, "tib": 1024**4,
    }
    return int(value * multipliers.get(unit, 1))


def format_size(size: int) -> str:
    if size <= 0:
        return ""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1000 or unit == "TB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1000
    return ""
