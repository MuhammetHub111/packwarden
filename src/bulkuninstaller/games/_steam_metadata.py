"""Steam Store API'sinden oyunun gerçek stüdyosunu (geliştirici/yayıncı) çeker.

Yerel .acf kurulum kaydında bu bilgi hiç yok, Steam'in kendi ikili
önbelleği (appinfo.vdf) içeriyor ama Valve'ın özel format ve bu sürümde
ayrı bir dize tablosu ekliyor — onu elle çözmek yerine, Steam
istemcisinin zaten sürekli konuştuğu herkese açık Store API'si
kullanılıyor (kullanıcı onayladı). Sonuçlar diske kalıcı olarak
önbelleklenir; stüdyo adı bir oyun için pratikte hiç değişmediğinden
ilk yenilemeden sonra ağ isteği gerekmez.
"""

import json
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor

_CACHE_PATH = os.path.expanduser("~/.cache/bulkuninstaller/steam-studio.json")
_API_URL = "https://store.steampowered.com/api/appdetails?appids={}&l=english"


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


def _fetch_one(appid: str, timeout: int) -> str | None:
    """Stüdyo adı, hiç veri yoksa "", istek başarısız olduysa None
    (None sonraki yenilemede tekrar denenmesi gerektiğini işaretler —
    geçici ağ hatasını "bu oyunun stüdyosu yok" diye kalıcı önbelleğe
    almamak için)."""
    try:
        with urllib.request.urlopen(_API_URL.format(appid), timeout=timeout) as resp:
            payload = json.load(resp)
    except Exception:
        return None
    entry = payload.get(appid) or {}
    if not entry.get("success"):
        return ""
    data = entry.get("data") or {}
    for key in ("developers", "publishers"):
        values = data.get(key) or []
        if values:
            return values[0]
    return ""


def studios_for(appids: list[str], timeout: int = 6) -> dict[str, str]:
    """appid -> stüdyo adı. Önbellekte olmayan appid'ler için paralel
    istek atar; bulunamayanlar boş dize döner (çağıran genel bir yer
    tutucuya düşer)."""
    cache = _load_cache()
    missing = [a for a in dict.fromkeys(appids) if a not in cache]
    if missing:
        with ThreadPoolExecutor(max_workers=min(8, len(missing))) as pool:
            results = pool.map(lambda a: (a, _fetch_one(a, timeout)), missing)
        changed = False
        for appid, studio in results:
            if studio is not None:
                cache[appid] = studio
                changed = True
        if changed:
            _save_cache(cache)
    return {a: cache.get(a, "") for a in appids}
