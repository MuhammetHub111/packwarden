import os

from .. import host
from ._flathub_metadata import developers_for
from .base import Backend, Package, parse_human_size

_INSTALL_ROOTS = (
    "/var/lib/flatpak/app",
    os.path.expanduser("~/.local/share/flatpak/app"),
)


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

        rows = []
        for line in proc.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 2 or not parts[0]:
                continue
            rows.append(parts)

        # Flathub'ın kendi API'si gerçek geliştirici adını veriyor —
        # yalnızca oradan gelen uygulamalar için sorgulanır, üçüncü
        # parti depolardakiler ters-DNS tahminine düşer (aşağıda).
        flathub_ids = [
            parts[0] for parts in rows
            if len(parts) > 5 and parts[5].lower() == "flathub"
        ]
        developers = developers_for(flathub_ids)

        packages = []
        for parts in rows:
            app_id = parts[0]
            publisher = developers.get(app_id) or self._publisher(app_id)
            packages.append(Package(
                id=app_id,
                name=parts[1] or app_id,
                version=parts[2] if len(parts) > 2 else "",
                size=parse_human_size(parts[3]) if len(parts) > 3 else 0,
                description=parts[4] if len(parts) > 4 else "",
                source=self.id,
                publisher=publisher,
                origin=parts[5] if len(parts) > 5 else "",
                install_date=self._install_date(app_id),
                install_path=self._install_path(app_id),
            ))
        return packages

    @staticmethod
    def _install_path(app_id: str) -> str:
        for root in _INSTALL_ROOTS:
            path = os.path.join(root, app_id)
            if os.path.isdir(path):
                return path
        return ""

    @staticmethod
    def _install_date(app_id: str) -> float | None:
        # flatpak install tarihini ayrı vermiyor; etkin dağıtımın (deploy)
        # dizin mtime'ı kuruluşta veya son güncellemede yenilenir, en
        # yakın karşılığı bu.
        for root in _INSTALL_ROOTS:
            current = os.path.join(root, app_id, "current")
            try:
                target = os.readlink(current)
                deploy_dir = os.path.normpath(os.path.join(root, app_id, target))
                return os.stat(deploy_dir).st_mtime
            except OSError:
                continue
        return None

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
