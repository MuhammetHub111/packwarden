"""leftovers.py: Wine programlarının KDE/GNOME uygulama MENÜSÜNDEKİ
(~/.local/share/applications/wine/Programs/) kısayollarının da kalıntı
olarak tespit edilmesi.

Canlı doğrulanan gerçek açık: PackWarden şimdiye kadar yalnızca
masaüstündeki (~/Desktop) Wine kısayollarını tarıyordu - winemenubuilder'ın
asıl ürettiği, KDE Plasma'nın "Uygulamalar" menüsünde/KRunner aramasında
(Super tuşu) görünen klasör olan applications/wine/Programs/ hiç
taranmıyordu. Gerçek sistemde, registry'den çoktan kaldırılmış bir
foobar2000 kurulumunun .desktop dosyası hâlâ orada duruyordu VE KDE'nin
gerçek uygulama arama panelinde hâlâ görünüp tıklanabiliyordu (bkz.
leftovers.py: _find_wine_shortcut_leftovers'ın güncel docstring'i).

Gerçek paket kaldırma veya gerçek kullanıcı verisi/menüsü YAPILMAZ.
`os.path.expanduser` sahte bir ev dizinine yönlendirilir (gerçek
~/.local/share/applications/wine/Programs'a asla dokunulmaz);
`backends.wine.resolved_exe_path`/`exec_basename` taklit edilir -
gerçek bir Wine prefix'i/registry'si hiç gerekmez.

Çalıştırma:
    PYTHONPATH=src python3 -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bulkuninstaller import leftovers  # noqa: E402
from bulkuninstaller.backends.base import Package  # noqa: E402

_real_expanduser = os.path.expanduser


def _fake_expanduser(fake_home):
    def expand(path):
        if path == "~":
            return fake_home
        if path.startswith("~/"):
            return os.path.join(fake_home, path[2:])
        return _real_expanduser(path)
    return expand


def _wine_pkg(prefix, key, name):
    return Package(
        id=f"{prefix}::{key}", name=name, version="1.0", size=0,
        description="", source="wine",
    )


_DESKTOP_TEMPLATE = (
    "[Desktop Entry]\n"
    "Type=Application\n"
    "Name={name}\n"
    "Exec=env WINEPREFIX=/home/x/.wine wine \"{exec_path}\"\n"
)


def _write_desktop(path, name, exec_path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(_DESKTOP_TEMPLATE.format(name=name, exec_path=exec_path))


class WineMenuShortcutDetectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        self.patcher = mock.patch(
            "os.path.expanduser", side_effect=_fake_expanduser(self.home)
        )
        self.patcher.start()
        self.menu_dir = os.path.join(
            self.home, ".local", "share", "applications", "wine", "Programs"
        )

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def _resolve_patches(self, basename_by_id=None, exe_path_by_id=None):
        basename_by_id = basename_by_id or {}
        exe_path_by_id = exe_path_by_id or {}
        return [
            mock.patch(
                "bulkuninstaller.backends.wine.exec_basename",
                side_effect=lambda pkg_id: basename_by_id.get(pkg_id),
            ),
            mock.patch(
                "bulkuninstaller.backends.wine.resolved_exe_path",
                side_effect=lambda pkg_id: exe_path_by_id.get(pkg_id),
            ),
        ]

    def test_a_top_level_menu_shortcut_for_an_already_removed_package_is_found(self):
        # Gercek senaryo: foobar2000 registry'den cikti ama .desktop'u
        # applications/wine/Programs'ta top-level olarak kaldi.
        _write_desktop(
            os.path.join(self.menu_dir, "foobar2000.desktop"),
            "foobar2000", "C:\\Program Files\\foobar2000\\foobar2000.exe",
        )
        pkg = _wine_pkg("/home/x/.wine", "foobar2000key", "foobar2000")
        patches = self._resolve_patches(
            basename_by_id={pkg.id: "foobar2000.exe"},
        )
        for p in patches:
            p.start()
        try:
            groups = leftovers.find_package_leftovers([pkg])
        finally:
            for p in patches:
                p.stop()
        categories = dict(groups)
        self.assertIn("Shortcut", categories)
        found = [item.path for item in categories["Shortcut"]]
        self.assertEqual(
            found, [os.path.join(self.menu_dir, "foobar2000.desktop")]
        )

    def test_all_four_launcher_files_of_a_nested_program_are_found(self):
        # Gercek Total Commander senaryosu: TEK registry kaydi, DORT
        # ayri .desktop dosyasi, kendi alt klasorunde (nested).
        folder = os.path.join(self.menu_dir, "Total Commander")
        names = [
            "Total Commander.desktop",
            "Total Commander 64 bit.desktop",
            "Total Commander Help.desktop",
            "Uninstall or Repair Total Commander.desktop",
        ]
        for name in names:
            _write_desktop(
                os.path.join(folder, name), name,
                "C:\\Program Files\\totalcmd\\totalcmd64.exe",
            )
        pkg = _wine_pkg("/home/x/.wine", "tckey", "Total Commander")
        patches = self._resolve_patches(
            basename_by_id={pkg.id: "totalcmd64.exe"},
            exe_path_by_id={
                pkg.id: "/home/x/.wine/drive_c/Program Files/totalcmd/totalcmd64.exe"
            },
        )
        for p in patches:
            p.start()
        try:
            groups = leftovers.find_package_leftovers([pkg])
        finally:
            for p in patches:
                p.stop()
        categories = dict(groups)
        found = {os.path.basename(item.path) for item in categories["Shortcut"]}
        self.assertEqual(found, set(names))

    def test_unrelated_wine_programs_shortcuts_are_never_touched(self):
        # SteelSeries GG baska bir paket - Total Commander kaldirilirken
        # ona hic dokunulmamali.
        folder = os.path.join(self.menu_dir, "Total Commander")
        _write_desktop(
            os.path.join(folder, "Total Commander.desktop"),
            "Total Commander", "C:\\Program Files\\totalcmd\\totalcmd64.exe",
        )
        other_folder = os.path.join(self.menu_dir, "SteelSeries", "SteelSeries GG")
        _write_desktop(
            os.path.join(other_folder, "SteelSeries GG.desktop"),
            "SteelSeries GG", "C:\\Program Files\\SteelSeries\\GG\\SteelSeriesGG.exe",
        )
        pkg = _wine_pkg("/home/x/.wine", "tckey", "Total Commander")
        patches = self._resolve_patches(
            basename_by_id={pkg.id: "totalcmd64.exe"},
            exe_path_by_id={
                pkg.id: "/home/x/.wine/drive_c/Program Files/totalcmd/totalcmd64.exe"
            },
        )
        for p in patches:
            p.start()
        try:
            groups = leftovers.find_package_leftovers([pkg])
        finally:
            for p in patches:
                p.stop()
        categories = dict(groups)
        found = [item.path for item in categories["Shortcut"]]
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].endswith("Total Commander.desktop"))
        # SteelSeries GG'nin dosyasi diskte hala duruyor olmali - hic
        # ele alinmadi, silinmedi.
        self.assertTrue(
            os.path.isfile(os.path.join(other_folder, "SteelSeries GG.desktop"))
        )

    def test_no_match_returns_no_shortcut_category(self):
        _write_desktop(
            os.path.join(self.menu_dir, "SomeOtherApp.desktop"),
            "SomeOtherApp", "C:\\Program Files\\other\\other.exe",
        )
        pkg = _wine_pkg("/home/x/.wine", "tckey", "Total Commander")
        patches = self._resolve_patches(
            basename_by_id={pkg.id: "totalcmd64.exe"},
        )
        for p in patches:
            p.start()
        try:
            groups = leftovers.find_package_leftovers([pkg])
        finally:
            for p in patches:
                p.stop()
        self.assertNotIn("Shortcut", dict(groups))

    def test_desktop_dir_shortcut_and_menu_shortcut_are_both_found_together(self):
        # Gerileme kontrolü: yeni menu taramasi, eski masaustu taramasini
        # bozmamali - ikisi de ayni calistirmada bulunabilmeli.
        _write_desktop(
            os.path.join(self.menu_dir, "foobar2000.desktop"),
            "foobar2000", "C:\\Program Files\\foobar2000\\foobar2000.exe",
        )
        _write_desktop(
            os.path.join(self.home, "Desktop", "foobar2000.desktop"),
            "foobar2000", "C:\\Program Files\\foobar2000\\foobar2000.exe",
        )
        pkg = _wine_pkg("/home/x/.wine", "foobar2000key", "foobar2000")
        patches = self._resolve_patches(
            basename_by_id={pkg.id: "foobar2000.exe"},
        )
        for p in patches:
            p.start()
        try:
            groups = leftovers.find_package_leftovers([pkg])
        finally:
            for p in patches:
                p.stop()
        found = {item.path for item in dict(groups)["Shortcut"]}
        self.assertEqual(
            found,
            {
                os.path.join(self.menu_dir, "foobar2000.desktop"),
                os.path.join(self.home, "Desktop", "foobar2000.desktop"),
            },
        )


class RealWorldLnkStyleExecLineTests(unittest.TestCase):
    """Gerçek winemenubuilder çıktısının Exec= satırı ne exe adını ne
    kurulum klasörünü taşır - SADECE bir Windows .lnk yolunu taşır
    (canlı doğrulandı, gerçek sistemdeki Total Commander/foobar2000
    kurulumlarıyla). _exec_matches tek başına bu durumda HİÇ eşleşmez
    - grup klasörü/Name= tabanlı eşleştirme burada devreye girmeli."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        self.patcher = mock.patch(
            "os.path.expanduser", side_effect=_fake_expanduser(self.home)
        )
        self.patcher.start()
        self.menu_dir = os.path.join(
            self.home, ".local", "share", "applications", "wine", "Programs"
        )

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def _write_lnk_style_desktop(self, path, name, lnk_windows_path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(
                "[Desktop Entry]\n"
                "Type=Application\n"
                f"Name={name}\n"
                f'Exec=env "WINEPREFIX=/home/x/.wine" wine "{lnk_windows_path}"\n'
            )

    def test_nested_lnk_style_files_are_matched_by_group_folder_name(self):
        folder = os.path.join(self.menu_dir, "Total Commander")
        cases = [
            ("Total Commander.desktop", "Total Commander",
             r"C:\\users\\x\\AppData\\Roaming\\Microsoft\\Windows\\Start Menu\\Programs\\Total Commander\\Total Commander.lnk"),
            ("Total Commander 64 bit.desktop", "Total Commander 64 bit",
             r"C:\\users\\x\\AppData\\Roaming\\Microsoft\\Windows\\Start Menu\\Programs\\Total Commander\\Total Commander 64 bit.lnk"),
            ("Total Commander Help.desktop", "Total Commander Help",
             r"C:\\users\\x\\AppData\\Roaming\\Microsoft\\Windows\\Start Menu\\Programs\\Total Commander\\Total Commander Help.lnk"),
            ("Uninstall or Repair Total Commander.desktop", "Uninstall or Repair Total Commander",
             r"C:\\users\\x\\AppData\\Roaming\\Microsoft\\Windows\\Start Menu\\Programs\\Total Commander\\Uninstall or Repair Total Commander.lnk"),
        ]
        for filename, name, lnk in cases:
            self._write_lnk_style_desktop(os.path.join(folder, filename), name, lnk)

        # exec_basename/resolved_exe_path GERCEKTE donse bile (ornegin
        # "totalcmd64.exe") bu metinlerin HICBIRINDE gecmiyor - sadece
        # pkg.name ("Total Commander") == grup klasoru adi eslesmesiyle
        # bulunabilmeli.
        pkg = _wine_pkg("/home/x/.wine", "tckey", "Total Commander")
        patches = [
            mock.patch(
                "bulkuninstaller.backends.wine.exec_basename",
                side_effect=lambda pkg_id: "totalcmd64.exe",
            ),
            mock.patch(
                "bulkuninstaller.backends.wine.resolved_exe_path",
                side_effect=lambda pkg_id:
                    "/home/x/.wine/drive_c/Program Files/totalcmd/totalcmd64.exe",
            ),
        ]
        for p in patches:
            p.start()
        try:
            groups = leftovers.find_package_leftovers([pkg])
        finally:
            for p in patches:
                p.stop()
        found = {os.path.basename(item.path) for item in dict(groups)["Shortcut"]}
        self.assertEqual(found, {c[0] for c in cases})

    def test_top_level_lnk_style_file_is_matched_by_its_own_name_field(self):
        self._write_lnk_style_desktop(
            os.path.join(self.menu_dir, "foobar2000.desktop"),
            "foobar2000",
            r"C:\\ProgramData\\Microsoft\\Windows\\Start Menu\\Programs\\foobar2000.lnk",
        )
        pkg = _wine_pkg("/home/x/.wine", "foobarkey", "foobar2000")
        patches = [
            mock.patch(
                "bulkuninstaller.backends.wine.exec_basename",
                side_effect=lambda pkg_id: None,
            ),
            mock.patch(
                "bulkuninstaller.backends.wine.resolved_exe_path",
                side_effect=lambda pkg_id: None,
            ),
        ]
        for p in patches:
            p.start()
        try:
            groups = leftovers.find_package_leftovers([pkg])
        finally:
            for p in patches:
                p.stop()
        found = [item.path for item in dict(groups)["Shortcut"]]
        self.assertEqual(
            found, [os.path.join(self.menu_dir, "foobar2000.desktop")]
        )

    def test_unrelated_top_level_lnk_style_file_is_not_matched(self):
        self._write_lnk_style_desktop(
            os.path.join(self.menu_dir, "Notepad++.desktop"),
            "Notepad++",
            r"C:\\ProgramData\\Microsoft\\Windows\\Start Menu\\Programs\\Notepad++.lnk",
        )
        pkg = _wine_pkg("/home/x/.wine", "foobarkey", "foobar2000")
        patches = [
            mock.patch(
                "bulkuninstaller.backends.wine.exec_basename",
                side_effect=lambda pkg_id: None,
            ),
            mock.patch(
                "bulkuninstaller.backends.wine.resolved_exe_path",
                side_effect=lambda pkg_id: None,
            ),
        ]
        for p in patches:
            p.start()
        try:
            groups = leftovers.find_package_leftovers([pkg])
        finally:
            for p in patches:
                p.stop()
        self.assertNotIn("Shortcut", dict(groups))
        self.assertTrue(
            os.path.isfile(os.path.join(self.menu_dir, "Notepad++.desktop"))
        )


class RemoveWineMenuShortcutLeftoversTests(unittest.TestCase):
    """Bulunan menü kısayolu, remove_leftovers() ile GERÇEKTEN (sahte
    ev dizininde) silinebiliyor mu?"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        self.patcher = mock.patch(
            "os.path.expanduser", side_effect=_fake_expanduser(self.home)
        )
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def test_menu_shortcut_is_permanently_deletable(self):
        path = os.path.join(
            self.home, ".local", "share", "applications", "wine", "Programs",
            "foobar2000.desktop",
        )
        _write_desktop(path, "foobar2000", "C:\\Program Files\\foobar2000\\foobar2000.exe")
        item = leftovers.Leftover(path=path, size=0)
        with mock.patch(
            "bulkuninstaller.leftovers.prefs.get", return_value="permanent"
        ):
            errors = leftovers.remove_leftovers([item])
        self.assertEqual(errors, [])
        self.assertFalse(os.path.exists(path))


class WineIconCacheLeftoverTests(unittest.TestCase):
    """leftovers.py: kaldırılan Wine paketlerinin çıkarılmış .exe
    ikonunun önbellek PNG'si (bkz. backends/wine.py icon_cache_path)
    de kalıntı olarak bulunuyor mu? Canlı doğrulandı: gerçek sistemde
    SteelSeries GG/Totalcmd64/foobar2000_x64 gibi registry'den çoktan
    kaldırılmış paketlerin ikon PNG'leri ~/.cache/bulkuninstaller/
    wine-icons/ altında hâlâ duruyordu."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        self.patcher = mock.patch(
            "os.path.expanduser", side_effect=_fake_expanduser(self.home)
        )
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def test_existing_icon_cache_file_is_found_as_a_leftover(self):
        from bulkuninstaller.backends import wine as wine_mod
        pkg = _wine_pkg("/home/x/.wine", "foobarkey", "foobar2000")
        cache_dir = os.path.join(self.home, ".cache", "bulkuninstaller", "wine-icons")
        os.makedirs(cache_dir, exist_ok=True)
        with mock.patch.object(wine_mod, "_ICON_CACHE_DIR", cache_dir):
            import re as _re
            cache_key = _re.sub(r"[^\w.-]+", "_", f"/home/x/.wine_foobarkey")
            icon_path = os.path.join(cache_dir, f"{cache_key}.png")
            with open(icon_path, "wb") as f:
                f.write(b"\x89PNG fake icon bytes")

            groups = leftovers.find_package_leftovers([pkg])
        categories = dict(groups)
        self.assertIn("Icon cache", categories)
        found = [item.path for item in categories["Icon cache"]]
        self.assertEqual(found, [icon_path])

    def test_missing_icon_cache_file_returns_nothing(self):
        pkg = _wine_pkg("/home/x/.wine", "foobarkey", "foobar2000")
        groups = leftovers.find_package_leftovers([pkg])
        self.assertNotIn("Icon cache", dict(groups))

    def test_another_packages_icon_is_never_matched(self):
        from bulkuninstaller.backends import wine as wine_mod
        pkg_a = _wine_pkg("/home/x/.wine", "akey", "App A")
        pkg_b = _wine_pkg("/home/x/.wine", "bkey", "App B")
        cache_dir = os.path.join(self.home, ".cache", "bulkuninstaller", "wine-icons")
        os.makedirs(cache_dir, exist_ok=True)
        with mock.patch.object(wine_mod, "_ICON_CACHE_DIR", cache_dir):
            import re as _re
            key_b = _re.sub(r"[^\w.-]+", "_", "/home/x/.wine_bkey")
            icon_b = os.path.join(cache_dir, f"{key_b}.png")
            with open(icon_b, "wb") as f:
                f.write(b"icon of B")

            # Sadece pkg_a kaldiriliyor - B'nin ikonuna dokunulmamali.
            groups = leftovers.find_package_leftovers([pkg_a])
        self.assertNotIn("Icon cache", dict(groups))
        self.assertTrue(os.path.isfile(icon_b))


if __name__ == "__main__":
    unittest.main()
