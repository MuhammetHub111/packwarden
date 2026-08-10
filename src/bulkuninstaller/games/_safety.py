"""Oyun kaldırma işlemlerinin güvenlik ortak kodu.

Steam/GOG/Lutris'in "bu oyun nereye kuruldu" bilgisi, kullanıcının
kendi ev dizininde sıradan, korumasız — yani çalışan HERHANGİ BİR
yerel süreç tarafından değiştirilebilir — dosyalarda tutuluyor: Steam'in
.acf metin dosyası, Heroic/GOG'un JSON'ı, Lutris'in SQLite veritabanı.
Buradan okunan bir yolu doğrulamadan doğrudan shutil.rmtree()'ye vermek,
o dosyayı değiştirebilen herhangi bir şeyin (kırık bir oyun kurulumu,
kötü niyetli bir mod, vb.) "oyunu kaldır" tıklamasını "~/Belgeler'i sil"
işlemine çevirebilmesi demek — çünkü os.path.join, parçalardan biri
mutlak yol olunca öncekileri sessizce yok sayıyor.

Bu modül iki savunma katmanı sunar:
- is_within(path, root): silinecek yolun GERÇEKTEN beklenen kütüphane
  kökünün altında kaldığını doğrular (Steam için — kesin bir kök
  biliniyor: steamapps/common/).
- is_critical(path): GOG/Lutris gibi kesin bir kök bilinmeyen
  durumlarda, en azından ev dizininin kendisini ve Belgeler/İndirilenler
  gibi bilinen kritik kullanıcı klasörlerini reddeden bir kara liste.
"""

import os
import re

_CRITICAL_BASENAMES_FALLBACK = {
    "desktop", "documents", "downloads", "download", "music", "pictures",
    "videos", "templates", "public",
}

_CRITICAL_ABSOLUTE_ROOTS = (
    "/", "/home", "/root", "/etc", "/usr", "/var", "/boot", "/opt",
    "/bin", "/sbin", "/lib", "/lib64", "/proc", "/sys", "/dev",
)


def _xdg_user_dirs() -> set[str]:
    """~/.config/user-dirs.dirs içindeki gerçek (yerelleştirilmiş)
    kullanıcı klasörlerini okur — Masaüstü/Belgeler/İndirilenler gibi.
    Dosya yoksa ya da okunamıyorsa boş küme döner, çağıran zaten
    İngilizce isim tahminine de bakıyor."""
    path = os.path.expanduser("~/.config/user-dirs.dirs")
    result = set()
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return result
    home = os.path.expanduser("~")
    for match in re.finditer(r'XDG_\w+_DIR="([^"]*)"', text):
        raw = match.group(1).replace("$HOME", home)
        result.add(os.path.realpath(raw))
    return result


def is_within(path: str, root: str) -> bool:
    """path, gerçekten root'un bir alt klasörü mü (root'un kendisi
    hariç)? Sembolik bağ/`..` gibi numaralara kanmamak için ikisi de
    realpath ile çözülür."""
    if not path or not root:
        return False
    real_path = os.path.realpath(path)
    real_root = os.path.realpath(root)
    if real_path == real_root:
        return False
    return os.path.commonpath([real_path, real_root]) == real_root


def is_critical(path: str) -> bool:
    """path; ev dizininin kendisi, bilinen bir kullanıcı klasörü
    (Belgeler/İndirilenler/Masaüstü vb., yerelleştirilmiş adlarıyla)
    ya da bir sistem kökü mü? Öyleyse silinmemeli."""
    if not path:
        return True
    real = os.path.realpath(path)
    home = os.path.realpath(os.path.expanduser("~"))

    if real == home or real in _CRITICAL_ABSOLUTE_ROOTS:
        return True
    if real in _xdg_user_dirs():
        return True
    if os.path.dirname(real) == home:
        basename = os.path.basename(real).lower()
        if basename in _CRITICAL_BASENAMES_FALLBACK:
            return True
    return False
