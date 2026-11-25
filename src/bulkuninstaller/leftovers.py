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

# Snap, her paketin kullanıcı verisini (SNAP_USER_DATA/SNAP_USER_COMMON)
# SCAN_BASES'in hiçbirinin altında değil, ~/snap/<ad>/ altında tutar -
# "snap remove --purge" (bkz. backends/snap.py remove_argv) snapd'nin
# KENDİ otomatik yedek anlık görüntüsünü bilerek devre dışı bırakıyor;
# bu veri buraya eklenmezse PackWarden'ın "Yedekle ve Kaldır" seçeneği
# de bu veriyi hiç görmez - iki güvenlik ağı da devre dışı kalırdı.
SNAP_DATA_BASE = "~/snap"

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
    # "file" (varsayılan) ya da "registry" - Wine paketinin ana
    # kurulum/kaldırma kaydı bir dosya değil, registry_prefix +
    # registry_key doldurulduğunda "wine reg delete" ile silinir.
    kind: str = "file"
    registry_prefix: str | None = None
    registry_key: str | None = None


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


def _installer_stem_candidates(name: str) -> list[str]:
    """Program görünen adının sonundaki mimari eki ve sürüm numarasını
    atıp normalize eder, sonra sondan kelime kelime kısaltarak sırayla
    en spesifikten en genele aday kökler üretir (ör. 'SteelSeries GG
    115.0.0' -> ['steelseriesgg', 'steelseries']).

    Sondaki '(x64)'/'(x86)' gibi parantezli ekler de atılıyor - canlı
    doğrulandı: 'foobar2000 v2.25.10 (x64)' bu ek yuzunden kurulum
    dosyasindaki 'foobar2000-x64_v2.25.10.exe' ile hiç eşleşmiyordu
    (mimari, dosya adında FARKLI bir sırada geçiyor - parantezli hali
    de dosya adında hiç geçmiyor).

    Kelime kelime kısaltma, BAŞKA bir gerçek örnek için: KeePass'ın
    kayıt defteri adı 'KeePass Password Safe 2.61.1', ama gerçek
    kurulum dosyası 'KeePass-2.61.1-Setup.exe' - 'Password Safe' hiç
    geçmiyor. Tam ad eşleşmeyince sondaki kelimeler tek tek atılıp
    yeniden denenir ('KeePass Password' -> 'KeePass'), en az 3 karaktere
    kadar - kısa/genel kelimelerde (örn. 'Total') yanlış pozitif riski
    biraz artar ama aranan yer zaten dar (İndirilenler/Masaüstü, sadece
    .exe, sadece kaldırılan paketle ilgili aramada)."""
    while True:
        stripped = _TRAILING_PAREN_RE.sub("", name)
        if stripped == name:
            break
        name = stripped
    core = _VERSION_SUFFIX_RE.sub("", name).strip()
    words = core.split()
    candidates = []
    for count in range(len(words), 0, -1):
        candidate = _normalize(" ".join(words[:count]))
        if len(candidate) >= 3:
            candidates.append(candidate)
    return candidates


def _find_installer_leftovers(packages: list[Package], safe) -> list[Leftover]:
    """Kaldırılan Wine programlarının kurulum dosyasını (varsa) bulur.

    Burada TAM eşleşme aranmıyor — bir kurulum dosyasının adı ("Steel
    SeriesGG115.0.0Setup.exe") hiçbir zaman programın görünen adıyla
    ("SteelSeries GG 115.0.0") birebir aynı olmaz, o yüzden normalize
    edilmiş program adı, normalize edilmiş dosya adının İÇİNDE aranıyor
    (alt dize eşleşmesi). Yanlış pozitif riski üç şekilde sınırlanıyor:
    yalnızca .exe uzantılı dosyalara bakılıyor, yalnızca bilinen birkaç
    klasörde (İndirilenler/Masaüstü) aranıyor, ve çok kısa (<3 karakter)
    kök adları hiç eşleştirilmiyor.

    Kelime kelime kısaltma (bkz. _installer_stem_candidates) bir aday
    daha da azaltıyor: sonuç, paketin kendi Yayıncı adının bir öneki
    olursa (ör. "Microsoft Visual C++ ... Redistributable"ın kısalmış
    hali "microsoft" - yayıncı da "Microsoft Corporation") o aday
    atılıyor. Canlı doğrulandı: aksi hâlde İndirilenler'deki ALAKASIZ
    herhangi bir "Microsoft*.exe" dosyası bu paketin kalıntısı sanılırdı
    - marka kelimesi yalnızca yayıncının kendi adına indirgendiğinde çok
    genel, gerçek bir ürün eşleştirmesi olmuyor."""
    stems = set()
    for pkg in packages:
        if pkg.source != "wine":
            continue
        publisher_norm = _normalize(pkg.publisher) if pkg.publisher else ""
        for stem in _installer_stem_candidates(pkg.name):
            if publisher_norm and publisher_norm.startswith(stem):
                continue
            stems.add(stem)
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

# winemenubuilder, HER Wine programı için burada (Start Menu'sünün
# birebir aynısı) bir .desktop dosyası yazar - KDE Plasma/GNOME'un
# "Uygulamalar" menüsünde/arama panelinde (Super tuşu, KRunner vb.)
# görünen asıl kaynak burası, masaüstü kısayolu (varsa) ayrıca ekstra.
# Alt klasörler (ör. "Total Commander/", "SteelSeries/SteelSeries GG/")
# programın kendi Start Menu grubunu birebir yansıtır - bu yüzden tarama
# tek düzey DEĞİL, os.walk ile TÜM derinlikte yapılıyor.
_WINE_MENU_DIR = "~/.local/share/applications/wine/Programs"


def _find_wine_shortcut_leftovers(packages: list[Package], safe) -> list[Leftover]:
    """Kaldırılan Wine programlarının masaüstündeki VE uygulama
    menüsündeki (varsa) kısayollarını bulur — kendi kurulumcusu
    (Wine'ın winemenubuilder'ı) ya da PackWarden'ın "Masaüstü kısayolu
    oluştur" özelliği tarafından yaratılmış olabilir.

    Canlı doğrulandı (gerçek sistemde, Wine registry'sinden çoktan
    kaldırılmış bir foobar2000 kurulumuyla): WineBackend artık bu
    paketi hiç listelemiyor olsa da, ~/.local/share/applications/wine/
    Programs/foobar2000.desktop hâlâ diskte duruyordu VE KDE'nin gerçek
    uygulama arama panelinde (KRunner - Super tuşuyla açılan menünün
    aynı mekanizması) hâlâ "foobar2000 - Play, organize and tag your
    music" olarak görünüp tıklanabiliyordu. PackWarden bu klasörü HİÇ
    taramıyordu - yalnızca masaüstü kısayolları kapsanıyordu.

    Masaüstü kısayolları için: YOL DEĞİL, .exe DOSYA ADI eşleştiriliyor
    - gerçek bir kısayolu (Total Commander) inceleyince Exec= satırının
    çözümlenmiş Unix yolunu DEĞİL, orijinal Windows yolunu ("C:\\\\
    Program Files\\\\totalcmd\\\\totalcmd64.exe") içerdiği görüldü. O
    yüzden tam yol (bkz. backends/wine.py: resolved_exe_path) yerine
    sadece dosya adı (bkz. exec_basename, "totalcmd64.exe") aranıyor.

    Uygulama MENÜSÜ kısayolları için bu YETMİYOR: canlı doğrulandı,
    gerçek winemenubuilder çıktısı Exec='de ne exe adını ne kurulum
    klasörünü taşıyor - SADECE bir Windows .lnk YOLUNU taşıyor (ör.
    'C:\\\\...\\\\Start Menu\\\\Programs\\\\Total Commander\\\\Total
    Commander 64 bit.lnk'). Gerçek Total Commander kurulumunda menü
    klasöründeki DÖRT .desktop dosyasının HİÇBİRİNİN Exec= satırında
    "totalcmd" ya da "totalcmd64.exe" geçmiyor - yukarıdaki .exe/kurulum-
    klasörü eşleştirmesi bu dosyaları YAKALAYAMAZ. Bunun yerine, Wine'ın
    bu dosyaları koyduğu GRUP KLASÖRÜNÜN adı ("Total Commander" - bu da
    tam olarak diskteki applications/wine/Programs/ altındaki alt klasör
    adı) paketin DisplayName'iyle (_normalize ile TAM eşleşme, alt dize
    değil - yanlış pozitif riski artmasın diye) karşılaştırılıyor; grup
    klasörü olmayan üst düzey dosyalarda (foobar2000.desktop gibi) aynı
    TAM eşleşme bu kez dosyanın kendi "Name=" alanına uygulanıyor.

    Her iki yöntem de (Exec= tabanlı VE grup/isim tabanlı) birbirinin
    ALTERNATİFİ olarak denenir - biri eşleşirse yeterli. TEK bir kayıt
    defteri girdisi (DisplayIcon) birden fazla kısayol (32/64 bit gibi
    kardeş .exe'ler, "Yardım", "Kaldır" gibi ek girdiler) üretebilir,
    ama kayıt SADECE kendi DisplayIcon .exe'sini bilir - grup klasörü
    eşleşmesi gerçek Total Commander örneğindeki DÖRDÜNÜ DE kapsıyor."""
    from .backends.wine import exec_basename, resolved_exe_path

    exec_needles = set()
    name_needles = set()
    for pkg in packages:
        if pkg.source != "wine":
            continue
        basename = exec_basename(pkg.id)
        if basename:
            exec_needles.add(basename)
        full_path = resolved_exe_path(pkg.id)
        if full_path:
            install_dir_name = os.path.basename(os.path.dirname(full_path))
            if len(install_dir_name) >= 3:
                exec_needles.add(install_dir_name.lower())
        if pkg.name:
            name_needles.add(_normalize(pkg.name))
    if not exec_needles and not name_needles:
        return []

    def _exec_matches(path: str) -> bool:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                exec_lines = [line for line in f if line.startswith("Exec=")]
        except OSError:
            return False
        exec_text = "\n".join(exec_lines).lower()
        return any(needle in exec_text for needle in exec_needles)

    def _desktop_entry_name(path: str) -> str | None:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.startswith("Name="):
                        return line[len("Name="):].strip()
        except OSError:
            return None
        return None

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
            if _exec_matches(path) and safe(path):
                items.append(Leftover(path=path, size=_tree_size(path)))

    menu_dir = os.path.expanduser(_WINE_MENU_DIR)
    for root, _dirs, files in os.walk(menu_dir):
        group_name = os.path.basename(root) if root != menu_dir else None
        group_matches = bool(
            group_name and _normalize(group_name) in name_needles
        )
        for entry in files:
            if not entry.lower().endswith(".desktop"):
                continue
            path = os.path.join(root, entry)
            matched = group_matches or _exec_matches(path)
            if not matched and group_name is None:
                desktop_name = _desktop_entry_name(path)
                matched = bool(
                    desktop_name and _normalize(desktop_name) in name_needles
                )
            if matched and safe(path):
                items.append(Leftover(path=path, size=_tree_size(path)))
    return items


def _find_wine_icon_cache_leftovers(packages: list[Package], safe) -> list[Leftover]:
    """Kaldırılan Wine programlarının çıkarılmış .exe ikonunun
    önbellek PNG'sini (varsa) bulur - bkz. backends/wine.py:
    icon_cache_path. Canlı doğrulandı: SteelSeries GG, Totalcmd64,
    foobar2000_x64 gibi registry'den çoktan kaldırılmış paketlerin
    ikon PNG'leri ~/.cache/bulkuninstaller/wine-icons/ altında hâlâ
    duruyordu - hiçbir şey bunları silmiyordu. Dosya adı doğrudan
    pkg.id'den (prefix+registry anahtarı) hesaplandığı için yanlış
    pozitif riski yok - başka bir paketin ikonuyla asla çakışmaz."""
    from .backends.wine import icon_cache_path

    items = []
    for pkg in packages:
        if pkg.source != "wine":
            continue
        path = icon_cache_path(pkg.id)
        if path and safe(path):
            items.append(Leftover(path=path, size=_tree_size(path)))
    return items


def _is_safe_snap_data_dir(snap_base_real: str, path: str) -> bool:
    """`path`'in GERÇEK hâli, ~/snap'in GERÇEKTEN bir alt klasörü mü?

    Yalnızca `safe()`'in ev-dizini kontrolüne güvenilmez - o yalnızca
    "ev dizini altında mı" diye bakar, "~/snap altında mı" diye değil;
    paket adı (kullanıcı girdisi gibi ele alınıyor, gerçekte snap
    adları yalnızca küçük harf/rakam/tire içerebilse de) teorik olarak
    ".." içerseydi sonuç hâlâ ev dizini altında ama ~/snap DIŞINDA bir
    yere (ör. ~/baska_klasor) çıkabilirdi. ~/snap'in KENDİSİ de
    reddedilir - tüm snap'lerin ortak kökü, tek bir paketin verisi
    gibi gösterilmemeli/silinmemeli."""
    if not path:
        return False
    real_path = os.path.realpath(path)
    if real_path == snap_base_real:
        return False
    return real_path.startswith(snap_base_real + os.sep)


def _find_wine_registry_leftovers(packages: list[Package]) -> list[Leftover]:
    """Kaldırılacak Wine paketlerinin ana registry (kayıt defteri)
    girdisini de kalıntı listesine ekler.

    WineBackend.remove() bu kaydı zaten HER durumda kendisi garanti
    altına alıyor (kaldırıcı çökse bile - bkz. _force_remove); bu satır
    farklı bir amaç için var: kullanıcının kaldırma onayı ekranında
    "registry kaydı da silinecek" diye AÇIKÇA görüp isteğe bağlı olarak
    işaretini kaldırabilmesi. İşareti kaldırılsa da WineBackend.remove()
    kaydı yine kendi başına siler - bu satır güvenlik ağı DEĞİL, sadece
    şeffaflık için."""
    from .backends.wine import _find_entry

    items = []
    for pkg in packages:
        if pkg.source != "wine":
            continue
        prefix, sep, key = pkg.id.partition("::")
        if not sep:
            continue
        entry = _find_entry(prefix, key)
        reg_path = entry.get("_reg_path") if entry else None
        if not reg_path:
            continue
        items.append(Leftover(
            path=f"Windows Registry/{pkg.name}",
            size=0,
            kind="registry",
            registry_prefix=prefix,
            registry_key=f"HKLM\\{reg_path}",
        ))
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
    snap_ids = set()
    for pkg in packages:
        if pkg.source == "flatpak":
            flatpak_ids.add(pkg.id)
            names.add(_normalize(pkg.name))
        elif pkg.source == "packwarden":
            # SelfInstallBackend'in kendisi - bu genel ad-eşleştirmeli
            # taramaya hiç katılmaz. pkg.name "PackWarden" normalize
            # edilince "packwarden" olur; ~/.local/share altında (zaten
            # SCAN_BASES'te taranan bir kök) aynen bu adı taşıyan klasör
            # uygulamanın KENDİ TÜM KAYNAK KODU - kaldırma betiğinin
            # (install.sh remove) zaten koşulsuz sildiği bu klasörü ayrı
            # bir "kalıntı" gibi göstermek yanıltıcı olurdu (işareti
            # kaldırmak hiçbir şeyi kurtarmaz) ve betik onu sildikten
            # sonra burada tekrar silinmeye çalışılıp gereksiz bir hata
            # üretebilirdi.
            pass
        else:
            if pkg.source == "snap":
                snap_ids.add(pkg.id)
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

    snap_base_real = os.path.realpath(os.path.expanduser(SNAP_DATA_BASE))
    items = []
    for snap_id in snap_ids:
        path = os.path.join(snap_base_real, snap_id)
        if (
            os.path.lexists(path)
            and _is_safe_snap_data_dir(snap_base_real, path)
            and safe(path)
        ):
            items.append(Leftover(path=path, size=_tree_size(path)))
    if items:
        items.sort(key=lambda item: -item.size)
        result.append(("Snap data", items))

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
        result.append(("Shortcut", shortcut_items))

    icon_items = _find_wine_icon_cache_leftovers(packages, safe)
    if icon_items:
        result.append(("Icon cache", icon_items))

    registry_items = _find_wine_registry_leftovers(packages)
    if registry_items:
        result.append(("Registry entry", registry_items))

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
    protected.add(os.path.realpath(os.path.expanduser(SNAP_DATA_BASE)))
    protected.add(home)
    permanent = prefs.get("leftover_delete_mode") == "permanent"

    for item in leftovers:
        if item.kind == "registry":
            # Gerçek bir dosya yolu değil - ev dizini güvenlik kontrolü
            # ve çöp kutusu kavramı burada uygulanmıyor, doğrudan
            # wineserver üzerinden siliniyor (bkz. backends/wine.py
            # _force_remove'daki aynı yaklaşım). WINEDLLOVERRIDES,
            # WineDbg'nin "Program Hatası" penceresini açmasını önler -
            # o pencere biri "Kapat"a basana kadar süreci bekletiyordu.
            env_argv = [
                "env", f"WINEPREFIX={item.registry_prefix}",
                "WINEDLLOVERRIDES=winedbg.exe=d", "wine",
            ]
            try:
                host.run(
                    env_argv + ["reg", "delete", item.registry_key, "/f"],
                    timeout=30,
                )
            except Exception:
                pass
            # "wine reg delete"in kendi çıkış kodu güvenilir değil - ana
            # paket kaldırması bu AYNI anahtarı kendi garantisiyle çoktan
            # silmiş olabilir (bkz. WineBackend._force_remove); o durumda
            # burada "anahtar bulunamadı" diye nonzero dönmesi normaldir
            # ve zaten istenen sonuçtur. Asıl soru: silme denemesinden
            # SONRA anahtar hâlâ registry'de mi.
            try:
                verify = host.run(
                    env_argv + ["reg", "query", item.registry_key], timeout=30
                )
                still_there = verify.returncode == 0
            except Exception:
                still_there = True
            if still_there:
                errors.append(f"{item.path}: kayıt hâlâ registry'de duruyor")
            continue
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
