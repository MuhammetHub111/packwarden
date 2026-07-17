"""Güncelleme denetimi ve yeniden başlatma.

Geliştirme sürümünde "en son sürüm" diskteki kaynak koddan okunur:
kod her değiştiğinde sürüm numarası artırılır, çalışan sürümle
karşılaştırılır. Uygulama Flathub'da yayınlandığında bu modül
Flatpak/GitHub sürüm denetimine bağlanacak.
"""

import os
import re
import subprocess
import sys

from . import VERSION

SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def latest_version() -> str:
    init_path = os.path.join(SRC_DIR, "bulkuninstaller", "__init__.py")
    try:
        with open(init_path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return VERSION
    match = re.search(r'VERSION\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else VERSION


def update_available() -> bool:
    return latest_version() != VERSION


def restart_app() -> None:
    """Yeni bir örneği ayrık süreç olarak başlatır.

    Çağıran taraf hemen ardından uygulamayı kapatmalıdır. Yeni örnek,
    eski süreç tamamen kapanana kadar bekler; yoksa tek-örnek kilidi
    yüzünden eski sürüme bağlanıp hiçbir şey değişmemiş gibi görünür.
    """
    env = dict(os.environ)
    extra = os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    env["PYTHONPATH"] = SRC_DIR + extra
    old_pid = os.getpid()
    subprocess.Popen(
        [
            "sh", "-c",
            f'while kill -0 {old_pid} 2>/dev/null; do sleep 0.2; done; '
            'exec "$0" -m bulkuninstaller',
            sys.executable,
        ],
        env=env,
        start_new_session=True,
    )
