"""Bridge between the (possibly sandboxed) app and the host system.

When the app runs inside a Flatpak sandbox, package managers like pacman
or apt are not visible. flatpak-spawn --host forwards the command to the
host session via the Flatpak portal. Outside a sandbox, commands run
directly. Commands that need root are wrapped with pkexec, which shows
the desktop's polkit authentication dialog.
"""

import os
import subprocess

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


def spawn(argv: list[str]) -> None:
    """Bir uygulamayı ayrık süreç olarak başlatır (çıktı beklenmez)."""
    subprocess.Popen(
        host_argv(argv),
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def command_exists(name: str) -> bool:
    try:
        return run(["sh", "-c", f"command -v {name}"], timeout=15).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
