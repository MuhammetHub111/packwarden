import re

from .. import host
from .base import Backend, Package

# Nix'in kendi resmi kuralı: ad/sürüm sınırı, HARF OLMAYAN (rakamla
# başlayan) bir tireden sonra başlar - "sağdan ilk tire" değil,
# "soldan ilk UYGUN tire". nixpkgs kuralı sürümün rakamla başlamasını
# zorunlu kılar, ama sürümün KENDİSİ de tire içerebilir (resmi örnekler:
# "1.17.1-unstable-2025-05-24", "2.42-67") - bu yüzden sağdan tek tire
# ile bölmek (rpartition) bu tür sürümleri yanlış keser. Soldan ilk
# "tire+rakam" konumunu arayıp oradan sona kadarını sürüm saymak,
# Nix'in belgelenmiş algoritmasının birebir karşılığı - ad kaç tire
# içerirse içersin (ör. "git-minimal", "systemd-minimal-libs") doğru
# çalışır, çünkü ad kısmında tireden hemen sonra rakam gelmez (bu da
# nixpkgs'in kendi kuralı).
_VERSION_RE = re.compile(r"-(\d.*)$")


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
            match = _VERSION_RE.search(line)
            if match and match.start() > 0:
                # match.start() == 0 satırın TAMAMEN "-rakam..." ile
                # başladığı anlamına gelir - ad kısmı boş kalırdı,
                # bu da anlamsız; bu durumda tüm satır ada düşer.
                name = line[: match.start()]
                version = match.group(1)
            else:
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
