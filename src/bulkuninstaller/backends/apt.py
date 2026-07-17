from .. import host
from .base import Backend, Package


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
                "-f=${Package}\\t${Version}\\t${Installed-Size}\\t"
                "${binary:Summary}\\t${Maintainer}\\n",
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
                size = int(parts[2]) * 1024  # Installed-Size is in KiB
            except (IndexError, ValueError):
                size = 0
            maintainer = parts[4].split("<", 1)[0].strip() if len(parts) > 4 else ""
            packages.append(Package(
                id=parts[0],
                name=parts[0],
                version=parts[1],
                size=size,
                description=parts[3] if len(parts) > 3 else "",
                source=self.id,
                publisher=maintainer,
            ))
        return packages

    def remove_argv(self, ids: list[str]) -> list[str]:
        return [
            "env", "DEBIAN_FRONTEND=noninteractive",
            "apt-get", "purge", "-y", "--autoremove",
        ] + ids
