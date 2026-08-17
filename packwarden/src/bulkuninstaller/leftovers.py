"""Kaldırılan paketlerin geride bıraktığı dosyaları bulma ve temizleme.

Güvenlik kuralları:
- Eşleşme TAM klasör/dosya adı üzerinden yapılır (parça/alt dize eşleşmesi
  yok); yanlış pozitif riskini en aza indirir. Tek esneklik, boşluk/tire/
  alt çizgi gibi ayraçların yok sayılması — "Stardew Valley" paketi
  "StardewValley" adlı bir yapılandırma klasörünü tanıyabilsin diye
  (doğrulandı: bu normalleştirme olmadan bu klasör hiç bulunamıyordu).
- Silinecek her yol ev dizininin altında olmak zorundadır ve tarama
  yapılan kök dizinlerin kendisi asla silinmez.
"""

import os
import re
from dataclasses import dataclass

from . import host
from .backends.base import Package

SCAN_BASES = (
    "~/.config",
    "~/.cache",
    "~/.local/share",
    "~/.local/state",
)

FLATPAK_DATA_BASE = "~/.var/app"

# Kategori anahtarları i18n sözlüğünden çevrilir
LEFTOVER_CATEGORIES = (
    ("~/.config", "Settings"),
    ("~/.cache", "Cache"),
    ("~/.local/share", "App data"),
    ("~/.local/state", "State logs"),
)


@dataclass
class Leftover:
    path: str
    size: int


def _normalize(text: str) -> str:
    """Boşluk/tire/alt çizgi/nokta farklarını yok sayar — hâlâ TAM
    eşleşme, alt dize değil, o yüzden yanlış pozitif riski artmıyor."""
    return re.sub(r"[\s_\-.]+", "", text.lower())


def _tree_size(path: str) -> int:
    if not os.path.isdir(path) or os.path.islink(path):
        try:
            return os.lstat(path).st_size
        except OSError:
            return 0
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda _e: None):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def find_package_leftovers(
    packages: list[Package],
) -> list[tuple[str, list[Leftover]]]:
    """Verilen paketlere ait kalıntıları kategorilere ayırarak bulur.

    Dönen liste: (kategori anahtarı, kalıntılar) çiftleri; kategoriler
    i18n sözlüğünden çevrilir, kalıntılar boyuta göre sıralıdır.
    """
    names = set()
    flatpak_ids = set()
    for pkg in packages:
        if pkg.source == "flatpak":
            flatpak_ids.add(pkg.id)
            names.add(_normalize(pkg.name))
        else:
            names.add(_normalize(pkg.id))
            names.add(_normalize(pkg.name))

    home = os.path.realpath(os.path.expanduser("~"))
    seen: set[str] = set()

    def safe(path: str) -> bool:
        real = os.path.realpath(path)
        # Sadece ev dizini altındaki yollar; aynı yol iki kez önerilmez
        if not real.startswith(home + os.sep) or real in seen:
            return False
        seen.add(real)
        return True

    result: list[tuple[str, list[Leftover]]] = []
    for base, category in LEFTOVER_CATEGORIES:
        base_dir = os.path.expanduser(base)
        try:
            entries = os.listdir(base_dir)
        except OSError:
            continue
        items = []
        for entry in entries:
            path = os.path.join(base_dir, entry)
            if _normalize(entry) in names and safe(path):
                items.append(Leftover(path=path, size=_tree_size(path)))
        if items:
            items.sort(key=lambda item: -item.size)
            result.append((category, items))

    flatpak_base = os.path.expanduser(FLATPAK_DATA_BASE)
    items = []
    for app_id in flatpak_ids:
        path = os.path.join(flatpak_base, app_id)
        if os.path.lexists(path) and safe(path):
            items.append(Leftover(path=path, size=_tree_size(path)))
    if items:
        items.sort(key=lambda item: -item.size)
        result.append(("Flatpak data", items))

    return result


def find_leftovers(packages: list[Package]) -> list[Leftover]:
    """find_package_leftovers'ın düz liste dönen hâli."""
    found: list[Leftover] = []
    for _category, items in find_package_leftovers(packages):
        found.extend(items)
    found.sort(key=lambda item: -item.size)
    return found


def remove_leftovers(leftovers: list[Leftover]) -> list[str]:
    """Verilen kalıntıları siler; silinemeyenler için hata listesi döner."""
    errors = []
    home = os.path.realpath(os.path.expanduser("~"))
    protected = {os.path.realpath(os.path.expanduser(b)) for b in SCAN_BASES}
    protected.add(os.path.realpath(os.path.expanduser(FLATPAK_DATA_BASE)))
    protected.add(home)

    for item in leftovers:
        real = os.path.realpath(item.path)
        if real in protected or not real.startswith(home + os.sep):
            errors.append(f"{item.path}: güvenlik nedeniyle atlandı")
            continue
        try:
            # Kalıcı silme yerine çöp kutusuna taşınır (gio trash, XDG Trash
            # spesifikasyonuna uyar) — yanlış tiklenen bir kalıntı geri
            # alınabilsin diye. Paket kaldırmadaki "yedekle" seçeneğiyle
            # aynı mantık: geri dönüşü olmayan işlemden kaçınmak.
            proc = host.run(["gio", "trash", item.path], timeout=30)
            if proc.returncode != 0:
                errors.append(f"{item.path}: {(proc.stderr or proc.stdout).strip()}")
        except Exception as exc:
            errors.append(f"{item.path}: {exc}")
    return errors
