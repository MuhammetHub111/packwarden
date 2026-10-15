"""Wine kaldırma güvenliği: `_is_safe_install_dir` ve `_force_remove`.

Hiç gerçek dosya/paket/registry'ye dokunmaz, yalnızca `tempfile` ile
oluşturulan geçici dizinler ve sahte registry verisiyle çalışır.

Çalıştırma:
    PYTHONPATH=src python3 -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bulkuninstaller.backends import wine  # noqa: E402


class IsSafeInstallDirTests(unittest.TestCase):
    """`_is_safe_install_dir()` - saf fonksiyon, gerçek dosya silmez."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.prefix = os.path.join(self.tmp.name, "wineprefix")
        self.drive_c = os.path.join(self.prefix, "drive_c")
        os.makedirs(os.path.join(self.drive_c, "Program Files", "Foo"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_install_dir_is_safe(self):
        target = os.path.join(self.drive_c, "Program Files", "Foo")
        self.assertTrue(wine._is_safe_install_dir(self.prefix, target))

    def test_dotdot_escaping_prefix_is_rejected(self):
        outside = os.path.join(self.tmp.name, "outside")
        os.makedirs(outside)
        # drive_c/Program Files/Foo/../../../outside -> tmp/outside
        traversal = os.path.join(
            self.drive_c, "Program Files", "Foo", "..", "..", "..", "outside"
        )
        self.assertFalse(wine._is_safe_install_dir(self.prefix, traversal))

    def test_root_directory_is_rejected(self):
        self.assertFalse(wine._is_safe_install_dir(self.prefix, "/"))

    def test_home_directory_is_rejected(self):
        self.assertFalse(
            wine._is_safe_install_dir(self.prefix, os.path.expanduser("~"))
        )

    def test_symlink_escaping_prefix_is_rejected(self):
        outside = os.path.join(self.tmp.name, "outside_target")
        os.makedirs(outside)
        link = os.path.join(self.drive_c, "Program Files", "evil_link")
        os.symlink(outside, link)
        self.assertFalse(wine._is_safe_install_dir(self.prefix, link))

    def test_similarly_named_sibling_prefix_is_rejected(self):
        # "/.../wineprefix-other" metinsel olarak "/.../wineprefix" ile
        # BAŞLAR ama tamamen farklı bir dizin - ayırıcı dahil
        # karşılaştırılmazsa yanlışlıkla "içeride" sayılabilirdi.
        sibling_dir = os.path.join(
            self.prefix + "-other", "drive_c", "Game"
        )
        os.makedirs(sibling_dir)
        self.assertFalse(wine._is_safe_install_dir(self.prefix, sibling_dir))

    def test_empty_path_is_rejected(self):
        self.assertFalse(wine._is_safe_install_dir(self.prefix, ""))

    def test_prefix_itself_is_rejected(self):
        self.assertFalse(wine._is_safe_install_dir(self.prefix, self.prefix))

    def test_drive_c_itself_is_rejected(self):
        # InstallLocation="C:" gibi dejenere bir değer, TÜM drive_c'nin
        # (yani kurulu her Wine programının) silinmesine yol açmamalı.
        self.assertFalse(wine._is_safe_install_dir(self.prefix, self.drive_c))

    def test_nonexistent_but_contained_path_is_still_safe(self):
        # Henüz oluşturulmamış ama prefix altında kalan bir yol -
        # os.path.isdir() ayrı bir kontrol, bu fonksiyon yalnızca
        # "konum güvenli mi" sorusuna bakar.
        target = os.path.join(self.drive_c, "Program Files", "NeverCreated")
        self.assertTrue(wine._is_safe_install_dir(self.prefix, target))


class ForceRemoveSafetyTests(unittest.TestCase):
    """`_force_remove()` - gerçek çağrı yerini, shutil.rmtree'nin
    GERÇEKTEN çağrılıp çağrılmadığını temel alarak doğrular."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.prefix = os.path.join(self.tmp.name, "wineprefix")
        self.drive_c = os.path.join(self.prefix, "drive_c")
        os.makedirs(self.drive_c)
        self.outside = os.path.join(self.tmp.name, "outside_victim")
        os.makedirs(self.outside)
        self.sentinel = os.path.join(self.outside, "sentinel.txt")
        with open(self.sentinel, "w", encoding="utf-8") as f:
            f.write("do not delete")

    def tearDown(self):
        self.tmp.cleanup()

    def test_unsafe_path_blocks_rmtree_and_warns(self):
        # drive_c/../../outside_victim -> tam olarak self.outside'a çıkar.
        entry = {
            "InstallLocation": r"C:\..\..\outside_victim",
            "_reg_path": (
                "Software\\Microsoft\\Windows\\CurrentVersion\\"
                "Uninstall\\Fake"
            ),
        }
        warnings: list[str] = []
        with mock.patch("bulkuninstaller.backends.wine.host.run") as run_mock:
            run_mock.return_value = mock.Mock(returncode=0)
            wine._force_remove(self.prefix, entry, warnings)
        self.assertTrue(
            os.path.isfile(self.sentinel),
            "prefix dışındaki victim dizini silinmemeli",
        )
        self.assertTrue(
            warnings, "güvensiz yol için anlaşılır bir uyarı üretilmeli"
        )

    def test_safe_path_is_actually_removed(self):
        target = os.path.join(self.drive_c, "Program Files", "RealApp")
        os.makedirs(target)
        with open(os.path.join(target, "file.txt"), "w", encoding="utf-8") as f:
            f.write("x")
        entry = {
            "InstallLocation": r"C:\Program Files\RealApp",
            "_reg_path": (
                "Software\\Microsoft\\Windows\\CurrentVersion\\"
                "Uninstall\\Real"
            ),
        }
        with mock.patch("bulkuninstaller.backends.wine.host.run") as run_mock:
            run_mock.return_value = mock.Mock(returncode=0)
            wine._force_remove(self.prefix, entry)
        self.assertFalse(
            os.path.isdir(target), "geçerli kurulum klasörü silinmeliydi"
        )

    def test_missing_install_location_does_not_crash(self):
        entry = {
            "_reg_path": (
                "Software\\Microsoft\\Windows\\CurrentVersion\\"
                "Uninstall\\NoLocation"
            ),
        }
        with mock.patch("bulkuninstaller.backends.wine.host.run") as run_mock:
            run_mock.return_value = mock.Mock(returncode=0)
            wine._force_remove(self.prefix, entry)  # exception atmamalı


if __name__ == "__main__":
    unittest.main()
