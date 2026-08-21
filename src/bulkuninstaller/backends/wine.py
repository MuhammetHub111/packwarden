"""Wine ile kurulmuş Windows programları.

Wine, kurulu Windows programlarının "Program Ekle/Kaldır" kaydını
Windows'un kendi biçiminde, kendi registry dosyasında (system.reg,
[Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\...] altında)
tutar — hiçbir Linux paket yöneticisi bundan haberdar değil, bu yüzden
diğer 11 kaynaktan hiçbiri bu programları göremiyordu.

Yalnızca varsayılan prefix (~/.wine) ve WINEPREFIX ortam değişkeni
ayarlıysa o taranıyor. Bottles/Lutris/PlayOnLinux gibi araçların kendi
ürettiği ayrı prefix'ler burada kapsanmıyor — her biri kendi konumuna
sahip, ayrı bir iş.

Kaldırma, kayıtlı UninstallString'i olduğu gibi çalıştırır — bu
Windows'un kendi kurulum programının kaldırma sihirbazı olabilir ve
etkileşim isteyebilir (gerçek Windows'ta da öyle çalışır); diğer
kaynaklardan farklı olarak "sessiz/etkileşimsiz" garantisi verilemez.
"""

import os
import re
import shutil
import tempfile
import time

from .. import host
from .base import Backend, Package, RemoveResult

_ICON_CACHE_DIR = os.path.expanduser("~/.cache/bulkuninstaller/wine-icons")
_ICON_SIZE_RE = re.compile(r"(\d+)x(\d+)")

_UNINSTALL_KEY_RE = re.compile(
    r'^\[Software\\\\(?:Wow6432Node\\\\)?Microsoft\\\\Windows\\\\'
    r'CurrentVersion\\\\Uninstall\\\\(.+)\]'
)
_STR_RE = re.compile(r'^"([^"]+)"=(?:str\(2\):)?"((?:[^"\\]|\\.)*)"$')
_DWORD_RE = re.compile(r'^"([^"]+)"=dword:([0-9a-fA-F]+)$')


def _unescape(value: str) -> str:
    return value.replace('\\"', '"').replace("\\\\", "\\")


def _prefixes() -> list[str]:
    found = []
    env_prefix = os.environ.get("WINEPREFIX")
    if env_prefix and os.path.isdir(env_prefix):
        found.append(os.path.realpath(env_prefix))
    default = os.path.expanduser("~/.wine")
    if os.path.isdir(default):
        real_default = os.path.realpath(default)
        if real_default not in found:
            found.append(real_default)
    return found


def _parse_uninstall_entries(reg_path: str) -> list[dict]:
    entries = []
    current = None
    try:
        with open(reg_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip("\n")
                if line.startswith("["):
                    if current is not None:
                        entries.append(current)
                        current = None
                    match = _UNINSTALL_KEY_RE.match(line)
                    if match:
                        current = {"_key": match.group(1)}
                    continue
                if current is None:
                    continue
                match = _STR_RE.match(line)
                if match:
                    current[match.group(1)] = _unescape(match.group(2))
                    continue
                match = _DWORD_RE.match(line)
                if match:
                    current[match.group(1)] = int(match.group(2), 16)
    except OSError:
        return []
    if current is not None:
        entries.append(current)
    return entries


def _install_date(raw) -> float | None:
    # Windows "InstallDate" YYYYMMDD dizesi olarak tutar
    if not isinstance(raw, str) or len(raw) != 8 or not raw.isdigit():
        return None
    try:
        return time.mktime(time.strptime(raw, "%Y%m%d"))
    except ValueError:
        return None


def _split_uninstall_string(raw: str) -> list[str]:
    """'"C:\\...\\uninst.exe" /S' -> ['C:\\...\\uninst.exe', '/S']."""
    raw = raw.strip()
    if raw.startswith('"'):
        end = raw.find('"', 1)
        if end != -1:
            exe = raw[1:end]
            rest = raw[end + 1:].strip()
            return [exe] + (rest.split() if rest else [])
    return raw.split()


def _windows_path_to_unix(prefix: str, windows_path: str) -> str | None:
    """'C:\\Program Files\\Foo\\bar.exe' -> '<prefix>/drive_c/Program Files/Foo/bar.exe'.

    Yalnızca C: sürücüsü destekleniyor — Wine prefix'lerinin neredeyse
    tamamında kurulumlar oraya gider, başka harfler (özel bağlanmış
    sürücüler) kapsanmıyor."""
    windows_path = windows_path.split(",")[0].strip('"').strip()
    if not windows_path.upper().startswith("C:"):
        return None
    rel = windows_path[2:].replace("\\", "/").lstrip("/")
    return os.path.join(prefix, "drive_c", rel)


def _extract_icon(exe_path: str, cache_key: str) -> str | None:
    """.exe içine gömülü ikonu yerel olarak çıkarır (ağa hiç çıkmaz);
    sonucu diske önbellekler. Araç kurulu değilse veya çıkarma
    başarısız olursa None döner — çağıran genel pakete geri düşer."""
    if not (host.command_exists("wrestool") and host.command_exists("icotool")):
        return None
    if not os.path.isfile(exe_path):
        return None
    out_png = os.path.join(_ICON_CACHE_DIR, f"{cache_key}.png")
    if os.path.isfile(out_png):
        return out_png
    try:
        os.makedirs(_ICON_CACHE_DIR, exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            ico_path = os.path.join(tmp, "icon.ico")
            extracted = host.run(
                ["wrestool", "-x", "-t", "14", "-o", ico_path, exe_path],
                timeout=15,
            )
            if extracted.returncode != 0 or not os.path.isfile(ico_path):
                return None
            converted = host.run(
                ["icotool", "-x", "-o", tmp, ico_path], timeout=15
            )
            if converted.returncode != 0:
                return None
            pngs = [f for f in os.listdir(tmp) if f.lower().endswith(".png")]
            if not pngs:
                return None

            def _icon_area(name: str) -> int:
                match = _ICON_SIZE_RE.search(name)
                if not match:
                    return 0
                return int(match.group(1)) * int(match.group(2))

            best = max(pngs, key=_icon_area)
            shutil.copyfile(os.path.join(tmp, best), out_png)
            return out_png
    except Exception:
        return None


class WineBackend(Backend):
    """Wine prefix'lerindeki Windows programları."""

    id = "wine"
    display_name = "Wine"
    needs_root = False

    def is_available(self) -> bool:
        return bool(_prefixes()) and host.command_exists("wine")

    def list_packages(self) -> list[Package]:
        packages = []
        default_prefix = os.path.realpath(os.path.expanduser("~/.wine"))
        for prefix in _prefixes():
            reg_path = os.path.join(prefix, "system.reg")
            for entry in _parse_uninstall_entries(reg_path):
                name = entry.get("DisplayName")
                if not name:
                    continue
                # Gerçek Windows'un "Program Ekle/Kaldır" listesi de bu
                # ikisini gizler: alt bileşenler ve sistem parçaları.
                if entry.get("SystemComponent") == 1:
                    continue
                if "ParentKeyName" in entry:
                    continue
                if not entry.get("UninstallString"):
                    continue
                size_kb = entry.get("EstimatedSize")
                icon_path = ""
                display_icon = entry.get("DisplayIcon")
                if display_icon:
                    exe_path = _windows_path_to_unix(prefix, display_icon)
                    if exe_path:
                        cache_key = re.sub(r"[^\w.-]+", "_", f"{prefix}_{entry['_key']}")
                        icon_path = _extract_icon(exe_path, cache_key) or ""
                packages.append(Package(
                    id=f"{prefix}::{entry['_key']}",
                    name=name,
                    version=entry.get("DisplayVersion", ""),
                    size=(size_kb * 1024) if isinstance(size_kb, int) else 0,
                    description="",
                    source=self.id,
                    publisher=entry.get("Publisher", ""),
                    origin="" if prefix == default_prefix else os.path.basename(prefix),
                    install_date=_install_date(entry.get("InstallDate")),
                    install_reason="explicit",
                    icon_path=icon_path,
                ))
        return packages

    def _find_entry(self, prefix: str, key: str) -> dict | None:
        reg_path = os.path.join(prefix, "system.reg")
        for entry in _parse_uninstall_entries(reg_path):
            if entry.get("_key") == key:
                return entry
        return None

    def _wine_argv(self, prefix: str, uninstall_string: str) -> list[str]:
        return (
            ["env", f"WINEPREFIX={prefix}", "wine"]
            + _split_uninstall_string(uninstall_string)
        )

    def remove_argv(self, ids: list[str]) -> list[str]:
        # ABC sözleşmesini karşılamak için basit bir yedek — Backend.remove()
        # birden çok id'yi TEK bir komuta katlamayı bekliyor, ama her Wine
        # programının kendi ayrı kaldırıcısı var. Asıl akış remove()'da
        # geçersiz kılınıyor (her id ayrı ayrı çalıştırılıyor); bu yalnızca
        # tek-id durumunu (veya remove() hiç çağrılmazsa) karşılar.
        if not ids:
            return ["true"]
        prefix, _sep, key = ids[0].partition("::")
        entry = self._find_entry(prefix, key)
        if not entry or not entry.get("UninstallString"):
            return ["true"]
        return self._wine_argv(prefix, entry["UninstallString"])

    def remove(self, ids: list[str]) -> RemoveResult:
        outputs = []
        failed = []
        for pkg_id in ids:
            prefix, sep, key = pkg_id.partition("::")
            if not sep:
                failed.append(pkg_id)
                continue
            entry = self._find_entry(prefix, key)
            if not entry or not entry.get("UninstallString"):
                failed.append(pkg_id)
                continue
            argv = self._wine_argv(prefix, entry["UninstallString"])
            try:
                proc = host.run(argv, timeout=600)
            except Exception as exc:
                outputs.append(f"{key}: {exc}")
                failed.append(pkg_id)
                continue
            outputs.append((proc.stdout or "") + (proc.stderr or ""))
            if proc.returncode != 0:
                failed.append(pkg_id)
        return RemoveResult(ok=not failed, output="\n".join(outputs), failed_ids=failed)
