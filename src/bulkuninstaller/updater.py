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
import threading
import time
import urllib.request

from . import VERSION

SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REPO = "MuhammetHub111/packwarden"
REPO_URL = f"https://github.com/{REPO}"
RELEASES_URL = REPO_URL + "/releases"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPO}/releases/latest"
INSTALL_DIR = os.path.expanduser("~/.local/share/packwarden")

# İki eşzamanlı download_and_install() çağrısını (ör. açılıştaki otomatik
# denetim VE Ayarlar'dan elle "Denetle" aynı anda açık iki UpdateWindow
# üretip ikisinde de "Güncelle"ye basılırsa) engeller - ikisi de aynı
# INSTALL_DIR'ı hedefler, eşzamanlı çalışmaları yarış durumu yaratır.
_install_lock = threading.Lock()


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


def _backup_dir() -> str:
    # Fonksiyon olarak tutuluyor (sabit değil) - testler INSTALL_DIR'ı
    # taklit ettiğinde bu da her çağrıda GÜNCEL değeri okumalı.
    return INSTALL_DIR + ".update-backup"


def _is_valid_install(path: str) -> bool:
    """`path`nin çalışan bir PackWarden kurulumu gibi göründüğünü ucuzca
    doğrular - install.sh ve asıl paket dizini var mı diye bakar. Dosya
    bütünlüğünün tam garantisi değildir, sadece bir dizini SİLMEDEN/
    ÜZERİNE YAZMADAN önce "burada bariz şekilde eksik/yarım bir şey mi
    var" sorusuna güvenle cevap vermek için yeterlidir."""
    return os.path.isfile(os.path.join(path, "install.sh")) and os.path.isdir(
        os.path.join(path, "src", "bulkuninstaller")
    )


def _reconcile_install_and_backup(log) -> None:
    """Her download_and_install() çağrısının EN BAŞINDA çalışır.

    Önceki bir güncelleme _activate_new_install()'ın iki `os.rename`'i
    ARASINDA kesilmiş olabilir (süreç öldürüldü, güç kesildi - Python
    kodu bunu YAKALAYAMAZ). Bu durumda INSTALL_DIR eksik/bozuk ama
    yedek sağlam kalır - burada GERİ yüklenir. INSTALL_DIR zaten
    sağlıklıysa yedek artık gereksiz debris'tir (önceki güncelleme
    tamamlanmış ama temizlik adımı kesilmiş olabilir), temizlenir.
    İKİSİ de güvenilmez görünüyorsa KÖRÜ KÖRÜNE silinmez - hangisinin
    ne olduğuna bakmadan asla üzerine yazma/silme kararı verilmez;
    yedek benzersiz bir adla kenara alınır ki bu çağrı kendi ismini
    kullanabilsin ama hiçbir veri kaybolmasın.

    SINIR: bu yalnızca Python süreci GERÇEKTEN başlayabildiyse işe
    yarar. INSTALL_DIR'ın KENDİSİ tamamen eksikse ve uygulamanın
    başlatıcısı (.desktop/PYTHONPATH) doğrudan o yola bakıyorsa,
    uygulama hiç açılamaz ve bu kod hiç çalışma şansı bulamaz - bu en
    kötü durumda kurtarma yalnızca kullanıcının yedeği elle geri
    taşımasıyla mümkündür.
    """
    backup = _backup_dir()
    if not os.path.exists(backup):
        return

    install_ok = os.path.isdir(INSTALL_DIR) and _is_valid_install(INSTALL_DIR)
    if install_ok:
        shutil.rmtree(backup, ignore_errors=True)
        return

    if _is_valid_install(backup):
        log("Found an interrupted update - restoring the previous version…")
        if os.path.isdir(INSTALL_DIR):
            shutil.rmtree(INSTALL_DIR, ignore_errors=True)
        os.rename(backup, INSTALL_DIR)
        return

    quarantine = f"{backup}.unrecognized-{int(time.time())}"
    log(
        "Found a leftover update backup that doesn't look valid - moved "
        f"it aside to {quarantine} instead of deleting it."
    )
    os.rename(backup, quarantine)


def _staging_parent() -> str:
    """Mümkünse INSTALL_DIR ile AYNI dosya sistemindeki üst dizin -
    devreye alma adımının gerçek bir atomik `os.rename` olabilmesi
    için (bkz. _activate_new_install). Üst dizin yazılabilir değilse
    tempfile'ın kendi varsayılanına (genelde /tmp, ayrı bir dosya
    sistemi OLABİLİR) düşülür; devreye alma o zaman da ÇALIŞIR
    (shutil.move'un kopya+sil geri dönüşüne düşer) ama atomik olmaz."""
    parent = os.path.dirname(INSTALL_DIR)
    if os.path.isdir(parent) and os.access(parent, os.W_OK):
        return parent
    return tempfile.gettempdir()


def _activate_new_install(source_root: str) -> None:
    """`source_root`u INSTALL_DIR'a GÜVENLİ biçimde devreye alır.

    Mevcut kurulum SİLİNMEDEN önce yedek konumuna taşınır (aynı dosya
    sisteminde atomik bir `os.rename`); devreye alma herhangi bir
    adımda BAŞARISIZ olursa eski kurulum GERİ yüklenir ve istisna
    yeniden fırlatılır - çağıran taraf "hiçbir şey kaybolmadı" diyebilir.

    ATOMİKLİK SINIRI: `os.rename(INSTALL_DIR, backup)` ile
    `os.rename(source_root, INSTALL_DIR)` İKİ AYRI sistem çağrısıdır.
    Süreç tam bu ikisi ARASINDA öldürülürse/güç kesilirse, INSTALL_DIR
    kısa bir süre için gerçekten YOK olur - bu, Python'un
    try/except'iyle YAKALANAMAZ. Bir sonraki download_and_install()
    çağrısı _reconcile_install_and_backup() ile bunu düzeltir, AMA
    uygulamanın kendisi o an açılamıyorsa (bkz. o fonksiyonun SINIR
    notu) bu kurtarma hiç tetiklenemez. Bu fonksiyon riski ORTADAN
    KALDIRMAZ, sadece "neredeyse her zaman" kurtarılabilir hâle getirir
    ve normal (öldürülmeyen) hata yollarında %100 geri dönüşü garanti eder.
    """
    backup = _backup_dir()
    had_previous = os.path.isdir(INSTALL_DIR)
    if had_previous:
        # _reconcile_install_and_backup() zaten çalışmış olmalı (yedek
        # burada olmamalı) - savunma amaçlı yine de kontrol ediyoruz,
        # körü körüne üzerine yazmıyoruz.
        if os.path.exists(backup):
            shutil.rmtree(backup, ignore_errors=True)
        os.rename(INSTALL_DIR, backup)

    try:
        try:
            os.rename(source_root, INSTALL_DIR)
        except OSError:
            # Staging alanı farklı bir dosya sisteminde kaldıysa (bkz.
            # _staging_parent) - hâlâ çalışır, sadece atomik değildir.
            shutil.move(source_root, INSTALL_DIR)
    except Exception:
        if had_previous:
            if os.path.isdir(INSTALL_DIR):
                shutil.rmtree(INSTALL_DIR, ignore_errors=True)
            os.rename(backup, INSTALL_DIR)
        raise

    if had_previous:
        shutil.rmtree(backup, ignore_errors=True)


def download_and_install(progress, log, cancelled) -> bool:
    """Güncellemeyi indirip GÜVENLİ biçimde kurar (bkz. _activate_new_install).

    Eşzamanlı iki çağrıya karşı bir kilitle korunur - hem açılıştaki
    otomatik denetim hem Ayarlar'dan elle tetiklenen akış bu fonksiyonu
    çağırır (ikisi de UpdateWindow._on_update() üzerinden); aynı anda
    ikisinde de "Güncelle"ye basılırsa ikinci çağrı hemen "zaten sürüyor"
    diye başarısız olur, INSTALL_DIR'a hiç dokunmaz.

    progress(fraction|None, done_bytes, total_bytes|None) her parça
    sonrası çağrılır; log(str) günlük satırı ekler; cancelled() True
    dönerse işlem iptal edilir. Başarıysa True döner.
    """
    if not _install_lock.acquire(blocking=False):
        log("An update is already in progress.")
        return False
    try:
        return _download_and_install_locked(progress, log, cancelled)
    finally:
        _install_lock.release()


def _download_and_install_locked(progress, log, cancelled) -> bool:
    _reconcile_install_and_backup(log)

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
    # Mümkünse INSTALL_DIR ile AYNI dosya sisteminde - bkz. _staging_parent
    # docstring'i (devreye alma adımının gerçek bir atomik rename olması için).
    workdir = tempfile.mkdtemp(prefix="packwarden-update-", dir=_staging_parent())
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
        try:
            _activate_new_install(source_root)
        except Exception as exc:
            log(
                f"Error while installing the update: {exc}. The previous "
                "version was restored, nothing was lost."
            )
            return False
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
