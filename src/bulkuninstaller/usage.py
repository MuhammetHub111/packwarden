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
import signal
import time

from . import host
from .appicons import APP_DIRS
from .backends.base import Package

USAGE_PATH = os.path.expanduser("~/.config/bulkuninstaller/usage.json")

# Bu kaynaklarda kimlik bir dosya/dizin yolu (ya da onun bir parçası) —
# eşleşme normal .desktop Exec= aramasıyla değil, doğrudan cmdline içinde
# bu yolun geçip geçmediğine bakılarak yapılır (bkz. scan_and_record,
# running_pids). Heroic/Lutris oyunları için kimlik install_path'tir.
_PATH_MATCH_SOURCES = (
    "appimage", "snap", "heroic-epic", "heroic-amazon", "heroic-gog", "lutris",
)

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


def _steam_pids_by_appid() -> dict[str, list[tuple[int, str]]]:
    """Çalışan her sürecin appid'sini /proc/<pid>/environ'daki SteamAppId
    değişkeninden okur, (pid, cmdline) çiftleri olarak döner.

    SteamAppId TEK BAŞINA güvenilir değil: canlı testte görüldü — Steam
    istemcisinin kendi steamwebhelper/zygote yardımcı süreçleri de bu
    değişkeni (büyük ihtimalle en son başlatılan/denenmiş oyundan miras
    kalan, fork sırasında donmuş bir değer olarak) taşıyabiliyor ve bu da
    hiç oynanmayan oyunları "az önce kullanıldı" gösteriyordu (birden
    fazla appid aynı anda, aynı zaman damgasıyla işaretlenmişti).
    Bu yüzden burada ham (appid, pid) eşleşmesi dönülür — gerçek eşleşme
    kararı _steam_corroborates() ile, pkg.install_path cmdline'da geçiyor
    mu diye ikinci bir doğrulamayla veriliyor (bkz. scan_and_record,
    running_pids). install_path Steam istemcisinin kendi süreçlerinde
    asla geçmez, bu yüzden onları elemeye yetiyor."""
    result: dict[str, list[tuple[int, str]]] = {}
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return result
    for pid in pids:
        try:
            with open(f"/proc/{pid}/environ", "rb") as f:
                environ = f.read()
        except OSError:
            continue
        appid = None
        for entry in environ.split(b"\0"):
            if entry.startswith(b"SteamAppId="):
                appid = entry[len(b"SteamAppId="):].decode("ascii", "replace").strip()
                break
        if not appid:
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmdline = f.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
        except OSError:
            cmdline = ""
        result.setdefault(appid, []).append((int(pid), cmdline))
    return result


def _steam_corroborates(pkg: Package, cmdline: str) -> bool:
    """SteamAppId eşleşmesini pkg.install_path cmdline'da geçiyor mu
    diye ikinci kez doğrular — bkz. _steam_pids_by_appid().

    ~/.steam/steam, gerçek Steam kurulumuna (genelde
    ~/.local/share/Steam) sembolik bağlantı olabiliyor; install_path
    hangi takma addan hesaplandıysa hesaplansın, süreçlerin cmdline'ı
    çözümlenmiş gerçek yolu kullanıyor (canlı testte doğrulandı: Half-
    Life için install_path ~/.steam/steam/... idi, cmdline ise
    ~/.local/share/Steam/... — ikisi de aynı dosyaya işaret ediyor ama
    ham string olarak eşleşmiyorlardı). Bu yüzden hem ham hali hem
    realpath()'i denenir."""
    if not pkg.install_path:
        return False
    if pkg.install_path in cmdline:
        return True
    real = os.path.realpath(pkg.install_path)
    return real != pkg.install_path and real in cmdline


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


# Bir süreç en az bu kadar (saniye) kesintisiz görülmeden "kullanıldı"
# diye kaydedilmez — hemen çöken/hata verip kapanan bir uygulamayı
# yanlışlıkla "az önce kullanıldı" saymamak için (SCAN_SECONDS=2 ile en
# az iki ardışık tarama gerektirir). Bu modülün yaşadığı süre boyunca
# bellekte tutulur (_pending) — daemon yeniden başlarsa sıfırlanır, bu
# sorun değil, sadece bir sonraki taramada yeniden sayılmaya başlar.
MIN_CONFIRM_SECONDS = 2
_pending: dict[str, float] = {}


def _confirm_running(key: str, now: float, running: bool) -> bool:
    if not running:
        _pending.pop(key, None)
        return False
    first_seen = _pending.get(key)
    if first_seen is None:
        _pending[key] = now
        return False
    return now - first_seen >= MIN_CONFIRM_SECONDS


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
    steam_running = None
    proc_snapshot = None

    for pkg in packages:
        key = _key(pkg)

        if pkg.source == "flatpak":
            if flatpak_running is None:
                flatpak_running = _running_flatpak_ids()
            if _confirm_running(key, now, pkg.id in flatpak_running):
                data[key] = now
                changed = True
            continue

        if pkg.source == "steam":
            if steam_running is None:
                steam_running = _steam_pids_by_appid()
            running = any(
                _steam_corroborates(pkg, cmdline)
                for _pid, cmdline in steam_running.get(pkg.id, [])
            )
            if _confirm_running(key, now, running):
                data[key] = now
                changed = True
            continue

        if pkg.source in _PATH_MATCH_SOURCES:
            needle = pkg.id if pkg.source == "appimage" else pkg.install_path
            if pkg.source == "snap":
                needle = f"/snap/{pkg.id}/"
        else:
            needle = _exec_base_for(pkg, launcher_map)

        if not needle:
            _pending.pop(key, None)
            continue

        if proc_snapshot is None:
            proc_snapshot = _proc_snapshot()

        running = False
        for comm, cmdline in proc_snapshot:
            if pkg.source in _PATH_MATCH_SOURCES:
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
                running = True
                break

        if _confirm_running(key, now, running):
            data[key] = now
            changed = True

    if changed:
        _save(data)


def _needle_for(pkg: Package, launcher_map: dict) -> str | None:
    """scan_and_record()'un kaynak bazlı needle seçimiyle aynı — Flatpak
    ve Steam hariç (ikisi de PID listesi için ayrıca ele alınıyor, bkz.
    running_pids)."""
    if pkg.source == "appimage":
        return pkg.id
    if pkg.source == "snap":
        return f"/snap/{pkg.id}/"
    if pkg.source in ("heroic-epic", "heroic-amazon", "heroic-gog", "lutris"):
        return pkg.install_path
    return _exec_base_for(pkg, launcher_map)


def running_pids(pkg: Package, launcher_map: dict) -> list[int]:
    """Bu paketin şu an çalışan süreçlerinin PID listesi (yoksa boş).

    scan_and_record() ile aynı eşleştirme mantığı ama PID de tutuyor —
    kaldırma öncesi çalışan süreci kapatabilmek için (bkz. close_running,
    removal.py). scan_and_record ayrı tutuldu, dokunulmadı."""
    if pkg.source == "flatpak":
        # "pid" sütunu SARICI sürecin PID'i — canlı test ettim (Gear
        # Lever), bu süreç sandbox tam kurulunca hemen kapanıyor,
        # kapatılacak gerçek süreç değil. "child-pid" gerçek, yalıtılmış
        # uygulama sürecinin PID'i — flatpak ps --help bunu doğruluyor.
        try:
            proc = host.run(
                ["flatpak", "ps", "--columns=application,child-pid"], timeout=5
            )
        except Exception:
            return []
        pids = []
        for line in proc.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] == pkg.id:
                try:
                    pids.append(int(parts[1]))
                except ValueError:
                    pass
        return pids

    if pkg.source == "steam":
        return [
            pid for pid, cmdline in _steam_pids_by_appid().get(pkg.id, [])
            if _steam_corroborates(pkg, cmdline)
        ]

    needle = _needle_for(pkg, launcher_map)
    if not needle:
        return []

    pids: list[int] = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return pids
    for pid_str in entries:
        if not pid_str.isdigit():
            continue
        comm = ""
        cmdline = ""
        try:
            with open(
                f"/proc/{pid_str}/comm", encoding="utf-8", errors="replace"
            ) as f:
                comm = f.read().strip()
        except OSError:
            pass
        try:
            with open(f"/proc/{pid_str}/cmdline", "rb") as f:
                cmdline = f.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
        except OSError:
            pass
        if not comm and not cmdline:
            continue
        if pkg.source in _PATH_MATCH_SOURCES:
            match = needle in cmdline
        else:
            first_token = cmdline.split()[0] if cmdline else ""
            match = (
                comm.lower() == needle
                or os.path.basename(first_token).lower() == needle
            )
        if match:
            pids.append(int(pid_str))
    return pids


def close_running(pkg: Package, launcher_map: dict) -> bool:
    """Bu paketin şu an çalışan süreçlerini kapatmayı dener.

    Önce SIGTERM (düzgün kapanma şansı), yarım saniye sonra hâlâ
    yaşıyorsa SIGKILL. En az bir süreç bulunduysa True döner — kapanmayı
    reddetse bile kaldırma işlemi yine de sürer, bu sadece "hiç
    kurulmamış gibi" bırakmak için bir en iyi çaba adımı (bkz. Kullanıcı
    isteği: kaldırma, uygulamayı arkada açık bırakmamalı)."""
    pids = running_pids(pkg, launcher_map)
    if not pids:
        return False
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    time.sleep(0.5)
    for pid in pids:
        try:
            os.kill(pid, 0)
        except OSError:
            continue  # zaten kapanmış
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    return True
