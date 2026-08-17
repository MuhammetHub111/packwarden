from ..backends.base import Backend
from .heroic import HeroicAmazonBackend, HeroicEpicBackend, HeroicGogBackend
from .lutris import LutrisGameBackend
from .steam import SteamGameBackend

# Ana paket listesine/disk haritasına ASLA eklenmez — sadece Unused
# Apps taramasında kullanılır (bkz. unusedwindow.py). backends'in
# ALL_BACKENDS listesinden bilinçli olarak ayrı tutulur.
GAME_BACKENDS: list[type[Backend]] = [
    SteamGameBackend,
    LutrisGameBackend,
    HeroicEpicBackend,
    HeroicGogBackend,
    HeroicAmazonBackend,
]


def available_game_backends() -> list[Backend]:
    backends = []
    for cls in GAME_BACKENDS:
        backend = cls()
        if backend.is_available():
            backends.append(backend)
    return backends
