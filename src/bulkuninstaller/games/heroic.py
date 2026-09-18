"""Heroic Games Launcher kütüphanesindeki oyunlar (Epic/GOG/Amazon).

Yalnızca Unused Apps taraması için. Heroic yerelde gerçek "son oynama
zamanı" tutmuyor (doğrulandı) — last_used, kurulum klasörünün
mtime'ı üzerinden YAKLAŞIK olarak hesaplanır; arayüzde bu açıkça
belirtilir.

Silme:
- Epic: birlikte gelen `legendary uninstall <app_name> -y` (doğrulandı)
- Amazon: birlikte gelen `nile uninstall <id>` (dosya yapısı Heroic'in
  kendi decompile edilmiş kaynağından doğrulandı, ama bu makinede
  kurulu Amazon oyunu olmadığı için uçtan uca uninstall çalıştırılıp
  denenemedi)
- GOG: gogdl'de uninstall alt komutu YOK (doğrulandı) — kurulum
  klasörü elle silinip yerel install-info kaydı temizlenir

Bu makinede Epic ve GOG üzerinden kurulu gerçek oyunlarla canlı
doğrulandı (bkz. HeroicGogBackend/HeroicEpicBackend.list_packages).
Amazon'da hiç kurulu oyun olmadığı için list_packages/remove'un dosya
yapısı Heroic'in kendi ana süreç kaynağından (main.js) çıkarılıp
doğrulandı - GOG/Epic'teki gibi canlı bir kurulu oyunla test edilemedi."""

import json
import os
import shutil

from .. import host
from ..backends.base import Backend, Package, RemoveResult, parse_human_size
from ._icons import cached_icon
from ._safety import is_critical
from ._shortcuts import remove_matching_shortcuts

CONFIG_DIR = "~/.config/heroic"

_BIN_CANDIDATES = (
    "/opt/Heroic/resources/app.asar.unpacked/build/bin/x64/linux",
)


def _bin_path(tool: str) -> str | None:
    for base in _BIN_CANDIDATES:
        path = os.path.join(base, tool)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    # _BIN_CANDIDATES yalnızca native (AUR/deb/rpm) Heroic kurulumunu
    # kapsıyor; Flatpak Heroic'in gerçek iç yolu bu makinede kurulu
    # olmadığı için doğrulanamadı - tahmin edip sabit kodlamak yerine
    # PATH'te aynı adla bir araç varsa (ör. ayrı bir AUR paketinden)
    # ona düşülüyor, hiçbir şeye güvenmemekten daha iyi.
    return shutil.which(tool)


def _config_path(*parts: str) -> str:
    return os.path.join(os.path.expanduser(CONFIG_DIR), *parts)


def _load_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _approx_last_used(install_path: str) -> float | None:
    try:
        return os.path.getmtime(install_path)
    except OSError:
        return None


def _run_uninstall(
    binary: str, argv_tail: list[str], ids: list[str],
    env_overrides: dict[str, str] | None = None,
) -> RemoveResult:
    env_prefix = (
        ["env"] + [f"{k}={v}" for k, v in env_overrides.items()]
        if env_overrides else []
    )
    failed = []
    messages = []
    for game_id in ids:
        try:
            proc = host.run(env_prefix + [binary, *argv_tail, game_id], timeout=300)
        except Exception as exc:
            failed.append(game_id)
            messages.append(f"{game_id}: {exc}")
            continue
        if proc.returncode != 0:
            failed.append(game_id)
            messages.append(f"{game_id}: {(proc.stdout or '') + (proc.stderr or '')}")
        else:
            # Heroic, oyunu eklerken isteğe bağlı .desktop kısayolu
            # oluşturabiliyor (menü + masaüstü) ama legendary/nile
            # uninstall bunu hiç silmiyor (doğrulandı) — kalırsa oyun
            # tamamen kaldırılsa bile ikonu görünmeye devam ediyor.
            remove_matching_shortcuts(game_id)
    if failed:
        return RemoveResult(False, "\n".join(messages), failed_ids=failed)
    return RemoveResult(True, "")


def _epic_library_map() -> dict[str, dict]:
    """app_name -> {"art_square", "developer"} (legendary_library.json'dan).

    Bu dosya Heroic'in kendi Epic kütüphane önbelleği — gerçek geliştirici
    adı (Epic mağazasındaki "developer" alanı) burada zaten yerelde
    duruyor, ağ isteği gerekmiyor."""
    data = _load_json(_config_path("store_cache", "legendary_library.json"))
    if not isinstance(data, dict):
        return {}
    library = data.get("library")
    if not isinstance(library, list):
        return {}
    result = {}
    for entry in library:
        if isinstance(entry, dict) and entry.get("app_name"):
            result[entry["app_name"]] = {
                "art_square": entry.get("art_square") or "",
                "developer": entry.get("developer") or "",
            }
    return result


def _gog_library_map() -> dict[str, dict]:
    """app_name -> {"title", "art_square", "developer"}
    (store_cache/gog_library.json'dan - Epic'in legendary_library.json'ı
    ile aynı rolde, kullanıcının GOG'da sahip olduğu TÜM oyunların
    önbelleği, kurulu olsun olmasın).

    Bu dosyanın da üst seviyede "games" listesinin YANINDA bir
    "__timestamp" bakım anahtarı var (gog_install_info.json'daki gibi -
    canlı doğrulandı) ama "games" listesinin dışında olduğu için buraya
    hiç girmiyor, ekstra bir eleme gerekmiyor."""
    data = _load_json(_config_path("store_cache", "gog_library.json"))
    if not isinstance(data, dict):
        return {}
    games = data.get("games")
    if not isinstance(games, list):
        return {}
    result = {}
    for entry in games:
        if isinstance(entry, dict) and entry.get("app_name"):
            result[entry["app_name"]] = {
                "title": entry.get("title") or "",
                "art_square": entry.get("art_square") or "",
                "developer": entry.get("developer") or "",
            }
    return result


def _nile_library_map() -> dict[str, dict]:
    """app_name -> {"title", "art_square", "developer"}
    (nile_config/nile/library.json'dan - Heroic'in kendi decompile
    edilmiş kaynağından doğrulandı: Epic/GOG'un aksine bu dosya
    {"library"/"games": [...]} şeklinde SARILMAMIŞ, düz bir dizi; her
    öğenin alanları da iç içe - product.title, product.id,
    product.productDetail.iconUrl (art_square karşılığı),
    ...details.developer."""
    data = _load_json(_config_path("nile_config", "nile", "library.json"))
    if not isinstance(data, list):
        return {}
    result = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        product = entry.get("product")
        if not isinstance(product, dict) or not product.get("id"):
            continue
        detail = product.get("productDetail") or {}
        details = detail.get("details") or {}
        result[product["id"]] = {
            "title": product.get("title") or "",
            "art_square": detail.get("iconUrl") or "",
            "developer": details.get("developer") or "",
        }
    return result


class HeroicEpicBackend(Backend):
    id = "heroic-epic"
    display_name = "Heroic (Epic)"
    needs_root = False

    def is_available(self) -> bool:
        return _bin_path("legendary") is not None

    def list_packages(self) -> list[Package]:
        data = _load_json(
            _config_path("legendaryConfig", "legendary", "installed.json")
        )
        if not isinstance(data, dict):
            return []
        epic_library = _epic_library_map()
        packages = []
        for app_name, info in data.items():
            if not isinstance(info, dict):
                continue
            title = info.get("title") or app_name
            install_path = info.get("install_path") or ""
            try:
                size = int(info.get("install_size") or 0)
            except (TypeError, ValueError):
                size = 0
            meta = epic_library.get(app_name, {})
            icon_path = cached_icon(meta.get("art_square", "")) or ""
            packages.append(Package(
                id=app_name,
                name=title,
                version="",
                size=size,
                description="",
                source=self.id,
                publisher=meta.get("developer") or "Epic Games (Heroic)",
                last_used=_approx_last_used(install_path) if install_path else None,
                icon_path=icon_path,
                install_path=install_path,
                # Heroic gerçek kurulum tarihini ayrı tutmuyor; kurulum
                # klasörünün değişim zamanı en yakın karşılığı (last_used
                # ile aynı kaynağı paylaşıyor, ikisi de yaklaşık).
                install_date=_approx_last_used(install_path) if install_path else None,
            ))
        return packages

    def remove_argv(self, ids: list[str]) -> list[str]:
        return ["true"]  # remove() birlikte gelen legendary ikilisini çağırır

    def remove(self, ids: list[str]) -> RemoveResult:
        binary = _bin_path("legendary")
        if not binary:
            return RemoveResult(False, "legendary not found", failed_ids=ids)
        # legendary, LEGENDARY_CONFIG_PATH verilmezse KENDİ varsayılan
        # config klasörünü (~/.config/legendary) kullanır - Heroic'in
        # kendi kopyası (installed.json dahil) legendaryConfig/legendary
        # altında, apayrı bir yer. Canlı doğrulandı: bu değişken
        # verilmeden "uninstall" çağrısı gerçek, kurulu bir oyun için
        # bile "Game ... not installed, cannot uninstall!" diyordu -
        # ÜSTÜNE bu hatayla bile çıkış kodu 0 dönüyordu, yani PackWarden
        # hiçbir şey silinmediği hâlde "kaldırıldı" sanıyordu.
        env = {"LEGENDARY_CONFIG_PATH": _config_path("legendaryConfig", "legendary")}
        return _run_uninstall(binary, ["uninstall", "-y"], ids, env_overrides=env)


class HeroicAmazonBackend(Backend):
    id = "heroic-amazon"
    display_name = "Heroic (Amazon)"
    needs_root = False

    def is_available(self) -> bool:
        return _bin_path("nile") is not None

    def list_packages(self) -> list[Package]:
        # DİKKAT (GOG'daki aynı hata sınıfı, Heroic'in kendi decompile
        # edilmiş kaynağından doğrulandı): store_cache/nile_install_info.json
        # "kurulu oyunlar" listesi DEĞİL, Heroic'in kendi ayrı iç
        # önbelleği. Gerçek kurulu-oyun listesi nile_config/nile/
        # installed.json - GOG'dan farklı olarak bu dosya {"installed":
        # [...]} şeklinde SARILMAMIŞ, DÜZ bir dizi; alan adları da farklı
        # ("id" değil "appName", "path" değil "install_path" vb. yok -
        # "id"/"path"/"size"/"version"). Başlık/ikon/geliştirici için
        # ayrıca _nile_library_map()'e bakılıyor.
        data = _load_json(_config_path("nile_config", "nile", "installed.json"))
        if not isinstance(data, list):
            return []
        library = _nile_library_map()
        packages = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            app_name = entry.get("id")
            if not app_name:
                continue
            install_path = entry.get("path") or ""
            try:
                size = int(entry.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            meta = library.get(app_name, {})
            icon_path = cached_icon(meta.get("art_square", "")) or ""
            packages.append(Package(
                id=app_name,
                name=meta.get("title") or app_name,
                version=entry.get("version") or "",
                size=size,
                description="",
                source=self.id,
                publisher=meta.get("developer") or "Amazon Games (Heroic)",
                last_used=_approx_last_used(install_path) if install_path else None,
                icon_path=icon_path,
                install_path=install_path,
                install_date=_approx_last_used(install_path) if install_path else None,
            ))
        return packages

    def remove_argv(self, ids: list[str]) -> list[str]:
        return ["true"]  # remove() birlikte gelen nile ikilisini çağırır

    def remove(self, ids: list[str]) -> RemoveResult:
        binary = _bin_path("nile")
        if not binary:
            return RemoveResult(False, "nile not found", failed_ids=ids)
        # legendary'deki AYNI sorunun nile'da da olması bekleniyor -
        # Heroic'in kendi decompile edilmiş kaynağından DOĞRULANDI:
        # options.env.NILE_CONFIG_PATH = path.dirname(nileConfigPath),
        # nileConfigPath = appFolder/nile_config/nile. Yani legendary'nin
        # tersine (LEGENDARY_CONFIG_PATH tam "legendary" klasörünü
        # gösteriyor), nile kendi "nile" alt klasörünü İÇERDE kendisi
        # ekliyor - buraya "nile_config/nile" verilseydi çift katmanlı
        # "nile_config/nile/nile/..." aranırdı (önceki hatalı hâl).
        # Bu makinede kurulu Amazon oyunu olmadığı için uçtan uca
        # doğrulanamadı ama yol artık Heroic'in kendi kaynağıyla eşleşiyor.
        env = {"NILE_CONFIG_PATH": _config_path("nile_config")}
        return _run_uninstall(binary, ["uninstall"], ids, env_overrides=env)


class HeroicGogBackend(Backend):
    id = "heroic-gog"
    display_name = "Heroic (GOG)"
    needs_root = False

    def is_available(self) -> bool:
        return _bin_path("gogdl") is not None

    def list_packages(self) -> list[Package]:
        # DİKKAT: store_cache/gog_install_info.json "kurulu oyunlar"
        # listesi DEĞİL - GOG'un indirme manifest önbelleği (canlı
        # doğrulandı: gerçek bir GOG oyunu kurulunca burada iç içe
        # {"game": {...}} yapısı VE "__timestamp" adında bir bakım
        # anahtarı görüldü - önceki kod ikisini de "kurulu oyun"
        # sanıp __timestamp'i de listede bir paket gibi gösteriyordu).
        # Gerçek kurulu-oyun listesi gog_store/installed.json'daki
        # "installed" dizisi - başlık/ikon/geliştirici için ayrıca
        # _gog_library_map()'e bakılıyor (o dosyada yalnızca app_name
        # var, başka hiçbir görüntüleme bilgisi yok - canlı doğrulandı:
        # ikon hiç gösterilmiyordu).
        data = _load_json(_config_path("gog_store", "installed.json"))
        if not isinstance(data, dict):
            return []
        installed = data.get("installed")
        if not isinstance(installed, list):
            return []
        library = _gog_library_map()
        packages = []
        for entry in installed:
            if not isinstance(entry, dict):
                continue
            app_name = entry.get("appName")
            if not app_name:
                continue
            install_path = entry.get("install_path") or ""
            meta = library.get(app_name, {})
            icon_path = cached_icon(meta.get("art_square", "")) or ""
            packages.append(Package(
                id=app_name,
                name=meta.get("title") or app_name,
                version=entry.get("version") or "",
                # install_size burada "2.31 GiB" gibi biçimlendirilmiş
                # bir dize - Epic/Amazon'daki ham bayt sayısından farklı.
                size=parse_human_size(entry.get("install_size") or ""),
                description="",
                source=self.id,
                publisher=meta.get("developer") or "GOG (Heroic)",
                last_used=_approx_last_used(install_path) if install_path else None,
                icon_path=icon_path,
                install_path=install_path,
                install_date=_approx_last_used(install_path) if install_path else None,
            ))
        return packages

    def remove_argv(self, ids: list[str]) -> list[str]:
        return ["true"]  # remove() gogdl'de uninstall olmadığı için elle siler

    def remove(self, ids: list[str]) -> RemoveResult:
        # gogdl'de uninstall alt komutu yok (doğrulandı) — kurulum
        # klasörünü elle sil, gog_store/installed.json'daki "installed"
        # dizisinden kaydı çıkar (bkz. list_packages'taki gerçek dosya
        # yapısı notu).
        info_path = _config_path("gog_store", "installed.json")
        data = _load_json(info_path)
        if not isinstance(data, dict) or not isinstance(data.get("installed"), list):
            return RemoveResult(False, "GOG installed.json not found", failed_ids=ids)

        failed = []
        messages = []
        found = set()
        remaining = []
        for entry in data["installed"]:
            app_name = entry.get("appName") if isinstance(entry, dict) else None
            if app_name not in ids:
                remaining.append(entry)
                continue
            found.add(app_name)
            install_path = entry.get("install_path")
            try:
                # install_path, Heroic'in yerel JSON kaydından geliyor —
                # sıradan, korumasız bir dosya, herhangi bir yerel süreç
                # tarafından değiştirilebilir. Kurcalanmışsa (ör. ev
                # dizinine ayarlanmışsa) rmtree çağırmadan önce reddet.
                if (
                    install_path and os.path.isdir(install_path)
                    and not is_critical(install_path)
                ):
                    shutil.rmtree(install_path)
                remove_matching_shortcuts(app_name)
            except OSError as exc:
                failed.append(app_name)
                messages.append(f"{app_name}: {exc}")
                remaining.append(entry)  # silinemedi, kayıtta tut

        for missing in set(ids) - found:
            failed.append(missing)
            messages.append(f"{missing}: gog_store/installed.json'da bulunamadı")

        data["installed"] = remaining
        try:
            with open(info_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError:
            pass  # oyun klasörü zaten silindi; kayıt temizliği ikincil

        if failed:
            return RemoveResult(False, "\n".join(messages), failed_ids=failed)
        return RemoveResult(True, "")
