"""Uygulamaların son kullanım zamanını tahmin eder (yalnızca test/dev sürümü).

Linux'ta 11 paket kaynağı genelinde güvenilir, sürekli işleyen bir
"gerçek son çalıştırma zamanı" API'si yok. İki sinyal birleştirilir:

1. usage.py: PackWarden açıkken yapılan taramalarda paketin fiilen
   çalıştığı tespit edilmişse, o anın zaman damgası (kesin ama yalnızca
   PackWarden'ın açık olduğu anları kapsar).
2. Eski sezgi: paketin ayarlar/önbellek/veri klasörünün en son
   değiştirilme zamanı (yaklaşık, uygulama o klasöre hiç yazmadan
   kapanırsa güncellenmez).

İkisinin en yenisi kullanılır — hangisi daha güncel bilgi taşıyorsa o
kazanır.
"""

import os

from . import usage
from .backends.base import Package
from .leftovers import find_package_leftovers


def last_used(pkg: Package) -> float | None:
    """pkg için bilinen en güncel "son kullanım" zamanı (epoch).

    Eşleşen sinyal yoksa None döner — bu "bilinmiyor" demektir, asla
    "hiç kullanılmadı" diye yorumlanmamalı.

    Oyun kaynakları (Steam/Lutris/Heroic) kendi last_used değerini
    doğrudan Package üzerinde taşır (bkz. games/) — burada diğer
    sinyallere hiç düşülmez.
    """
    if pkg.last_used is not None:
        return pkg.last_used

    candidates = []

    tracked = usage.get_seen(pkg)
    if tracked is not None:
        candidates.append(tracked)

    latest_leftover: float | None = None
    for _category, items in find_package_leftovers([pkg]):
        for item in items:
            try:
                mtime = os.path.getmtime(item.path)
            except OSError:
                continue
            if latest_leftover is None or mtime > latest_leftover:
                latest_leftover = mtime
    if latest_leftover is not None:
        candidates.append(latest_leftover)

    return max(candidates) if candidates else None
