from .apk import ApkBackend
from .appimage import AppImageBackend
from .apt import AptBackend
from .base import Backend, Package, RemoveResult, format_size
from .dnf import DnfBackend
from .flatpak import FlatpakBackend
from .nix import NixBackend
from .pacman import PacmanBackend
from .portage import PortageBackend
from .snap import SnapBackend
from .xbps import XbpsBackend
from .zypper import ZypperBackend

# Yeni bir paket yöneticisi desteklemek için: backends/ altına bir
# Backend alt sınıfı ekle ve bu listeye kaydet — başka değişiklik gerekmez.
ALL_BACKENDS: list[type[Backend]] = [
    PacmanBackend,    # Arch, CachyOS, Manjaro
    AptBackend,       # Debian, Ubuntu, Mint
    DnfBackend,       # Fedora, RHEL
    ZypperBackend,    # openSUSE
    ApkBackend,       # Alpine
    XbpsBackend,      # Void
    PortageBackend,   # Gentoo
    NixBackend,       # NixOS
    FlatpakBackend,   # dağıtımdan bağımsız
    SnapBackend,      # dağıtımdan bağımsız
    AppImageBackend,  # dağıtımdan bağımsız
]


def available_backends() -> list[Backend]:
    backends = []
    for cls in ALL_BACKENDS:
        backend = cls()
        if backend.is_available():
            backends.append(backend)
    return backends
