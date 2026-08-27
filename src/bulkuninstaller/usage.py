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
from .backends.wine import exec_basename as _wine_exec_basename

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
    # Atomik yazma: aynı dizinde geçici bir dosyaya yazıp os.replace ile
    # yerine koyuyoruz. Bu dosyayı biri arka plan daemon'ı, biri de
    # PackWarden'ın kendisi (Kullanılmayan Uygulamalar penceresi açıkken)
    # olmak üzere iki ayrı süreç eşzamanlı güncelleyebiliyor — doğrudan
    # üzerine yazmak, ikisi tam o anda çakışırsa yarım/bozuk bir JSON
    # bırakıp tüm geçmişin sessizce sıfırlanmasına yol açabilirdi.
    # os.replace POSIX'te atomiktir: bir okuyucu ya eski ya da yeni
    # tam içeriği görür, asla yarısını görmez.
    tmp_path = f"{USAGE_PATH}.tmp.{os.getpid()}"
    try:
        os.makedirs(os.path.dirname(USAGE_PATH), exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, USAGE_PATH)
    except OSError:
        # diske yazılamasa da oturum boyunca tarama yine çalışır — ama
        # yarım kalan geçici dosyayı arkada bırakmayalım. Bu temizlik de
        # başarısız olursa (ör. dosya hiç oluşmadıysa) asıl hatayı
        # gizlemeden sessizce yut.
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def _key(pkg: Package) -> str:
    return f"{pkg.source}:{pkg.id}"


def get_seen_map() -> dict:
    """Tüm "paket -> son görülme zamanı" kaydını bir kerede döner.

    Birden çok paket için get_seen() çağıracak bir tarama döngüsü, bunun
    yerine bu haritayı bir kez yükleyip get_seen(pkg, seen_map=...) ile
    kullanmalı — aksi halde her paket için dosya yeniden okunur/ayrıştırılır.
    """
    return _load()


def get_seen(pkg: Package, seen_map: dict | None = None) -> float | None:
    """Paketin en son "çalışırken görüldüğü" zaman; hiç görülmediyse None.

    seen_map verilmezse dosya bu çağrı için ayrıca yüklenir (tek paketlik
    kullanım için uygun); bir döngü içinde çağrılıyorsa get_seen_map() ile
    önceden yüklenip buraya geçilmeli.
    """
    if seen_map is None:
        seen_map = _load()
    return seen_map.get(_key(pkg))


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
        elif pkg.source == "wine":
            # launch_argv() ile AYNI .exe çözümlemesi (bkz. backends/wine.py:
            # _resolve_exe) — needle her zaman o Wine paketinin SPESİFİK
            # hedef .exe'si, bu yüzden wineserver/winedevice.exe gibi
            # prefix'e ait paylaşılan süreçlerle asla eşleşmez. Proton/Steam
            # prefix'leri _prefixes()'in tarama kapsamına hiç girmediği için
            # (~/.wine ve $WINEPREFIX dışında hiçbir yeri taramıyor) burada
            # ayrıca bir dışlama gerekmiyor.
            needle = _wine_exec_basename(pkg.id)
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
                # comm/cmdline büyük/küçük harf korur (Windows .exe adları
                # sık sık PascalCase, ör. "KeePass.exe") ama needle her
                # zaman küçük harf üretiliyor (_exec_base_for/exec_basename)
                # — karşılaştırmadan önce ikisini de küçük harfe çevirmek
                # gerekiyor. Linux ikili adları zaten neredeyse hep küçük
                # harf olduğundan bu, diğer kaynaklar için etkisiz (no-op).
                match = (
                    comm.lower() == needle
                    or os.path.basename(first_token).lower() == needle
                )
            if match:
                data[_key(pkg)] = now
                changed = True
                break

    if changed:
        _save(data)
