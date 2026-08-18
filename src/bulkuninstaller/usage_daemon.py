"""Bağımsız, GTK'sız arkaplan kullanım algılama döngüsü.

`python3 -m bulkuninstaller.usage_daemon` olarak çalıştırılır; systemd
--user servisi olarak sürmesi için tasarlandı (bkz. install.sh'ın
yazdığı .service dosyası). Ayarlar'da "Arkaplanda kullanım algıla"
açılmadıkça bu servis hiç etkinleştirilmez/başlatılmaz — varsayılan
olarak sistemde çalışmaz.

Tamamen usage.py'nin zaten yazılmış scan_and_record() işlevini
kullanır, aynı gizlilik kuralları geçerlidir: hiçbir ağ isteği yok,
tek yazılan şey ~/.config/bulkuninstaller/usage.json'daki paket
kimliği -> zaman damgası.

Paket listesini çıkarmak (11 paket yöneticisini sorgulamak) görece
ağır bir iş olduğu için seyrek yenilenir; "şu an çalışıyor mu"
kontrolü ucuz olduğu için sık yapılır.
"""

import signal
import sys
import time

from . import usage
from .appicons import build_maps
from .backends import available_backends

PACKAGE_REFRESH_SECONDS = 15 * 60
SCAN_SECONDS = 2

_stop = False


def _handle_stop(_signum, _frame):
    global _stop
    _stop = True


def _load_packages():
    from .games import available_game_backends

    backends = available_game_backends() + available_backends()
    packages = []
    for backend in backends:
        try:
            packages.extend(backend.list_packages())
        except Exception:
            pass  # bozuk bir kaynak servisi çökertmemeli
    try:
        _icons, launcher_map, _categories = build_maps()
    except Exception:
        launcher_map = {}
    return packages, launcher_map


def main() -> int:
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    packages, launcher_map = _load_packages()
    last_refresh = time.monotonic()

    while not _stop:
        try:
            usage.scan_and_record(packages, launcher_map)
        except Exception:
            pass  # bir tarama hatası servisi tamamen durdurmamalı

        for _ in range(SCAN_SECONDS):
            if _stop:
                break
            time.sleep(1)

        if not _stop and time.monotonic() - last_refresh >= PACKAGE_REFRESH_SECONDS:
            packages, launcher_map = _load_packages()
            last_refresh = time.monotonic()

    return 0


if __name__ == "__main__":
    sys.exit(main())
