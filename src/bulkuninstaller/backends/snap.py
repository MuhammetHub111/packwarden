from .. import host
from .base import Backend, Package


class SnapBackend(Backend):
    """Snap paketleri (Ubuntu ve snapd kurulu her dağıtım)."""

    id = "snap"
    display_name = "Snap"
    needs_root = True

    def is_available(self) -> bool:
        return host.command_exists("snap")

    def list_packages(self) -> list[Package]:
        # Sütunlar: Name Version Rev Tracking Publisher Notes
        proc = host.run(["env", "LC_ALL=C", "snap", "list"], timeout=300)
        if proc.returncode != 0:
            return []

        packages = []
        for line in proc.stdout.splitlines()[1:]:  # başlık satırını atla
            parts = line.split()
            if len(parts) < 2 or parts[0] == "Name":
                continue
            publisher = parts[4].rstrip("✓*") if len(parts) > 4 else ""
            if publisher == "-":
                publisher = ""
            packages.append(Package(
                id=parts[0],
                name=parts[0],
                version=parts[1],
                size=0,
                description="",
                source=self.id,
                publisher=publisher,
                origin=parts[3] if len(parts) > 3 else "",
            ))
        return packages

    def remove_argv(self, ids: list[str]) -> list[str]:
        return ["snap", "remove"] + ids
