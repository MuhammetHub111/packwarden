"""Bridge between the (possibly sandboxed) app and the host system.

When the app runs inside a Flatpak sandbox, package managers like pacman
or apt are not visible. flatpak-spawn --host forwards the command to the
host session via the Flatpak portal. Outside a sandbox, commands run
directly. Commands that need root are wrapped with pkexec, which shows
the desktop's polkit authentication dialog.
"""

import os
import subprocess
import threading

IN_FLATPAK = os.path.exists("/.flatpak-info")


def host_argv(argv: list[str]) -> list[str]:
    if IN_FLATPAK:
        return ["flatpak-spawn", "--host"] + argv
    return argv


def run(argv: list[str], timeout: int | None = 120) -> subprocess.CompletedProcess:
    """Run a command on the host and capture its output."""
    return subprocess.run(
        host_argv(argv),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_privileged(argv: list[str], timeout: int | None = 1800) -> subprocess.CompletedProcess:
    """Run a command on the host as root via pkexec.

    pkexec exit codes: 126 = user dismissed the auth dialog,
    127 = authorization failed.
    """
    return run(["pkexec"] + argv, timeout=timeout)


class SpawnedProcess:
    """Bir spawn() çağrısının sonucu — çağıran, kısa bir süre sonra
    sürecin gerçekten ayakta kalıp kalmadığını (ve çöktüyse hangi
    çıktıyı bıraktığını) kontrol edebilsin diye.

    Çıktı sürekli, arka planda bir thread'le okunup son birkaç KB'ı
    tutulur — hiç okunmasaydı, çıktı üreten uzun ömürlü bir uygulama
    işletim sisteminin pipe arabelleği dolunca yazma üzerinde
    tıkanabilirdi (klasik subprocess PIPE tuzağı); bu yüzden okuma
    sürecin TÜM ömrü boyunca sürer, sadece ilk kontrolle sınırlı değil.
    """

    _TAIL_LIMIT = 4096

    def __init__(self, proc: subprocess.Popen):
        self._proc = proc
        self._tail = b""
        self._lock = threading.Lock()
        if proc.stdout is not None:
            threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        try:
            for chunk in iter(lambda: self._proc.stdout.read(4096), b""):
                with self._lock:
                    self._tail = (self._tail + chunk)[-self._TAIL_LIMIT:]
        except (OSError, ValueError):
            pass

    def poll(self) -> int | None:
        """Süreç hâlâ çalışıyorsa None, bittiyse çıkış kodu."""
        return self._proc.poll()

    def output_tail(self) -> str:
        """Yakalanan çıktının (stdout+stderr birleşik) son kısmı."""
        with self._lock:
            return self._tail.decode("utf-8", "replace").strip()


def spawn(argv: list[str]) -> SpawnedProcess:
    """Bir uygulamayı ayrık süreç olarak başlatır.

    Ham Popen değil bir SpawnedProcess sarmalayıcısı döner — çağıran
    isterse kısa bir süre sonra .poll()/.output_tail() ile sürecin
    gerçekten açık kalıp kalmadığını ve çöktüyse neden olduğunu
    öğrenebilir; istemezse hiç dokunmayabilir, davranış aynı kalır."""
    proc = subprocess.Popen(
        host_argv(argv),
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return SpawnedProcess(proc)


def command_exists(name: str) -> bool:
    try:
        return run(["sh", "-c", f"command -v {name}"], timeout=15).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
