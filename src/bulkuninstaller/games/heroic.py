"""Heroic Games Launcher kütüphanesindeki oyunlar (Epic/GOG/Amazon).

Yalnızca Unused Apps taraması için. Heroic yerelde gerçek "son oynama
zamanı" tutmuyor (doğrulandı) — last_used, kurulum klasörünün
mtime'ı üzerinden YAKLAŞIK olarak hesaplanır; arayüzde bu açıkça
belirtilir.

Silme:
- Epic: birlikte gelen `legendary uninstall <app_name> -y` (doğrulandı)
- Amazon: birlikte gelen `nile uninstall <id>` (doğrulandı)
- GOG: gogdl'de uninstall alt komutu YOK (doğrulandı) — kurulum
  klasörü elle silinip yerel install-info kaydı temizlenir

Bu makinede yalnızca Epic üzerinden kurulu oyun var; GOG/Amazon
ayrıştırma mantığı bu yüzden savunmacı yazıldı (beklenmeyen bir alan
adı sessizce atlanır, hiçbir şeyi çökertmez) ama uçtan uca
doğrulanamadı.
"""

import json
import os
import shutil

from .. import host
from ..backends.base import Backend, Package, RemoveResult
from ._icons import cached_icon
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
    return None


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


def _run_uninstall(binary: str, argv_tail: list[str], ids: list[str]) -> RemoveResult:
    failed = []
    messages = []
    for game_id in ids:
        try:
            proc = host.run([binary, *argv_tail, game_id], timeout=300)
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
        return _run_uninstall(binary, ["uninstall", "-y"], ids)


class HeroicAmazonBackend(Backend):
    id = "heroic-amazon"
    display_name = "Heroic (Amazon)"
    needs_root = False

    def is_available(self) -> bool:
        return _bin_path("nile") is not None

    def list_packages(self) -> list[Package]:
        data = _load_json(_config_path("store_cache", "nile_install_info.json"))
        if not isinstance(data, dict):
            return []
        packages = []
        for game_id, info in data.items():
            if not isinstance(info, dict):
                continue
            title = info.get("title") or info.get("name") or game_id
            install_path = info.get("install_path") or info.get("path") or ""
            try:
                size = int(info.get("install_size") or info.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            packages.append(Package(
                id=game_id,
                name=title,
                version="",
                size=size,
                description="",
                source=self.id,
                publisher="Amazon Games (Heroic)",
                last_used=_approx_last_used(install_path) if install_path else None,
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
        return _run_uninstall(binary, ["uninstall"], ids)


class HeroicGogBackend(Backend):
    id = "heroic-gog"
    display_name = "Heroic (GOG)"
    needs_root = False

    def is_available(self) -> bool:
        return _bin_path("gogdl") is not None

    def list_packages(self) -> list[Package]:
        data = _load_json(_config_path("store_cache", "gog_install_info.json"))
        if not isinstance(data, dict):
            return []
        packages = []
        for game_id, info in data.items():
            if not isinstance(info, dict):
                continue
            title = info.get("title") or info.get("name") or game_id
            install_path = info.get("install_path") or info.get("path") or ""
            try:
                size = int(info.get("install_size") or info.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            packages.append(Package(
                id=game_id,
                name=title,
                version="",
                size=size,
                description="",
                source=self.id,
                publisher="GOG (Heroic)",
                last_used=_approx_last_used(install_path) if install_path else None,
                install_path=install_path,
                install_date=_approx_last_used(install_path) if install_path else None,
            ))
        return packages

    def remove_argv(self, ids: list[str]) -> list[str]:
        return ["true"]  # remove() gogdl'de uninstall olmadığı için elle siler

    def remove(self, ids: list[str]) -> RemoveResult:
        # gogdl'de uninstall alt komutu yok (doğrulandı) — kurulum
        # klasörünü elle sil, yerel install-info kaydını temizle.
        info_path = _config_path("store_cache", "gog_install_info.json")
        data = _load_json(info_path) or {}
        failed = []
        messages = []
        for game_id in ids:
            entry = data.get(game_id) if isinstance(data, dict) else None
            install_path = None
            if isinstance(entry, dict):
                install_path = entry.get("install_path") or entry.get("path")
            try:
                if install_path and os.path.isdir(install_path):
                    shutil.rmtree(install_path)
                if isinstance(data, dict) and game_id in data:
                    del data[game_id]
                remove_matching_shortcuts(game_id)
            except OSError as exc:
                failed.append(game_id)
                messages.append(f"{game_id}: {exc}")

        if isinstance(data, dict):
            try:
                with open(info_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
            except OSError:
                pass  # oyun klasörü zaten silindi; kayıt temizliği ikincil

        if failed:
            return RemoveResult(False, "\n".join(messages), failed_ids=failed)
        return RemoveResult(True, "")
