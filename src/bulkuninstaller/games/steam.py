"""Steam kütüphanesindeki oyunlar (yalnızca Unused Apps taraması için).

Silme, Steam'in KENDİ "steam://uninstall/<appid>" komutu üzerinden
Steam'e devrediliyor — kullanıcı Steam'in kendi "Kaldır" penceresini
onaylıyor, Steam de kendi dosyalarını/manifest'ini kendisi siliyor.
Önceki yaklaşım (dosyaları biz doğrudan siliyorduk) çalışıyordu ama
Steam'in kendisi steamapps klasörünü yalnızca açılışta okuyor - dışarıdan
silinen bir oyun, Steam yeniden başlatılana kadar hâlâ "Oyna" gösteriyordu
(canlı doğrulandı). Steam'in kendi kaldırma akışını kullanınca bu sorun
hiç oluşmuyor - Steam kendi durumunu kendi güncelliyor. Windows'ta
benzer bir araçla (kullanıcının kendi testi) karşılaştırıldı, aynı
yaklaşımı kullandığı görüldü.

Steam'in kendi kaldırıcısı Proton prefix'ini (compatdata), workshop
içeriğini ve gölgelendirici önbelleğini silmiyor (doğrulandı: Project
Zomboid'in workshop klasörü Steam'in kendi kaldırmasından sonra da
duruyordu) - bunlar Steam'in onayından SONRA ayrıca temizleniyor.
"""

import glob
import os
import re
import shutil
import time

from .. import host
from ..backends.base import Backend, Package, RemoveResult
from ._safety import is_within
from ._shortcuts import remove_matching_shortcuts
from ._steam_metadata import studios_for

# Kullanıcının Steam'in kendi "Kaldır" penceresini görüp onaylaması için
# beklenen en uzun süre - bu sürede manifest hâlâ duruyorsa (iptal etti
# ya da fark etmedi) kaldırma başarısız sayılır.
_CONFIRM_TIMEOUT_SECONDS = 120

STEAMAPPS_CANDIDATES = (
    "~/.steam/steam/steamapps",
    "~/.local/share/Steam/steamapps",
)

# Gerçek oyun olmayan Valve araç/çalışma zamanı girdileri
_SKIP_PREFIXES = (
    "steam linux runtime",
    "proton",
    "steamworks common redistributables",
)


def _steamapps_dir() -> str | None:
    for candidate in STEAMAPPS_CANDIDATES:
        path = os.path.expanduser(candidate)
        if os.path.isdir(path):
            return path
    return None


def _all_steamapps_dirs() -> list[str]:
    """Birincil kütüphane + libraryfolders.vdf'de tanımlı ek kütüphaneler
    (ör. başka bir diskteki "SteamLibrary" klasörü) — kullanıcı Steam'in
    kendi arayüzünden ikinci bir kütüphane eklediyse oradaki oyunlar da
    burada olmalı, yoksa sessizce görünmez kalırlar (doğrulandı: bir
    oyun ikinci kütüphanede kuruluyken listede hiç görünmüyordu)."""
    primary = _steamapps_dir()
    if not primary:
        return []
    dirs = [primary]
    seen = {os.path.realpath(primary)}
    try:
        text = open(
            os.path.join(primary, "libraryfolders.vdf"),
            encoding="utf-8", errors="replace",
        ).read()
    except OSError:
        text = ""
    for lib_path in re.findall(r'"path"\s*"([^"]+)"', text):
        candidate = os.path.join(lib_path, "steamapps")
        real = os.path.realpath(candidate)
        if real not in seen and os.path.isdir(candidate):
            seen.add(real)
            dirs.append(candidate)
    return dirs


def _field(text: str, key: str) -> str | None:
    match = re.search(r'"' + re.escape(key) + r'"\s*"([^"]*)"', text)
    return match.group(1) if match else None




_ICON_HASH_RE = re.compile(r"^[0-9a-f]{40}\.(jpg|png)$")


def _icon_path(appid: str) -> str:
    """Steam'in kendi kütüphane önbelleğindeki küçük kare simgeyi bul.

    appcache/librarycache/<appid>/ altında sabit adlı banner/logo
    dosyaları dışında, tek bir SHA1-hash.jpg dosyası gerçek küçük
    simgedir (doğrulandı: appcache/librarycache/730/<hash>.jpg CS2'nin
    ikonu). Bulunamazsa boş döner, çağıran genel simgeye düşer.

    Bu önbellek HER ZAMAN birincil Steam kurulumunun altındadır — oyun
    ikinci bir kütüphanede (ör. başka bir diskteki SteamLibrary) kurulu
    olsa bile. Canlı testte doğrulandı: ikinci kütüphanedeki 3 oyun,
    kendi steamapps'lerinin yanında hiç var olmayan bir appcache aranınca
    ikon bulamıyor, genel oyun simgesine düşüyordu."""
    primary = _steamapps_dir()
    if not primary:
        return ""
    cache_dir = os.path.join(
        os.path.dirname(primary), "appcache", "librarycache", appid
    )
    try:
        entries = os.listdir(cache_dir)
    except OSError:
        return ""
    for entry in entries:
        path = os.path.join(cache_dir, entry)
        if _ICON_HASH_RE.match(entry) and os.path.isfile(path):
            return path
    return ""


class SteamGameBackend(Backend):
    id = "steam"
    display_name = "Steam"
    needs_root = False

    def is_available(self) -> bool:
        return _steamapps_dir() is not None

    def list_packages(self) -> list[Package]:
        steamapps_dirs = _all_steamapps_dirs()
        if not steamapps_dirs:
            return []

        manifests = []
        for steamapps in steamapps_dirs:
            for manifest_path in glob.glob(
                os.path.join(steamapps, "appmanifest_*.acf")
            ):
                try:
                    text = open(
                        manifest_path, encoding="utf-8", errors="replace"
                    ).read()
                except OSError:
                    continue

                appid = _field(text, "appid")
                name = _field(text, "name")
                if not appid or not name:
                    continue
                if name.lower().startswith(_SKIP_PREFIXES):
                    continue

                try:
                    last_played = int(_field(text, "LastPlayed") or "0")
                except ValueError:
                    last_played = 0
                try:
                    size = int(_field(text, "SizeOnDisk") or "0")
                except ValueError:
                    size = 0
                # Steam'de "1.2.3" gibi klasik bir sürüm numarası yok;
                # oyun her güncellenince değişen "buildid" en yakın karşılığı
                buildid = _field(text, "buildid") or ""
                installdir = _field(text, "installdir") or ""
                try:
                    last_updated = int(_field(text, "LastUpdated") or "0")
                except ValueError:
                    last_updated = 0

                manifests.append(
                    (steamapps, appid, name, buildid, size, last_played,
                     installdir, last_updated)
                )

        # Gerçek stüdyo adı yerelde hiç yok (.acf'de sadece appid/versiyon
        # var) — hepsi için TEK seferde, paralel olarak Store API'sinden
        # çekiliyor (bkz. _steam_metadata.py); sonuç kalıcı önbelleklenir.
        studios = studios_for([m[1] for m in manifests])

        packages = []
        for (steamapps, appid, name, buildid, size, last_played,
             installdir, last_updated) in manifests:
            install_path = ""
            if installdir:
                candidate = os.path.join(steamapps, "common", installdir)
                if os.path.isdir(candidate):
                    install_path = candidate
            packages.append(Package(
                id=appid,
                name=name,
                version=buildid,
                size=size,
                description="",
                source=self.id,
                publisher=studios.get(appid, ""),
                last_used=float(last_played) if last_played > 0 else None,
                icon_path=_icon_path(appid),
                install_path=install_path,
                # Steam kurulum tarihini ayrı vermiyor; "LastUpdated" en
                # yakın karşılığı (hiç güncellenmemiş bir oyunda kurulum
                # zamanıyla aynı, güncellenmişse son güncelleme zamanı —
                # tıpkı pacman'in "Install Date"i gibi, o da yükseltmede
                # yenileniyor).
                install_date=float(last_updated) if last_updated > 0 else None,
            ))
        return packages

    def remove_argv(self, ids: list[str]) -> list[str]:
        return ["true"]  # remove() bu yolu kullanmaz, dosya sistemiyle çalışır

    def remove(self, ids: list[str]) -> RemoveResult:
        steamapps_dirs = _all_steamapps_dirs()
        if not steamapps_dirs:
            return RemoveResult(False, "Steam library not found", failed_ids=ids)

        failed = []
        messages = []
        for appid in ids:
            # Oyun birden fazla kütüphaneden (ör. ikinci diskteki
            # "SteamLibrary") herhangi birinde olabilir — manifest'i
            # bulana kadar hepsini dener.
            steamapps = None
            manifest_path = None
            for candidate_dir in steamapps_dirs:
                candidate_manifest = os.path.join(
                    candidate_dir, f"appmanifest_{appid}.acf"
                )
                if os.path.isfile(candidate_manifest):
                    steamapps = candidate_dir
                    manifest_path = candidate_manifest
                    break

            if not steamapps:
                failed.append(appid)
                messages.append(f"{appid}: manifest not found in any library")
                continue

            try:
                host.run(["steam", f"steam://uninstall/{appid}"], timeout=10)
            except Exception as exc:
                failed.append(appid)
                messages.append(f"{appid}: {exc}")
                continue

            # Steam kendi "Kaldır" penceresini açtı - kullanıcı orada
            # onaylayana kadar bekleniyor. Manifest kalkması, Steam'in
            # GERÇEKTEN kaldırdığının kanıtı (kullanıcı iptal ederse ya
            # da fark etmezse zaman aşımına uğrar, başarısız sayılır).
            deadline = time.monotonic() + _CONFIRM_TIMEOUT_SECONDS
            confirmed = False
            while time.monotonic() < deadline:
                if not os.path.isfile(manifest_path):
                    confirmed = True
                    break
                time.sleep(1)

            if not confirmed:
                failed.append(appid)
                messages.append(
                    f"{appid}: Steam'in kendi kaldırma penceresi onaylanmadı"
                )
                continue

            try:
                # Steam'in kendi kaldırıcısı bunları silmiyor (doğrulandı) -
                # yol, appid'den geliyor (sıradan metin), her silme öncesi
                # gerçek sonucun beklenen kök İÇİNDE kaldığı doğrulanıyor.
                compat_root = os.path.join(steamapps, "compatdata")
                compat_path = os.path.join(compat_root, appid)
                if os.path.isdir(compat_path) and is_within(compat_path, compat_root):
                    shutil.rmtree(compat_path)

                workshop_root = os.path.join(steamapps, "workshop", "content")
                workshop_content = os.path.join(workshop_root, appid)
                if os.path.isdir(workshop_content) and is_within(
                    workshop_content, workshop_root
                ):
                    shutil.rmtree(workshop_content)

                workshop_dir = os.path.join(steamapps, "workshop")
                workshop_acf = os.path.join(workshop_dir, f"appworkshop_{appid}.acf")
                acf_parent = os.path.dirname(os.path.realpath(workshop_acf))
                if os.path.isfile(workshop_acf) and acf_parent == os.path.realpath(
                    workshop_dir
                ):
                    os.remove(workshop_acf)

                shader_root = os.path.join(steamapps, "shadercache")
                shadercache = os.path.join(shader_root, appid)
                if os.path.isdir(shadercache) and is_within(shadercache, shader_root):
                    shutil.rmtree(shadercache)

                remove_matching_shortcuts(f"steam://rungameid/{appid}")
            except OSError:
                pass  # ana kaldırma (Steam onayladı) zaten başarılı sayılır

        if failed:
            return RemoveResult(False, "\n".join(messages), failed_ids=failed)
        return RemoveResult(True, "")
