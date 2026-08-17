from .. import host
from .base import Backend, Package


class NixBackend(Backend):
    """NixOS ve diğer dağıtımlardaki Nix kullanıcı profili."""

    id = "nix"
    display_name = "Nix"
    # Kullanıcı profili üzerinde çalışır; kök gerekmez
    needs_root = False

    def is_available(self) -> bool:
        return host.command_exists("nix-env")

    def list_packages(self) -> list[Package]:
        # "paketadi-1.2.3" satırları (kullanıcı profili)
        proc = host.run(["nix-env", "-q"], timeout=300)
        if proc.returncode != 0:
            return []

        packages = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            name, _, version = line.rpartition("-")
            if not name or not version[:1].isdigit():
                name, version = line, ""
            packages.append(Package(
                id=name or line,
                name=name or line,
                version=version,
                size=0,
                description="",
                source=self.id,
            ))
        return packages

    def remove_argv(self, ids: list[str]) -> list[str]:
        return ["nix-env", "-e"] + ids
