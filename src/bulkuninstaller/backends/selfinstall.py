import os
import re

from .. import host
from .base import Backend, Package, RemoveResult

INSTALL_DIR = "~/.local/share/packwarden"


class SelfInstallBackend(Backend):
    """install.sh ile kurulmuş PackWarden'ın kendisi.

    Uygulama diğer her şeyi listelerken kendini saklamasın: betikle
    kurulduysa listede görünür ve kendi kaldırma betiğiyle silinebilir.
    (Flatpak olarak kurulduysa zaten Flatpak kaynağında listelenir.)
    """

    id = "packwarden"
    display_name = "PackWarden"
    needs_root = False

    def is_available(self) -> bool:
        return os.path.isdir(os.path.expanduser(INSTALL_DIR))

    def list_packages(self) -> list[Package]:
        base = os.path.expanduser(INSTALL_DIR)
        version = ""
        try:
            init_text = open(
                os.path.join(base, "src/bulkuninstaller/__init__.py"),
                encoding="utf-8",
            ).read()
            match = re.search(r'VERSION\s*=\s*"([^"]+)"', init_text)
            if match:
                version = match.group(1)
        except OSError:
            pass

        size = 0
        for root, _dirs, files in os.walk(base, onerror=lambda _e: None):
            for name in files:
                try:
                    size += os.lstat(os.path.join(root, name)).st_size
                except OSError:
                    pass

        return [Package(
            id=base,
            name="PackWarden",
            version=version,
            size=size,
            description="Bulk application manager (this app)",
            source=self.id,
            publisher="MuhammetHub111",
        )]

    def remove_argv(self, ids: list[str]) -> list[str]:
        return ["sh", os.path.join(ids[0], "install.sh"), "remove"]

    def remove(self, ids: list[str]) -> RemoveResult:
        script = os.path.join(os.path.expanduser(INSTALL_DIR), "install.sh")
        try:
            proc = host.run(["sh", script, "remove"], timeout=120)
        except Exception as exc:
            return RemoveResult(False, str(exc), failed_ids=ids)
        output = (proc.stdout or "") + (proc.stderr or "")
        return RemoveResult(proc.returncode == 0, output)
