import glob
import os

from .. import host
from .base import Backend, Package

# snapd, GUI sunan her snap için kendi .desktop dosyasını buraya koyar —
# bu yol snapd'nin kendi tasarımı, dağıtımdan bağımsız (Ubuntu/Debian/
# Fedora/openSUSE/Arch-CachyOS-Manjaro hepsinde aynı). Dosya adı deseni
# "<snap-adı>_<uygulama-adı>.desktop" — bir snap birden fazla uygulama
# sunabileceği için ada göre önek eşleşmesi (glob) doğru yaklaşım.
SNAP_DESKTOP_DIR = "/var/lib/snapd/desktop/applications"

# snapd'nin KENDİ kurallı veri kökü — /snap DEĞİL. Doğrulandı (resmi
# snapcraft.io belgeleri + bu sistemde canlı test): /snap, sadece
# Ubuntu'da varsayılan gelen; Fedora/Arch/openSUSE'de "classic
# confinement" desteği için KULLANICININ elle oluşturması gereken,
# /var/lib/snapd/snap'e işaret eden bir kısayol sembolik bağdan ibaret
# (bu sistemde hiç yok). Asıl veri her zaman burada:
# - <ad>/current: o snap'in şu an bağlı (mount edilmiş) sürümü
# - ../snaps/<ad>_<rev>.snap: gerçek, sıkıştırılmış squashfs dosyası
SNAPD_SNAP_DIR = "/var/lib/snapd/snap"
SNAPD_SNAPS_DIR = "/var/lib/snapd/snaps"

# snap list'in Notes sütununda görülebilecek, kullanıcının bilinçli
# kurmadığı, başka snap'lerin ihtiyaç duyduğu çalışma zamanı/altyapı
# değerleri — pacman'daki "dependency" kavramının snap karşılığı.
_DEPENDENCY_NOTES = {"base", "core", "snapd"}


def _has_desktop_entry(snap_name: str) -> bool:
    return bool(glob.glob(os.path.join(SNAP_DESKTOP_DIR, f"{snap_name}_*.desktop")))


def _installed_size(name: str, rev: str) -> int:
    """<ad>_<rev>.snap dosyasının gerçek boyutu; bulunamazsa 0.

    snap info'nun mağazaya gidip ~330ms süren "installed:" satırı yerine
    — bu tamamen yerel, tek bir stat() çağrısı, ağ hiç yok."""
    path = os.path.join(SNAPD_SNAPS_DIR, f"{name}_{rev}.snap")
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


class SnapBackend(Backend):
    """Snap paketleri (Ubuntu ve snapd kurulu her dağıtım)."""

    id = "snap"
    display_name = "Snap"
    needs_root = True

    def is_available(self) -> bool:
        return host.command_exists("snap")

    def list_packages(self) -> list[Package]:
        # Sütunlar: Name Version Rev Tracking Publisher Notes
        proc = host.run(["env", "LC_ALL=C", "snap", "list"], timeout=300)
        if proc.returncode != 0:
            return []

        packages = []
        for line in proc.stdout.splitlines()[1:]:  # başlık satırını atla
            parts = line.split()
            if len(parts) < 2 or parts[0] == "Name":
                continue
            name = parts[0]
            rev = parts[2] if len(parts) > 2 else ""
            notes = parts[5] if len(parts) > 5 else "-"
            publisher = parts[4].rstrip("✓*") if len(parts) > 4 else ""
            if publisher == "-":
                publisher = ""
            # snapd her zaman etkin sürümü SNAPD_SNAP_DIR/<ad>/current
            # altına bağlar (dağıtımdan bağımsız, snapd'nin kendi
            # tasarımı — bkz. yukarıdaki SNAPD_SNAP_DIR yorumu).
            install_path = os.path.join(SNAPD_SNAP_DIR, name, "current")
            install_date = None
            if os.path.isdir(install_path):
                try:
                    # os.stat DEĞİL os.lstat: "current" bir sembolik bağ,
                    # os.stat onu TAKİP EDİP mount edilmiş squashfs
                    # içeriğinin mtime'ını verir — bu da snap'in mağazada
                    # YAYINLANMA tarihi oluyor (doğrulandı: gimp için bu
                    # 2026-04-17 veriyordu, tam da snap info'nun channels
                    # bölümündeki yayın tarihiyle eşleşiyor — ama gimp bu
                    # sistemde bugün kuruldu). os.lstat sembolik bağın
                    # KENDİ mtime'ını verir; snapd her kurulum/güncellemede
                    # bu bağı yeniden yazdığı için gerçek yerel kurulum/
                    # güncelleme zamanını yansıtan doğru olan bu.
                    install_date = os.lstat(install_path).st_mtime
                except OSError:
                    pass
            else:
                install_path = ""
            note_set = set(notes.split(",")) if notes != "-" else set()
            install_reason = (
                "dependency" if note_set & _DEPENDENCY_NOTES else "explicit"
            )
            packages.append(Package(
                id=name,
                name=name,
                version=parts[1],
                size=_installed_size(name, rev) if rev else 0,
                description="",
                source=self.id,
                publisher=publisher,
                origin=parts[3] if len(parts) > 3 else "",
                install_path=install_path,
                install_date=install_date,
                install_reason=install_reason,
                has_desktop_entry=_has_desktop_entry(name),
            ))
        return packages

    def remove_argv(self, ids: list[str]) -> list[str]:
        return ["snap", "remove"] + ids
