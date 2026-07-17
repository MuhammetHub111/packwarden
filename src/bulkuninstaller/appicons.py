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

_ICON_EXTENSIONS = (".png", ".svg", ".svgz", ".xpm")


def _clean_icon(icon: str) -> str:
    icon = icon.strip()
    # "firefox.png" gibi uzantılı adlar tema aramasında bulunamaz
    if not os.path.isabs(icon):
        for ext in _ICON_EXTENSIONS:
            if icon.endswith(ext):
                return icon[: -len(ext)]
    return icon


def _parse_desktop(path: str):
    """(icon, exec_base, name) döner; okunamazsa None."""
    icon = exec_base = name = None
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
    except OSError:
        return None
    return icon, exec_base, name


def build_maps() -> tuple[dict[str, str], dict[str, str]]:
    """İki eşleme döner (anahtarlar küçük harfli paket adı/kimliği):

    - simgeler: → simge adı (veya mutlak yol)
    - başlatıcılar: → .desktop kimliği (gtk-launch ile çalıştırmak için)
    """
    icons: dict[str, str] = {}
    launchers: dict[str, str] = {}

    # 1) Sezgisel dizin: dosya adı, Exec ve Name anahtarları
    for base in APP_DIRS:
        base_dir = os.path.expanduser(base)
        try:
            entries = os.listdir(base_dir)
        except OSError:
            continue
        for entry in entries:
            if not entry.endswith(".desktop"):
                continue
            parsed = _parse_desktop(os.path.join(base_dir, entry))
            if not parsed or not parsed[0]:
                continue
            icon, exec_base, name = parsed
            desktop_id = entry[: -len(".desktop")]
            for key in (desktop_id.lower(), exec_base, name):
                if key:
                    icons.setdefault(key, icon)
                    launchers.setdefault(key, desktop_id)

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
                if parsed and parsed[0]:
                    seen.add(pkg)
                    icons[pkg] = parsed[0]
                    launchers[pkg] = os.path.basename(path)[: -len(".desktop")]
    except Exception:
        pass  # simgeler süs; hata listeyi engellememeli

    return icons, launchers
