"""Güncelleme denetimi ve yeniden başlatma.

Geliştirme sürümünde "en son sürüm" diskteki kaynak koddan okunur:
kod her değiştiğinde sürüm numarası artırılır, çalışan sürümle
karşılaştırılır. Uygulama Flathub'da yayınlandığında bu modül
Flatpak/GitHub sürüm denetimine bağlanacak.
"""

import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

from . import VERSION

SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REPO_URL = "https://github.com/MuhammetHub111/packwarden"
RELEASES_URL = REPO_URL + "/releases"
RAW_INIT_URL = (
    "https://raw.githubusercontent.com/MuhammetHub111/packwarden/"
    "main/src/bulkuninstaller/__init__.py"
)
TARBALL_URL = REPO_URL + "/archive/refs/heads/main.tar.gz"
INSTALL_DIR = os.path.expanduser("~/.local/share/packwarden")


def fetch_remote_version(timeout: int = 15) -> str | None:
    """GitHub'daki güncel sürüm numarası; ulaşılamazsa None."""
    try:
        with urllib.request.urlopen(RAW_INIT_URL, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", "replace")
    except Exception:
        return None
    match = re.search(r'VERSION\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else None


def _version_tuple(version: str) -> tuple:
    return tuple(int(part) for part in re.findall(r"\d+", version)[:3])


def is_newer(remote: str, local: str = VERSION) -> bool:
    try:
        return _version_tuple(remote) > _version_tuple(local)
    except Exception:
        return remote != local


def download_and_install(progress, log, cancelled) -> bool:
    """Güncellemeyi indirip kurar.

    progress(fraction|None, done_bytes, total_bytes|None) her parça
    sonrası çağrılır; log(str) günlük satırı ekler; cancelled() True
    dönerse işlem iptal edilir. Başarıysa True döner.
    """
    log("Downloading update…")
    response = urllib.request.urlopen(TARBALL_URL, timeout=30)
    total_header = response.headers.get("Content-Length")
    total = int(total_header) if total_header else None

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz")
    done = 0
    try:
        while True:
            if cancelled():
                log("Update cancelled.")
                return False
            chunk = response.read(65536)
            if not chunk:
                break
            tmp.write(chunk)
            done += len(chunk)
            progress(done / total if total else None, done, total)
    finally:
        tmp.close()
        if cancelled():
            os.unlink(tmp.name)

    if cancelled():
        return False

    log("Download finished.")
    log("Extracting…")
    workdir = tempfile.mkdtemp(prefix="packwarden-update-")
    try:
        with tarfile.open(tmp.name) as tar:
            tar.extractall(workdir)
        entries = [
            entry for entry in os.listdir(workdir)
            if os.path.isdir(os.path.join(workdir, entry))
        ]
        if not entries:
            log("Error: archive is empty.")
            return False

        log("Installing…")
        source_root = os.path.join(workdir, entries[0])
        if os.path.isdir(INSTALL_DIR):
            shutil.rmtree(INSTALL_DIR)
        shutil.move(source_root, INSTALL_DIR)
    finally:
        os.unlink(tmp.name)
        shutil.rmtree(workdir, ignore_errors=True)

    progress(1.0, done, total or done)
    log("Update installed.")
    return True


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
