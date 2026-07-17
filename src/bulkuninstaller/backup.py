"""Kaldırma öncesi yedekleme.

Yedek içeriği:
- paketler.txt  → kaldırılan paketlerin listesi (kaynak, kimlik, sürüm);
  geri kurulum için başvuru.
- dosyalar.tar.gz → paketlere ait ayar/veri klasörleri. Önbellekler
  (~/.cache) yedeğe alınmaz; yeniden üretilebilir veridir ve arşivi
  gereksiz şişirir.
"""

import os
import tarfile
import time

from .backends.base import Package
from .leftovers import Leftover

BACKUP_BASE = "~/PackWarden-Yedek"


def _is_cache(path: str) -> bool:
    cache_dir = os.path.realpath(os.path.expanduser("~/.cache"))
    return os.path.realpath(path).startswith(cache_dir + os.sep)


def create_backup(
    packages: list[Package],
    leftovers: list[Leftover],
    base: str = BACKUP_BASE,
) -> str:
    """Yedeği oluşturur ve yedek klasörünün yolunu döner."""
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    dest = os.path.join(os.path.expanduser(base), stamp)
    os.makedirs(dest, exist_ok=True)

    with open(os.path.join(dest, "paketler.txt"), "w", encoding="utf-8") as f:
        f.write("# PackWarden yedeği — kaldırılan paketler\n")
        f.write("# Geri kurmak için örnek:\n")
        f.write("#   pacman  : sudo pacman -S <kimlik>\n")
        f.write("#   apt     : sudo apt install <kimlik>\n")
        f.write("#   dnf     : sudo dnf install <kimlik>\n")
        f.write("#   flatpak : flatpak install <kimlik>\n\n")
        for pkg in packages:
            f.write(f"{pkg.source}\t{pkg.id}\t{pkg.version}\n")

    _archive(dest, leftovers)
    return dest


def _archive(dest: str, leftovers: list[Leftover]) -> None:
    to_archive = [item for item in leftovers if not _is_cache(item.path)]
    if not to_archive:
        return
    archive_path = os.path.join(dest, "dosyalar.tar.gz")
    with tarfile.open(archive_path, "w:gz") as tar:
        for item in to_archive:
            try:
                tar.add(item.path, arcname=item.path.lstrip(os.sep))
            except OSError:
                pass  # okunamayan tek dosya yedeğin tamamını bozmasın
