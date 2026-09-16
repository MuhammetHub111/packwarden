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
import shutil
from dataclasses import dataclass

from . import host, prefs
from .backends.base import Package

SCAN_BASES = (
    "~/.config",
    "~/.cache",
    "~/.local/share",
    "~/.local/state",
)

FLATPAK_DATA_BASE = "~/.var/app"

# Bazı uygulamalar ilk çalıştırmada, kendi adını taşımayan, standart
# tarama konumlarının (SCAN_BASES) dışında büyük bir ek SDK/araç seti
# indiriyor — genel ad-eşleştirmeli tarama bunu hiç yakalayamaz (ör.
# "~/Android" klasör adı normalize edilince "Android Studio" ile
# eşleşmiyor). Doğrulandı: Android Studio'nun oluşturduğu ~/Android/Sdk
# (1.4GB) ve ~/.android sadece SDK/araç verisi, gerçek proje kaynak
# kodu içermiyor — yine de varsayılan olarak İŞARETLİ gelmiyor, çünkü
# SDK bağımsız olarak (Android Studio olmadan, komut satırından) da
# kullanılabiliyor.
_KNOWN_EXTRA_DATA = {
    "com.google.AndroidStudio": (
        ("~/Android", "Android SDK"),
        ("~/.android", "Android SDK"),
    ),
}

# Kategori anahtarları i18n sözlüğünden çevrilir
LEFTOVER_CATEGORIES = (
    ("~/.config", "Settings"),
    ("~/.cache", "Cache"),
    ("~/.local/share", "App data"),
    ("~/.local/state", "State logs"),
)

# Wine ile kurulmuş bir program kaldırılırken, onu kurmak için indirilmiş
# .exe dosyasını da (varsa) İndirilenler/Masaüstü'nde arar — bu dosya hiçbir
# paket yöneticisine kayıtlı değildir, PackWarden'ın kendi kurulum dosyası
# temizliğinin dışında kalır.
_INSTALLER_SCAN_DIRS = ("~/Downloads", "~/İndirilenler", "~/Desktop", "~/Masaüstü")
_VERSION_SUFFIX_RE = re.compile(r"\s+[vV]?[\d][\d.]*.*$")
_TRAILING_PAREN_RE = re.compile(r"\s*\([^()]*\)\s*$")


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


def _installer_stem(name: str) -> str:
    """Program görünen adının sonundaki mimari eki ve sürüm numarasını
    atıp normalize eder (ör. 'SteelSeries GG 115.0.0' -> 'steelseriesgg').

    Sondaki '(x64)'/'(x86)' gibi parantezli ekler de atılıyor - canlı
    doğrulandı: 'foobar2000 v2.25.10 (x64)' bu ek yuzunden kurulum
    dosyasindaki 'foobar2000-x64_v2.25.10.exe' ile hiç eşleşmiyordu
    (mimari, dosya adında FARKLI bir sırada geçiyor - parantezli hali
    de dosya adında hiç geçmiyor). Bu ek atılınca geriye kalan çekirdek
    ad (yalnızca 'foobar2000') yine de güvenli bir alt dize eşleşmesi
    sağlıyor."""
    while True:
        stripped = _TRAILING_PAREN_RE.sub("", name)
        if stripped == name:
            break
        name = stripped
    return _normalize(_VERSION_SUFFIX_RE.sub("", name))


def _find_installer_leftovers(packages: list[Package], safe) -> list[Leftover]:
    """Kaldırılan Wine programlarının kurulum dosyasını (varsa) bulur.

    Burada TAM eşleşme aranmıyor — bir kurulum dosyasının adı ("Steel
    SeriesGG115.0.0Setup.exe") hiçbir zaman programın görünen adıyla
    ("SteelSeries GG 115.0.0") birebir aynı olmaz, o yüzden normalize
    edilmiş program adı, normalize edilmiş dosya adının İÇİNDE aranıyor
    (alt dize eşleşmesi). Yanlış pozitif riski üç şekilde sınırlanıyor:
    yalnızca .exe uzantılı dosyalara bakılıyor, yalnızca bilinen birkaç
    klasörde (İndirilenler/Masaüstü) aranıyor, ve çok kısa (<3 karakter)
    kök adları hiç eşleştirilmiyor."""
    stems = {
        stem for pkg in packages if pkg.source == "wine"
        for stem in [_installer_stem(pkg.name)]
        if len(stem) >= 3
    }
    if not stems:
        return []
    items = []
    for base in _INSTALLER_SCAN_DIRS:
        base_dir = os.path.expanduser(base)
        try:
            entries = os.listdir(base_dir)
        except OSError:
            continue
        for entry in entries:
            if not entry.lower().endswith(".exe"):
                continue
            normalized_entry = _normalize(entry[:-4])
            if not any(stem in normalized_entry for stem in stems):
                continue
            path = os.path.join(base_dir, entry)
            if safe(path):
                items.append(Leftover(path=path, size=_tree_size(path)))
    return items


_DESKTOP_SHORTCUT_DIRS = ("~/Desktop", "~/Masaüstü")


def _find_wine_shortcut_leftovers(packages: list[Package], safe) -> list[Leftover]:
    """Kaldırılan Wine programlarının masaüstündeki (varsa) kısayolunu
    bulur — kendi kurulumcusu (Wine'ın winemenubuilder'ı) ya da
    PackWarden'ın "Masaüstü kısayolu oluştur" özelliği tarafından
    yaratılmış olabilir; PackWarden şu ana kadar hiçbirini kaldırma
    sırasında temizlemiyordu (canlı doğrulandı: kaldırılmış bir Wine
    programının .desktop dosyası masaüstünde öylece kalıyor, çift
    tıklayınca hiçbir tepki vermiyor).

    YOL DEĞİL, .exe DOSYA ADI eşleştiriliyor: gerçek bir kısayolu
    (Total Commander) inceleyince Exec= satırının çözümlenmiş Unix
    yolunu DEĞİL, orijinal Windows yolunu ("C:\\\\Program
    Files\\\\totalcmd\\\\totalcmd64.exe") içerdiği görüldü — winemenu-
    builder'ın kendi ürettiği kısayollar hep böyle. O yüzden tam yol
    (bkz. backends/wine.py: resolved_exe_path) yerine sadece dosya adı
    (bkz. exec_basename, "totalcmd64.exe") aranıyor - format ne olursa
    olsun (Windows'lu ya da Unix'li yol) aynı kalan tek parça bu.

    AYRICA kurulum klasörünün adı da (ör. "totalcmd") aranıyor: TEK bir
    kayıt defteri girdisi (DisplayIcon) birden fazla kısayol (32/64 bit
    gibi kardeş .exe'ler) üretebilir, ama kayıt SADECE kendi DisplayIcon
    .exe'sini bilir — gerçek Total Commander örneğinde bu, iki masaüstü
    kısayolundan sadece "64 bit" olanını buluyordu, "totalcmd.exe"
    (32-bit) kısayolu kaçıyordu. Klasör adı ikisini de kapsıyor.

    Yanlış pozitif riski, aramayı SADECE "Exec=" satırıyla ve zaten dar
    bir alanla (masaüstü, .desktop uzantısı) sınırlayarak azaltılıyor."""
    from .backends.wine import exec_basename, resolved_exe_path

    needles = set()
    for pkg in packages:
        if pkg.source != "wine":
            continue
        basename = exec_basename(pkg.id)
        if basename:
            needles.add(basename)
        full_path = resolved_exe_path(pkg.id)
        if full_path:
            install_dir_name = os.path.basename(os.path.dirname(full_path))
            if len(install_dir_name) >= 3:
                needles.add(install_dir_name.lower())
    if not needles:
        return []

    items = []
    for base in _DESKTOP_SHORTCUT_DIRS:
        base_dir = os.path.expanduser(base)
        try:
            entries = os.listdir(base_dir)
        except OSError:
            continue
        for entry in entries:
            if not entry.lower().endswith(".desktop"):
                continue
            path = os.path.join(base_dir, entry)
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    exec_lines = [
                        line for line in f if line.startswith("Exec=")
                    ]
            except OSError:
                continue
            exec_text = "\n".join(exec_lines).lower()
            if any(needle in exec_text for needle in needles) and safe(path):
                items.append(Leftover(path=path, size=_tree_size(path)))
    return items


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

    extra_by_category: dict[str, list[Leftover]] = {}
    for pkg in packages:
        for extra_path, category in _KNOWN_EXTRA_DATA.get(pkg.id, ()):
            path = os.path.expanduser(extra_path)
            if os.path.lexists(path) and safe(path):
                extra_by_category.setdefault(category, []).append(
                    Leftover(path=path, size=_tree_size(path))
                )
    for category, extra_items in extra_by_category.items():
        extra_items.sort(key=lambda item: -item.size)
        result.append((category, extra_items))

    installer_items = _find_installer_leftovers(packages, safe)
    if installer_items:
        installer_items.sort(key=lambda item: -item.size)
        result.append(("Installer file", installer_items))

    shortcut_items = _find_wine_shortcut_leftovers(packages, safe)
    if shortcut_items:
        shortcut_items.sort(key=lambda item: -item.size)
        result.append(("Desktop shortcut", shortcut_items))

    return result


def find_leftovers(packages: list[Package]) -> list[Leftover]:
    """find_package_leftovers'ın düz liste dönen hâli."""
    found: list[Leftover] = []
    for _category, items in find_package_leftovers(packages):
        found.extend(items)
    found.sort(key=lambda item: -item.size)
    return found


def remove_leftovers(leftovers: list[Leftover]) -> list[str]:
    """Verilen kalıntıları siler; silinemeyenler için hata listesi döner.

    Ayarlar → Güvenlik → Kalıntı silme yöntemi tercihine göre ya çöp
    kutusuna taşır ya da kalıcı olarak siler.
    """
    errors = []
    home = os.path.realpath(os.path.expanduser("~"))
    protected = {os.path.realpath(os.path.expanduser(b)) for b in SCAN_BASES}
    protected.add(os.path.realpath(os.path.expanduser(FLATPAK_DATA_BASE)))
    protected.add(home)
    permanent = prefs.get("leftover_delete_mode") == "permanent"

    for item in leftovers:
        real = os.path.realpath(item.path)
        if real in protected or not real.startswith(home + os.sep):
            errors.append(f"{item.path}: güvenlik nedeniyle atlandı")
            continue
        try:
            if permanent:
                if os.path.isdir(item.path) and not os.path.islink(item.path):
                    shutil.rmtree(item.path)
                else:
                    os.remove(item.path)
            else:
                # Çöp kutusuna taşınır (gio trash, XDG Trash spesifikasyonuna
                # uyar) — yanlış tiklenen bir kalıntı geri alınabilsin diye.
                # Paket kaldırmadaki "yedekle" seçeneğiyle aynı mantık:
                # geri dönüşü olmayan işlemden kaçınmak.
                proc = host.run(["gio", "trash", item.path], timeout=30)
                if proc.returncode != 0:
                    errors.append(f"{item.path}: {(proc.stderr or proc.stdout).strip()}")
        except Exception as exc:
            errors.append(f"{item.path}: {exc}")
    return errors
