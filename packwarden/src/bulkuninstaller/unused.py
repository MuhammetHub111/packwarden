"""Uygulamaların son kullanım zamanını tahmin eder (yalnızca test/dev sürümü).

Linux'ta 11 paket kaynağı genelinde güvenilir bir "gerçek son çalıştırma
zamanı" API'si yok. Bunun yerine paketin ayarlar/önbellek/veri
klasörlerinin en son değiştirilme zamanı sinyal olarak kullanılır — bu
kesin değil, yalnızca yaklaşık bir tahmindir.
"""

import os

from .backends.base import Package
from .leftovers import find_package_leftovers


def last_used(pkg: Package) -> float | None:
    """pkg'ye ait kalıntı klasörlerinin en son değişim zamanı (epoch).

    Eşleşen klasör yoksa None döner — bu "bilinmiyor" demektir, asla
    "hiç kullanılmadı" diye yorumlanmamalı.

    Oyun kaynakları (Steam/Lutris/Heroic) kendi last_used değerini
    doğrudan Package üzerinde taşır (bkz. games/) — burada leftover
    sezgisine hiç düşülmez.
    """
    if pkg.last_used is not None:
        return pkg.last_used

    latest: float | None = None
    for _category, items in find_package_leftovers([pkg]):
        for item in items:
            try:
                mtime = os.path.getmtime(item.path)
            except OSError:
                continue
            if latest is None or mtime > latest:
                latest = mtime
    return latest
