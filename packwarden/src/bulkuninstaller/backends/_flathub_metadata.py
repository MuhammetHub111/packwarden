"""Flathub API'sinden uygulamanın gerçek geliştirici/yayıncı adını çeker.

Flatpak uygulama kimliği (ör. com.spotify.Client) yalnızca ters-DNS bir
tahminle yayıncıya çevrilebiliyordu (bkz. FlatpakBackend._publisher,
örn. "spotify.com") — Flathub'ın kendi API'si gerçek "developer_name"
alanını doğrudan veriyor. Yalnızca kökeni flathub olan uygulamalar için
sorgulanır (üçüncü parti depolardan gelenlerde bu bilgi yok). Sonuçlar
diske kalıcı önbelleklenir (geliştirici adı pratikte hiç değişmez),
ilk yenilemeden sonra ağ isteği gerekmez.
"""

import json
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor

_CACHE_PATH = os.path.expanduser("~/.cache/bulkuninstaller/flathub-developer.json")
_API_URL = "https://flathub.org/api/v2/appstream/{}"


def _load_cache() -> dict[str, str]:
    try:
        with open(_CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(cache: dict[str, str]) -> None:
    try:
        os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
        with open(_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f)
    except OSError:
        pass


def _fetch_one(app_id: str, timeout: int) -> str | None:
    """Geliştirici adı, hiç yoksa "", istek başarısız olduysa None
    (None bir sonraki yenilemede tekrar denenir — geçici ağ hatasını
    kalıcı olarak "bilgi yok" diye önbelleğe almamak için)."""
    try:
        with urllib.request.urlopen(_API_URL.format(app_id), timeout=timeout) as resp:
            data = json.load(resp)
    except Exception:
        return None
    return data.get("developer_name") or ""


def developers_for(app_ids: list[str], timeout: int = 6) -> dict[str, str]:
    """app_id -> geliştirici adı. Önbellekte olmayanlar için paralel istek atar."""
    cache = _load_cache()
    missing = [a for a in dict.fromkeys(app_ids) if a not in cache]
    if missing:
        with ThreadPoolExecutor(max_workers=min(8, len(missing))) as pool:
            results = pool.map(lambda a: (a, _fetch_one(a, timeout)), missing)
        changed = False
        for app_id, dev in results:
            if dev is not None:
                cache[app_id] = dev
                changed = True
        if changed:
            _save_cache(cache)
    return {a: cache.get(a, "") for a in app_ids}
