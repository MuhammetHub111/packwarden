"""Uzak kapak/simge görsellerini yerel diske indirip önbelleğe alır.

Yalnızca gerçek yerel ikonu olmayan kaynaklar (ör. Heroic/Epic) için;
Steam gibi zaten yerelde önbelleklenmiş ikonu olan kaynaklar bunu hiç
kullanmaz. İndirme başarısız olursa (çevrimdışı, zaman aşımı vb.)
sessizce None döner — çağıran genel simgeye düşer.
"""

import hashlib
import os
import urllib.request

_CACHE_DIR = os.path.expanduser("~/.cache/bulkuninstaller/game-icons")


def cached_icon(url: str, timeout: int = 4) -> str | None:
    if not url:
        return None

    ext = os.path.splitext(url.split("?")[0])[1] or ".jpg"
    digest = hashlib.sha256(url.encode()).hexdigest()
    path = os.path.join(_CACHE_DIR, digest + ext)
    if os.path.isfile(path):
        return path

    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = resp.read()
        with open(path, "wb") as f:
            f.write(data)
    except Exception:
        return None
    return path
