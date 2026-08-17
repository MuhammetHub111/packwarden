from .. import host
from .base import Backend, Package


class ApkBackend(Backend):
    """Alpine Linux, postmarketOS."""

    id = "apk"
    display_name = "APK"
    needs_root = True

    def is_available(self) -> bool:
        return host.command_exists("apk")

    def list_packages(self) -> list[Package]:
        # "paketadi-1.2.3-r0" satırları
        proc = host.run(["apk", "info", "-v"], timeout=300)
        if proc.returncode != 0:
            return []

        packages = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            # sürüm her zaman son iki tire ile eklenir: ad-1.2.3-r0
            pieces = line.rsplit("-", 2)
            if len(pieces) == 3:
                name, version = pieces[0], f"{pieces[1]}-{pieces[2]}"
            else:
                name, version = line, ""
            packages.append(Package(
                id=name,
                name=name,
                version=version,
                size=0,
                description="",
                source=self.id,
            ))
        return packages

    def remove_argv(self, ids: list[str]) -> list[str]:
        return ["apk", "del"] + ids
