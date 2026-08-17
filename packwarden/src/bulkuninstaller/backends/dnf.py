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
                "%{NAME}\\t%{VERSION}-%{RELEASE}\\t%{SIZE}\\t%{SUMMARY}\\t"
                "%{VENDOR}\\t%{INSTALLTIME}\\t%{LICENSE}\\n",
            ],
            timeout=300,
        )
        if proc.returncode != 0:
            return []

        # dnf, kullanıcının elle kurduğu paketleri ayrıca izliyor (dnf
        # history) — rpm veritabanının kendisinde böyle bir alan yok.
        # Tek toplu komutla alınır, paket başına ayrı çağrı gerekmez.
        explicit = self._explicit_set()

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
            try:
                install_date = float(parts[5]) if len(parts) > 5 else None
            except ValueError:
                install_date = None
            license_ = parts[6] if len(parts) > 6 else ""
            if license_ == "(none)":
                license_ = ""
            name = parts[0]
            install_reason = ""
            if explicit is not None:
                install_reason = "explicit" if name in explicit else "dependency"
            packages.append(Package(
                id=name,
                name=name,
                version=parts[1],
                size=size,
                description=parts[3] if len(parts) > 3 else "",
                source=self.id,
                publisher=vendor,
                install_date=install_date,
                license=license_,
                install_reason=install_reason,
            ))
        return packages

    @staticmethod
    def _explicit_set() -> set[str] | None:
        proc = host.run(["dnf", "repoquery", "--userinstalled", "--qf", "%{NAME}"],
                         timeout=120)
        if proc.returncode != 0:
            return None
        return set(proc.stdout.split())

    def remove_argv(self, ids: list[str]) -> list[str]:
        return ["dnf", "remove", "-y"] + ids
