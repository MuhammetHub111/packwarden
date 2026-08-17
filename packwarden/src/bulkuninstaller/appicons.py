"""Paket → uygulama simgesi eşlemesi.

Paket adı çoğu zaman simge adıyla aynı değildir (ör. gnome-calculator
paketi → org.gnome.Calculator simgesi). Doğru simgeyi bulmak için
.desktop dosyaları taranır:

1. Sezgisel eşleme: tüm .desktop dosyalarından dosya adı, Exec ve Name
   alanlarına göre bir dizin kurulur (her dağıtımda çalışır).
2. Kesin eşleme: pacman'de her paketin sahip olduğu .desktop dosyası
   bilinir (pacman -Ql); o dosyanın Icon değeri paketle birebir eşlenir
   ve sezgisel eşlemeyi ezer.
"""

import os

from . import host

APP_DIRS = (
    "/usr/share/applications",
    "/usr/local/share/applications",
    "~/.local/share/applications",
)

# Flatpak/Snap kendi .desktop dosyalarını yukarıdaki sıradan klasörlere
# değil, kendi dışa aktarma klasörlerine koyar (window.py'deki kısayol
# aramasıyla aynı yollar) — Kategori taraması bunları da içermeli, yoksa
# tüm Flatpak/Snap uygulamaları kategorisiz kalır.
_EXTRA_APP_DIRS = (
    "/var/lib/flatpak/exports/share/applications",
    "~/.local/share/flatpak/exports/share/applications",
    "/var/lib/snapd/desktop/applications",
)

_ICON_EXTENSIONS = (".png", ".svg", ".svgz", ".xpm")

# freedesktop.org menu spesifikasyonundaki "Main Categories" — bir
# .desktop dosyasının Categories alanı bunlardan birini (genelde ilk
# sırada) içerir, gerisi alt kategoridir (ör. Game;ActionGame;). Değerler
# İngilizce kanonik etiket — Türkçeye çeviri window.py'de i18n._() ile
# yapılır (buradaki diğer eşlemeler gibi bu modül dil ayarını bilmiyor).
_MAIN_CATEGORIES = {
    "AudioVideo": "Audio & Video",
    "Audio": "Audio",
    "Video": "Video",
    "Development": "Development",
    "Education": "Education",
    "Game": "Game",
    "Graphics": "Graphics",
    "Network": "Internet",
    "Office": "Office",
    "Science": "Science",
    "Settings": "Settings",
    "System": "System",
    "Utility": "Utility",
}


def _normalize_category(raw: str) -> str:
    for token in raw.split(";"):
        label = _MAIN_CATEGORIES.get(token.strip())
        if label:
            return label
    return ""


def _clean_icon(icon: str) -> str:
    icon = icon.strip()
    # "firefox.png" gibi uzantılı adlar tema aramasında bulunamaz
    if not os.path.isabs(icon):
        for ext in _ICON_EXTENSIONS:
            if icon.endswith(ext):
                return icon[: -len(ext)]
    return icon


def _parse_desktop(path: str):
    """(icon, exec_base, name, category) döner; okunamazsa None."""
    icon = exec_base = name = category = None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            in_entry = False
            for line in f:
                line = line.strip()
                if line.startswith("["):
                    if in_entry:
                        break  # [Desktop Entry] bölümü bitti
                    in_entry = line == "[Desktop Entry]"
                    continue
                if not in_entry:
                    continue
                if line.startswith("Icon=") and icon is None:
                    icon = _clean_icon(line[5:])
                elif line.startswith("Exec=") and exec_base is None:
                    tokens = line[5:].strip().split()
                    if tokens:
                        exec_base = os.path.basename(tokens[0]).lower()
                elif line.startswith("Name=") and name is None:
                    name = line[5:].strip().lower()
                elif line.startswith("Categories=") and category is None:
                    category = _normalize_category(line[len("Categories="):])
    except OSError:
        return None
    return icon, exec_base, name, category


def build_maps() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Üç eşleme döner (anahtarlar küçük harfli paket adı/kimliği):

    - simgeler: → simge adı (veya mutlak yol)
    - başlatıcılar: → .desktop kimliği (gtk-launch ile çalıştırmak için)
    - kategoriler: → Türkçeleştirilmiş ana kategori (Oyun, Ofis, ...)
    """
    icons: dict[str, str] = {}
    launchers: dict[str, str] = {}
    categories: dict[str, str] = {}

    # 1) Sezgisel dizin: dosya adı, Exec ve Name anahtarları — Flatpak/Snap'ın
    # kendi dışa aktarma klasörleri de dahil, yoksa o uygulamalar
    # kategorisiz kalır.
    for base in APP_DIRS + _EXTRA_APP_DIRS:
        base_dir = os.path.expanduser(base)
        try:
            entries = os.listdir(base_dir)
        except OSError:
            continue
        for entry in entries:
            if not entry.endswith(".desktop"):
                continue
            parsed = _parse_desktop(os.path.join(base_dir, entry))
            if not parsed:
                continue
            icon, exec_base, name, category = parsed
            desktop_id = entry[: -len(".desktop")]
            for key in (desktop_id.lower(), exec_base, name):
                if not key:
                    continue
                if icon:
                    icons.setdefault(key, icon)
                    launchers.setdefault(key, desktop_id)
                if category:
                    categories.setdefault(key, category)

    # 2) pacman kesin eşlemesi: paket → sahibi olduğu .desktop dosyası
    try:
        proc = host.run(
            [
                "sh", "-c",
                "pacman -Ql 2>/dev/null | "
                "grep -E '/usr/share/applications/[^/]+[.]desktop$'",
            ],
            timeout=120,
        )
        if proc.returncode == 0:
            seen: set[str] = set()
            for line in proc.stdout.splitlines():
                pkg, _, path = line.partition(" ")
                pkg = pkg.lower()
                path = path.strip()
                if not pkg or not path or pkg in seen:
                    continue
                parsed = _parse_desktop(path)
                if parsed:
                    icon, _exec_base, _name, category = parsed
                    seen.add(pkg)
                    if icon:
                        icons[pkg] = icon
                        launchers[pkg] = os.path.basename(path)[: -len(".desktop")]
                    if category:
                        categories[pkg] = category
    except Exception:
        pass  # simgeler süs; hata listeyi engellememeli

    return icons, launchers, categories
