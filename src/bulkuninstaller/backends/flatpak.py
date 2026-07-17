from .. import host
from .base import Backend, Package, parse_human_size


class FlatpakBackend(Backend):
    """Flatpak apps — works the same on every distro."""

    id = "flatpak"
    display_name = "Flatpak"
    # flatpak talks to polkit itself when a system install needs it
    needs_root = False

    def is_available(self) -> bool:
        return host.command_exists("flatpak")

    def list_packages(self) -> list[Package]:
        proc = host.run(
            [
                "env", "LC_ALL=C",
                "flatpak", "list", "--app",
                "--columns=application,name,version,size,description,origin",
            ],
            timeout=300,
        )
        if proc.returncode != 0:
            return []

        packages = []
        for line in proc.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 2 or not parts[0]:
                continue
            packages.append(Package(
                id=parts[0],
                name=parts[1] or parts[0],
                version=parts[2] if len(parts) > 2 else "",
                size=parse_human_size(parts[3]) if len(parts) > 3 else 0,
                description=parts[4] if len(parts) > 4 else "",
                source=self.id,
                publisher=self._publisher(parts[0]),
                origin=parts[5] if len(parts) > 5 else "",
            ))
        return packages

    @staticmethod
    def _publisher(app_id: str) -> str:
        """Uygulama kimliğindeki ters alan adından yayıncıyı çıkar.

        com.valvesoftware.Steam -> valvesoftware.com
        io.github.kolunmi.Bazaar -> kolunmi.github.io
        """
        parts = app_id.split(".")
        if len(parts) < 2:
            return ""
        hosts = ("github", "gitlab", "codeberg", "sourceforge", "frama")
        if len(parts) >= 3 and parts[1] in hosts:
            return f"{parts[2]}.{parts[1]}.{parts[0]}"
        return f"{parts[1]}.{parts[0]}"

    def remove_argv(self, ids: list[str]) -> list[str]:
        return ["flatpak", "uninstall", "-y", "--noninteractive"] + ids
