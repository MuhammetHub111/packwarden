"""Güncelleme denetimi ve yeniden başlatma.

Geliştirme sürümünde "en son sürüm" diskteki kaynak koddan okunur:
kod her değiştiğinde sürüm numarası artırılır, çalışan sürümle
karşılaştırılır (latest_version/update_available — bunlar tamamen
yerel, ağ kullanmaz).

Gerçek güncelleme denetimi ise `main` dalının o anki hâline DEĞİL,
GitHub'ın "releases/latest" API'sine bakar — çünkü `main`'e giden her
commit otomatik olarak "güncelleme" sayılırsa, o dala sızan (yanlışlıkla
ya da hesap ele geçirilerek) herhangi bir şey otomatik güncelleyiciyle
her kullanıcının makinesine yayılır. Bir "release" ise yalnızca bilerek,
elle yayınlandığında oluşur.

İndirilen arşiv ayrıca aynı sürümün "checksum.txt" ekiyle karşılaştırılır
(bkz. _fetch_checksum) — bozuk/eksik bir indirmeyi ve arşivin GitHub'daki
sürümle birebir aynı olmadığı durumları yakalar. Bunun gerçek bir
kriptografik imza (GPG) OLMADIĞINI belirtmek gerekir: sağlama toplamı da
aynı GitHub hesabından geliyor, hesabın kendisi ele geçirilirse bu kontrol
tek başına yeterli olmaz — ama "her commit otomatik yayılır" riskini
kapatıyor ve indirme sırasında oluşan bozulmayı/eksik veriyi kesin olarak
yakalıyor.
"""

import hashlib
import json
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

REPO = "MuhammetHub111/packwarden"
REPO_URL = f"https://github.com/{REPO}"
RELEASES_URL = REPO_URL + "/releases"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPO}/releases/latest"
INSTALL_DIR = os.path.expanduser("~/.local/share/packwarden")


def _fetch_latest_release(timeout: int = 15) -> dict | None:
    try:
        req = urllib.request.Request(
            LATEST_RELEASE_API,
            headers={"Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except Exception:
        return None


def fetch_remote_version(timeout: int = 15) -> str | None:
    """GitHub'daki en son YAYINLANMIŞ (etiketli) sürüm numarası;
    ulaşılamazsa None. `main` dalının ucu değil, bilerek yayınlanmış
    bir "release" bekler."""
    release = _fetch_latest_release(timeout)
    if not release:
        return None
    tag = release.get("tag_name") or ""
    version = tag[1:] if tag.startswith("v") else tag
    return version or None


def _version_tuple(version: str) -> tuple:
    return tuple(int(part) for part in re.findall(r"\d+", version)[:3])


def is_newer(remote: str, local: str = VERSION) -> bool:
    try:
        return _version_tuple(remote) > _version_tuple(local)
    except Exception:
        return remote != local


def _fetch_checksum(tag: str, timeout: int = 15) -> str | None:
    """Sürümün checksum.txt ekinden beklenen SHA256'yı okur (64 hex
    karakter, ilk eşleşme). Bulunamazsa None — çağıran bunu "doğrulama
    yapılamıyor, kurma" olarak ele almalı, sessizce atlamamalı."""
    url = f"{REPO_URL}/releases/download/{tag}/checksum.txt"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", "replace")
    except Exception:
        return None
    match = re.search(r"\b[0-9a-fA-F]{64}\b", text)
    return match.group(0).lower() if match else None


def download_and_install(progress, log, cancelled) -> bool:
    """Güncellemeyi indirip kurar.

    progress(fraction|None, done_bytes, total_bytes|None) her parça
    sonrası çağrılır; log(str) günlük satırı ekler; cancelled() True
    dönerse işlem iptal edilir. Başarıysa True döner.
    """
    release = _fetch_latest_release()
    if not release:
        log("Could not reach GitHub to check the latest release.")
        return False
    tag = release.get("tag_name")
    tarball_url = release.get("tarball_url")
    if not tag or not tarball_url:
        log("Error: release metadata is incomplete.")
        return False

    log("Fetching checksum…")
    expected_sha256 = _fetch_checksum(tag)
    if not expected_sha256:
        log("Error: no checksum published for this release — refusing to install.")
        return False

    log("Downloading update…")
    response = urllib.request.urlopen(tarball_url, timeout=30)
    total_header = response.headers.get("Content-Length")
    total = int(total_header) if total_header else None

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz")
    done = 0
    digest = hashlib.sha256()
    try:
        while True:
            if cancelled():
                log("Update cancelled.")
                return False
            chunk = response.read(65536)
            if not chunk:
                break
            tmp.write(chunk)
            digest.update(chunk)
            done += len(chunk)
            progress(done / total if total else None, done, total)
    finally:
        tmp.close()
        if cancelled():
            os.unlink(tmp.name)

    if cancelled():
        return False

    log("Verifying checksum…")
    actual_sha256 = digest.hexdigest()
    if actual_sha256 != expected_sha256:
        log(
            f"Error: checksum mismatch (expected {expected_sha256[:12]}…, "
            f"got {actual_sha256[:12]}…). Update aborted, nothing was installed."
        )
        os.unlink(tmp.name)
        return False
    log("Checksum OK.")

    log("Extracting…")
    workdir = tempfile.mkdtemp(prefix="packwarden-update-")
    try:
        with tarfile.open(tmp.name) as tar:
            # "data" filtresi olmadan extractall, üye adlarındaki "../"
            # yollarını doğrulamadan kabul ediyor (Python'un eski
            # "fully_trusted" varsayılanı) — sağlama toplamı bu arşivle
            # aynı yayından geldiği için tek başına yeterli bir güvence
            # değil, arşivin kendisi de workdir dışına yazamayacak
            # şekilde kısıtlanmalı.
            tar.extractall(workdir, filter="data")
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
