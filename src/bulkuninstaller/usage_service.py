"""Arkaplan kullanım algılama servisinin (systemd --user) aç/kapa kontrolü.

Servis birimi (.service) ve başlatıcı script install.sh tarafından
kurulum sırasında yazılır ama hiç etkinleştirilmez — burada sadece
zaten kurulu olan birimi systemctl ile açıp kapatıyoruz. install.sh
hiç çalışmadıysa (ör. kaynaktan geliştirme modu) birim dosyası yoktur;
bu durumda açma isteği net bir hatayla reddedilir, sessizce
yoksayılmaz.
"""

import os

from . import host
from .i18n import _

SERVICE_NAME = "packwarden-usage-daemon.service"
UNIT_PATH = os.path.expanduser(f"~/.config/systemd/user/{SERVICE_NAME}")


def is_installed() -> bool:
    return os.path.isfile(UNIT_PATH)


def is_enabled() -> bool:
    try:
        proc = host.run(["systemctl", "--user", "is-enabled", SERVICE_NAME], timeout=5)
    except Exception:
        return False
    return proc.returncode == 0


def set_enabled(enabled: bool) -> tuple[bool, str]:
    """Servisi aç/kapat. (başarılı mı, hata mesajı) döner."""
    if not is_installed():
        return False, _(
            "Service unit not found — run install.sh to install PackWarden "
            "properly before enabling this."
        )
    verb = "enable" if enabled else "disable"
    try:
        proc = host.run(
            ["systemctl", "--user", verb, "--now", SERVICE_NAME], timeout=10
        )
    except Exception as exc:
        return False, str(exc)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "systemctl failed").strip()
    return True, ""
