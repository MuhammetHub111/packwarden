"""leftovers.py: PackWarden'ın kendi kaynak klasörünün ("packwarden"
kaynağı) genel ad-eşleştirmeli artık dosya taramasından hariç tutulması.

Gerçek `install.sh`/kaldırma betiği HİÇ çalıştırılmaz - yalnızca
`find_package_leftovers()` (salt okunur bulma) test edilir, hiçbir
dosya silinmez. `os.path.expanduser` sahte bir ev dizinine
yönlendirilir, gerçek `~/.local/share` asla taranmaz.

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


def _packwarden_pkg(install_dir):
    return Package(
        id=install_dir, name="PackWarden", version="1.0", size=0,
        description="", source="packwarden", install_path=install_dir,
    )


def _ordinary_pkg(name):
    return Package(
        id=name, name=name, version="1.0", size=0,
        description="", source="pacman",
    )


class SelfInstallExclusionTests(unittest.TestCase):
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

    def test_own_source_folder_is_not_listed_as_a_leftover(self):
        # ~/.local/share/packwarden - uygulamanın TÜM kaynak kodu.
        # install.sh remove zaten bunu koşulsuz siliyor - genel tarama
        # bunu AYRICA bir "kalıntı" gibi göstermemeli.
        install_dir = os.path.join(self.home, ".local", "share", "packwarden")
        os.makedirs(install_dir, exist_ok=True)
        with open(os.path.join(install_dir, "run.sh"), "w") as f:
            f.write("#!/bin/sh\n")

        groups = leftovers.find_package_leftovers([_packwarden_pkg(install_dir)])
        all_paths = [item.path for _cat, items in groups for item in items]
        self.assertNotIn(install_dir, all_paths)

    def test_no_leftover_categories_at_all_for_a_lone_packwarden_package(self):
        install_dir = os.path.join(self.home, ".local", "share", "packwarden")
        os.makedirs(install_dir, exist_ok=True)
        groups = leftovers.find_package_leftovers([_packwarden_pkg(install_dir)])
        self.assertEqual(groups, [])

    def test_ordinary_package_matching_is_unaffected(self):
        # Gerileme kontrolü: packwarden'a özgü hariç tutma, SIRADAN bir
        # paketin kendi gerçek artık klasörünü bulma yeteneğini
        # bozmamalı.
        config_dir = os.path.join(self.home, ".config", "firefox")
        os.makedirs(config_dir, exist_ok=True)
        groups = leftovers.find_package_leftovers([_ordinary_pkg("firefox")])
        categories = dict(groups)
        self.assertIn("Settings", categories)
        found_paths = [item.path for item in categories["Settings"]]
        self.assertEqual(found_paths, [config_dir])

    def test_packwarden_and_ordinary_package_together_only_ordinary_matches(self):
        install_dir = os.path.join(self.home, ".local", "share", "packwarden")
        os.makedirs(install_dir, exist_ok=True)
        config_dir = os.path.join(self.home, ".config", "firefox")
        os.makedirs(config_dir, exist_ok=True)

        groups = leftovers.find_package_leftovers(
            [_packwarden_pkg(install_dir), _ordinary_pkg("firefox")]
        )
        all_paths = [item.path for _cat, items in groups for item in items]
        self.assertNotIn(install_dir, all_paths)
        self.assertIn(config_dir, all_paths)

    def test_a_real_folder_literally_named_packwarden_elsewhere_is_unaffected(self):
        # Hariç tutma yalnızca pkg.source == "packwarden" paketi İÇİN
        # geçerli - "packwarden" adına normalize olan BAŞKA bir
        # paketin (varsayımsal) kendi taraması etkilenmemeli. Burada
        # doğrudan _normalize mantığını değil, dışlamanın yalnızca
        # doğru pakete uygulandığını doğruluyoruz.
        other_named_packwarden = Package(
            id="packwarden-clone", name="PackWarden Clone", version="1.0",
            size=0, description="", source="pacman",
        )
        clone_dir = os.path.join(self.home, ".local", "share", "packwardenclone")
        os.makedirs(clone_dir, exist_ok=True)
        groups = leftovers.find_package_leftovers([other_named_packwarden])
        categories = dict(groups)
        self.assertIn("App data", categories)
        found_paths = [item.path for item in categories["App data"]]
        self.assertEqual(found_paths, [clone_dir])


if __name__ == "__main__":
    unittest.main()
