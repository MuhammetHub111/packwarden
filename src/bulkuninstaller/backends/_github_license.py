"""GitHub API'sinden, yerel/mağaza kaynağında bulunamayan lisansı çeker.

İkinci kademe: FlatpakBackend önce kendi yerel kaynağına bakar (flatpak
info — bkz. FlatpakBackend._license). Orada lisans yoksa (mağaza
yayıncısı hiç doldurmamışsa) ve uygulama kimliği GitHub'ı işaret
ediyorsa (io.github.<kullanıcı>.<proje> — Flatpak'ın standart ters-DNS
kalıbı), GitHub'ın deposu için otomatik tespit ettiği lisansı sorar.
Kimlik GitHub'ı işaret etmiyorsa hiç sorgulanmaz — üçüncü bir kademe
(mağaza sitesi kazıma, geliştirici sitesi arama vb.) kasıtlı olarak
yok, bakımı imkânsız/kırılgan olurdu.

Sonuçlar diske önbelleklenir (bkz. _flathub_metadata.py ve
_steam_metadata.py — benzer desen, ama KALICI değil): açık kaynak
lisansları nadiren ama bazen değişiyor (özellikle büyük/ticari destekli
projelerde) — bu yüzden bir girdi 60 günden eskiyse yeniden sorgulanır.
Küçük/hobi projelerinde (bu kademenin asıl hedef kitlesi) lisans
değişikliği zaten çok nadir, 60 gün makul bir denge.
"""

import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

_CACHE_PATH = os.path.expanduser("~/.cache/bulkuninstaller/github-license.json")
_API_URL = "https://api.github.com/repos/{}/{}"
_CACHE_TTL_SECONDS = 60 * 86400  # 60 gün


def _github_owner_repo(app_id: str) -> tuple[str, str] | None:
    """"io.github.<kullanıcı>.<proje>" -> (kullanıcı, proje); değilse None."""
    parts = app_id.split(".")
    if len(parts) >= 4 and parts[0] == "io" and parts[1] == "github":
        return parts[2], parts[3]
    return None


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


def _fetch_one(owner: str, repo: str, timeout: int) -> str | None:
    """SPDX lisans kodu, GitHub tespit edemediyse "", istek başarısız
    olduysa None (bir sonraki yenilemede tekrar denenir)."""
    try:
        req = urllib.request.Request(
            _API_URL.format(owner, repo),
            headers={"Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except Exception:
        return None
    license_info = data.get("license") or {}
    spdx = license_info.get("spdx_id")
    return "" if not spdx or spdx == "NOASSERTION" else spdx


def _is_fresh(entry) -> bool:
    """Önbellek girdisi geçerli desende ve 60 günden eski değil mi.

    Eski desen (düz dize, tarihsiz) veya bozuk bir girdi de "eski"
    sayılır — böylece kendiliğinden yeni desene geçer, elle taşımaya
    gerek kalmaz."""
    if not isinstance(entry, dict) or "fetched_at" not in entry:
        return False
    return (time.time() - entry["fetched_at"]) < _CACHE_TTL_SECONDS


def licenses_for(app_ids: list[str], timeout: int = 6) -> dict[str, str]:
    """app_id -> SPDX lisans kodu. Yalnızca io.github.* kalıbındaki
    kimlikler sorgulanır; diğerleri sonuç sözlüğünde hiç yer almaz.
    Önbellek girdisi 60 günden eskiyse yeniden sorgulanır."""
    targets = {}
    for app_id in app_ids:
        owner_repo = _github_owner_repo(app_id)
        if owner_repo:
            targets[app_id] = owner_repo
    if not targets:
        return {}

    cache = _load_cache()
    missing = [a for a in targets if a not in cache or not _is_fresh(cache[a])]
    if missing:
        with ThreadPoolExecutor(max_workers=min(8, len(missing))) as pool:
            results = pool.map(
                lambda a: (a, _fetch_one(*targets[a], timeout)), missing
            )
        changed = False
        for app_id, lic in results:
            if lic is not None:
                cache[app_id] = {"license": lic, "fetched_at": time.time()}
                changed = True
        if changed:
            _save_cache(cache)
    return {
        a: cache[a]["license"]
        for a in targets
        if isinstance(cache.get(a), dict) and cache[a].get("license")
    }
