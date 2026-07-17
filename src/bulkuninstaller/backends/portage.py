import re

from .. import host
from .base import Backend, Package

_VERSION_RE = re.compile(r"-(\d[^/]*)$")


class PortageBackend(Backend):
    """Gentoo, Funtoo."""

    id = "portage"
    display_name = "Portage"
    needs_root = True

    def is_available(self) -> bool:
        return host.command_exists("emerge")

    def list_packages(self) -> list[Package]:
        # Kurulu paket veritabanı: /var/db/pkg/<kategori>/<ad-sürüm>/
        proc = host.run(
            ["sh", "-c", "ls -d /var/db/pkg/*/* 2>/dev/null"], timeout=120
        )
        if proc.returncode != 0:
            return []

        packages = []
        for line in proc.stdout.splitlines():
            parts = line.rstrip("/").split("/")
            if len(parts) < 2:
                continue
            category, pkgver = parts[-2], parts[-1]
            match = _VERSION_RE.search(pkgver)
            if match:
                name = pkgver[: match.start()]
                version = match.group(1)
            else:
                name, version = pkgver, ""
            packages.append(Package(
                id=f"{category}/{name}",
                name=name,
                version=version,
                size=0,
                description="",
                source=self.id,
                origin=category,
            ))
        return packages

    def remove_argv(self, ids: list[str]) -> list[str]:
        return ["emerge", "--unmerge", "--ask=n"] + ids
