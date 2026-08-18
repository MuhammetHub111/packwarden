"""Yerel, ağa hiç çıkmayan "şu an gerçekten çalışıyor mu" algılayıcısı.

unused.py'deki last_used() tahmini, paketin ayar/önbellek klasörünün son
değişim zamanına dayanıyor — bir uygulama o klasöre hiç yazmadan sadece
açılıp kapatılırsa bu sinyal güncellenmiyor ve Kullanılmayan Uygulamalar
listesi yanlışlıkla "hâlâ kullanılmıyor" gösterebiliyor.

Burada bunun yerine paketin şu an fiilen çalışıp çalışmadığına bakılır:
- Flatpak için resmi `flatpak ps` komutu (uygulama kimliğiyle birebir).
- Geri kalan tüm kaynaklar için yerel süreç tablosu (/proc) — Snap için
  /snap/<ad>/ yol öneki, AppImage için tam dosya yolu, diğerlerinde
  .desktop dosyasının Exec= ikili adı aranır.

Eşleşme bulunursa "şimdi" zamanı ~/.config/bulkuninstaller/usage.json
dosyasına yazılır. Hiçbir ağ isteği yapılmaz, hiçbir veri bu makineden
dışarı çıkmaz; dosyanın kendisi de sadece paket kimliği → zaman damgası
tutar, süreç isimleri veya pencere başlıkları gibi ayrıntı saklanmaz.

Bu bir arka plan servisi DEĞİLDİR — yalnızca PackWarden açıkken, tarama
çağrıldığı anda ne çalışıyorsa onu görür. Uygulama kapalıyken başka bir
uygulamanın kullanımı bu şekilde yakalanamaz; bu, sürekli çalışan bir
sistem servisi olmadan ulaşılabilecek en iyi yaklaşımdır.
"""

import json
import os
import time

from . import host
from .appicons import APP_DIRS
from .backends.base import Package

USAGE_PATH = os.path.expanduser("~/.config/bulkuninstaller/usage.json")

_EXTRA_DESKTOP_DIRS = (
    "/var/lib/flatpak/exports/share/applications",
    "~/.local/share/flatpak/exports/share/applications",
    "/var/lib/snapd/desktop/applications",
)


def _load() -> dict:
    try:
        with open(USAGE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {
                k: float(v) for k, v in data.items() if isinstance(v, (int, float))
            }
    except (OSError, ValueError, TypeError):
        pass
    return {}


def _save(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(USAGE_PATH), exist_ok=True)
        with open(USAGE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass  # diske yazılamasa da oturum boyunca tarama yine çalışır


def _key(pkg: Package) -> str:
    return f"{pkg.source}:{pkg.id}"


def get_seen(pkg: Package) -> float | None:
    """Paketin en son "çalışırken görüldüğü" zaman; hiç görülmediyse None."""
    return _load().get(_key(pkg))


def _running_flatpak_ids() -> set:
    try:
        proc = host.run(["flatpak", "ps", "--columns=application"], timeout=5)
    except Exception:
        return set()
    if proc.returncode != 0:
        return set()
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def _proc_snapshot() -> list:
    """Her çalışan süreç için (comm, cmdline) çiftlerinin listesi."""
    snapshot = []
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return snapshot
    for pid in pids:
        comm = ""
        cmdline = ""
        try:
            with open(f"/proc/{pid}/comm", encoding="utf-8", errors="replace") as f:
                comm = f.read().strip()
        except OSError:
            pass
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmdline = f.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
        except OSError:
            pass
        if comm or cmdline:
            snapshot.append((comm, cmdline))
    return snapshot


def _exec_base_for(pkg: Package, launcher_map: dict) -> str | None:
    """Paketin .desktop dosyasındaki Exec= ikilisinin adı (küçük harf)."""
    desktop_id = launcher_map.get(pkg.id.lower()) or launcher_map.get(
        pkg.name.lower()
    )
    if not desktop_id:
        return None
    for base in APP_DIRS + _EXTRA_DESKTOP_DIRS:
        path = os.path.join(os.path.expanduser(base), f"{desktop_id}.desktop")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.startswith("Exec="):
                        tokens = line[5:].strip().split()
                        if tokens:
                            return os.path.basename(tokens[0]).lower()
        except OSError:
            continue
    return None


def scan_and_record(packages: list, launcher_map: dict) -> None:
    """Şu an çalışan paketleri tespit edip yerel kayda "şimdi" yazar.

    Eşleşme bulunamayan paketler dokunulmadan bırakılır — önceki kayıt
    (varsa) korunur, "çalışmıyor" diye bir şey yazılmaz. Bu, hatalı
    negatiften (yanlışlıkla "kullanılmadı" demekten) kaçınmak içindir.
    """
    now = time.time()
    data = _load()
    changed = False
    flatpak_running = None
    proc_snapshot = None

    for pkg in packages:
        if pkg.source == "flatpak":
            if flatpak_running is None:
                flatpak_running = _running_flatpak_ids()
            if pkg.id in flatpak_running:
                data[_key(pkg)] = now
                changed = True
            continue

        if pkg.source == "appimage":
            needle = pkg.id  # kimlik = tam dosya yolu
        elif pkg.source == "snap":
            needle = f"/snap/{pkg.id}/"
        else:
            needle = _exec_base_for(pkg, launcher_map)

        if not needle:
            continue

        if proc_snapshot is None:
            proc_snapshot = _proc_snapshot()

        for comm, cmdline in proc_snapshot:
            if pkg.source in ("appimage", "snap"):
                match = needle in cmdline
            else:
                first_token = cmdline.split()[0] if cmdline else ""
                match = comm == needle or os.path.basename(first_token) == needle
            if match:
                data[_key(pkg)] = now
                changed = True
                break

    if changed:
        _save(data)
