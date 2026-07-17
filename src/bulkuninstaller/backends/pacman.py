from .. import host
from .base import Backend, Package, parse_human_size


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
        )

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
