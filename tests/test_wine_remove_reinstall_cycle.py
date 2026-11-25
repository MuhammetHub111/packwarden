"""leftovers.py + backends/wine.py: bir Wine paketi kaldırıldıktan
sonra AYNI uygulama yeniden kurulursa, eski kaldırma işleminin yeni
kuruluma ait kısayolu/ikonunu YANLIŞLIKLA engellemediğini/silmediğini
doğrular.

Bu dosyanın VAR OLMA SEBEBİ: gerçek, izole bir sahte HOME + geçici
WINEPREFIX'te, gerçek `npp.8.9.9.1.Installer.x64.exe` ile CANLI olarak
2 kez tekrarlanan tam kaldır→yeniden-kur döngüsü test edildi (bu
oturumun interaktif doğrulaması) - PackWarden'ın kodunda hiçbir
"kalıcı engel" bulunamadı: her seferinde temiz şekilde kaldırıldı,
her seferinde yeniden kurulumda kısayol doğru şekilde yeniden
oluştu. Bu testler o canlı doğrulamanın HIZLI, CI'da güvenle
tekrarlanabilir, sentetik (gerçek installer/Wine gerektirmeyen) hâli.

Gerçek paket kaldırma/kurulum veya gerçek kullanıcı dizinleri
YAPILMAZ - `os.path.expanduser` sahte bir ev dizinine yönlendirilir.

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


def _write_desktop(path, name, lnk_name):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(
            "[Desktop Entry]\nType=Application\n"
            f"Name={name}\n"
            f'Exec=env "WINEPREFIX=/home/x/.wine" wine '
            f'"C:\\\\ProgramData\\\\Microsoft\\\\Windows\\\\Start Menu\\\\Programs\\\\{lnk_name}.lnk"\n'
        )


def _wine_pkg(prefix, key, name):
    return Package(
        id=f"{prefix}::{key}", name=name, version="1.0", size=0,
        description="", source="wine",
    )


class RemoveThenReinstallDoesNotBlockTheNewShortcutTests(unittest.TestCase):
    """Kaldırma -> aynı uygulamanın yeniden kurulması -> yeni kısayol
    hiçbir ek müdahaleye gerek kalmadan tekrar bulunabilir olmalı."""

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
        self.desktop_path = os.path.join(self.menu_dir, "foobar2000.desktop")

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def _resolve_patches(self, pkg_id, basename, exe_path):
        return [
            mock.patch(
                "bulkuninstaller.backends.wine.exec_basename",
                side_effect=lambda pid: basename if pid == pkg_id else None,
            ),
            mock.patch(
                "bulkuninstaller.backends.wine.resolved_exe_path",
                side_effect=lambda pid: exe_path if pid == pkg_id else None,
            ),
        ]

    def test_fresh_shortcut_after_reinstall_is_not_flagged_as_a_leftover_of_the_old_removal(self):
        pkg = _wine_pkg("/home/x/.wine", "foobarkey", "foobar2000")
        _write_desktop(self.desktop_path, "foobar2000", "foobar2000")

        patches = self._resolve_patches(
            pkg.id, "foobar2000.exe",
            "/home/x/.wine/drive_c/Program Files/foobar2000/foobar2000.exe",
        )
        for p in patches:
            p.start()
        try:
            # 1) Kaldirma oncesi tarama (gercek RemovalWindow siralamasiyla
            # AYNI - once tara, SONRA kaldir).
            groups = leftovers.find_package_leftovers([pkg])
            flat = [item for _c, items in groups for item in items]
            self.assertTrue(
                any(item.path == self.desktop_path for item in flat)
            )
            # 2) "Kaldirma" simule edilir: kalinti silinir (gercek
            # remove_leftovers() cagrisi, sahte ev dizininde). Kalici
            # silme modu zorlanir - /tmp altindaki sahte ev dizini
            # gercek "cop kutusu" (gio trash) icin uygun bir points
            # degil (ayri bir dosya sistemi/baglama noktasi olabilir).
            with mock.patch(
                "bulkuninstaller.leftovers.prefs.get", return_value="permanent"
            ):
                errors = leftovers.remove_leftovers(flat)
            self.assertEqual(errors, [])
            self.assertFalse(os.path.exists(self.desktop_path))
        finally:
            for p in patches:
                p.stop()

        # 3) "Yeniden kurulum" simule edilir: winemenubuilder'in
        # yapacagi gibi AYNI isimle yeni bir .desktop yazilir - YENI
        # bir paket/registry kaydi olarak.
        new_pkg = _wine_pkg("/home/x/.wine", "foobarkey2", "foobar2000")
        _write_desktop(self.desktop_path, "foobar2000", "foobar2000")
        self.assertTrue(os.path.isfile(self.desktop_path))

        # 4) Yeni kurulum GERCEKTEN tekrar bulunabiliyor mu - baska bir
        # paketin (alakasiz) kaldirilmasi bu YENI dosyaya dokunmamali.
        unrelated_pkg = _wine_pkg("/home/x/.wine", "other", "SomeOtherApp")
        other_patches = [
            mock.patch(
                "bulkuninstaller.backends.wine.exec_basename",
                return_value=None,
            ),
            mock.patch(
                "bulkuninstaller.backends.wine.resolved_exe_path",
                return_value=None,
            ),
        ]
        for p in other_patches:
            p.start()
        try:
            groups2 = leftovers.find_package_leftovers([unrelated_pkg])
            self.assertNotIn("Shortcut", dict(groups2))
        finally:
            for p in other_patches:
                p.stop()
        self.assertTrue(
            os.path.isfile(self.desktop_path),
            "Yeniden kurulan kisayol, alakasiz bir kaldirma isleminden "
            "etkilenmemeli",
        )

        # 5) Ve nihayet: YENI paket icin dogru sekilde tekrar bir
        # kalinti olarak (gercekten kaldirilmak istenirse) bulunabiliyor.
        new_patches = self._resolve_patches(
            new_pkg.id, "foobar2000.exe",
            "/home/x/.wine/drive_c/Program Files/foobar2000/foobar2000.exe",
        )
        for p in new_patches:
            p.start()
        try:
            groups3 = leftovers.find_package_leftovers([new_pkg])
            flat3 = [item for _c, items in groups3 for item in items]
            self.assertTrue(any(item.path == self.desktop_path for item in flat3))
        finally:
            for p in new_patches:
                p.stop()

    def test_removal_writes_no_persistent_marker_anywhere_in_the_home_directory(self):
        # Kaldirma islemi sonrasi sahte ev dizininde, kaldirilan .desktop
        # haricinde HICBIR yeni dosya/marker olusturulmamali - "bir daha
        # kurulamasin" turunden gizli bir engel mekanizmasi yok.
        pkg = _wine_pkg("/home/x/.wine", "foobarkey", "foobar2000")
        _write_desktop(self.desktop_path, "foobar2000", "foobar2000")

        before = set()
        for root, _dirs, files in os.walk(self.home):
            for f in files:
                before.add(os.path.join(root, f))

        patches = self._resolve_patches(
            pkg.id, "foobar2000.exe",
            "/home/x/.wine/drive_c/Program Files/foobar2000/foobar2000.exe",
        )
        for p in patches:
            p.start()
        try:
            groups = leftovers.find_package_leftovers([pkg])
            flat = [item for _c, items in groups for item in items]
            with mock.patch(
                "bulkuninstaller.leftovers.prefs.get", return_value="permanent"
            ):
                errors = leftovers.remove_leftovers(flat)
            self.assertEqual(errors, [])
        finally:
            for p in patches:
                p.stop()

        after = set()
        for root, _dirs, files in os.walk(self.home):
            for f in files:
                after.add(os.path.join(root, f))

        new_files = after - before
        self.assertEqual(
            new_files, set(),
            f"Kaldırma, beklenmeyen yeni dosyalar bıraktı: {new_files}",
        )


if __name__ == "__main__":
    unittest.main()
