from .. import host
from .base import Backend, Package


class DnfBackend(Backend):
    """Fedora, RHEL, AlmaLinux, Rocky, ..."""

    id = "dnf"
    display_name = "DNF"
    needs_root = True

    def is_available(self) -> bool:
        return host.command_exists("dnf") and host.command_exists("rpm")

    def list_packages(self) -> list[Package]:
        proc = host.run(
            [
                "rpm", "-qa", "--qf",
                "%{NAME}\\t%{VERSION}-%{RELEASE}\\t%{SIZE}\\t%{SUMMARY}\\t%{VENDOR}\\n",
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
            try:
                size = int(parts[2])
            except (IndexError, ValueError):
                size = 0
            vendor = parts[4] if len(parts) > 4 else ""
            if vendor == "(none)":
                vendor = ""
            packages.append(Package(
                id=parts[0],
                name=parts[0],
                version=parts[1],
                size=size,
                description=parts[3] if len(parts) > 3 else "",
                source=self.id,
                publisher=vendor,
            ))
        return packages

    def remove_argv(self, ids: list[str]) -> list[str]:
        return ["dnf", "remove", "-y"] + ids
