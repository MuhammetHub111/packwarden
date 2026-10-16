"""backends/portage.py: SLOT okuma ve çakışan kimlik ayrıştırması.

Gerçek `emerge` HİÇ çalıştırılmaz - yalnızca `host.run` (ls -d çıktısı)
taklit edilir. Gerçek dosya okuma (SLOT, os.stat) GERÇEK geçici
dizinlerle yapılır (tempfile) - böylece `_read_slot()` ve dosya
hatalarının gerçek davranışı (mock değil) test edilir.

Bu makinede Gentoo/Portage kurulu değil - SLOT dosyasının VDB içindeki
konumu ve "category/name:slot" atom söz dizimi resmi Gentoo belgelerine
(bkz. inceleme raporu) dayanıyor, gerçek bir emerge/Portage kurulumuna
karşı canlı doğrulanmadı.

Çalıştırma:
    PYTHONPATH=src python3 -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bulkuninstaller.backends.portage import PortageBackend  # noqa: E402


class PortageTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _make_pkg_dir(self, category, pkgver, slot=None):
        path = os.path.join(self.tmp.name, category, pkgver)
        os.makedirs(path, exist_ok=True)
        if slot is not None:
            with open(os.path.join(path, "SLOT"), "w", encoding="utf-8") as f:
                f.write(slot)
        return path

    def _list_packages(self, dir_paths: list[str]) -> list:
        ls_output = "\n".join(dir_paths) + "\n"

        def fake_run(argv, timeout=None):
            if argv[:2] == ["sh", "-c"]:
                return mock.Mock(returncode=0, stdout=ls_output)
            return mock.Mock(returncode=1, stdout="")

        backend = PortageBackend()
        with mock.patch(
            "bulkuninstaller.backends.portage.host.run", side_effect=fake_run
        ):
            return backend.list_packages()


class SlotDisambiguationTests(PortageTestBase):
    def test_two_slots_get_distinct_ids_and_removal_targets(self):
        p78 = self._make_pkg_dir("dev-lang", "spidermonkey-78.15.0", slot="78")
        p115 = self._make_pkg_dir("dev-lang", "spidermonkey-115.16.0", slot="115")
        packages = self._list_packages([p78, p115])
        ids = sorted(pkg.id for pkg in packages)
        self.assertEqual(
            ids, ["dev-lang/spidermonkey:115", "dev-lang/spidermonkey:78"]
        )
        self.assertEqual(len(set(ids)), 2)

        backend = PortageBackend()
        by_id = {pkg.version: pkg.id for pkg in packages}
        argv_78 = backend.remove_argv([by_id["78.15.0"]])
        argv_115 = backend.remove_argv([by_id["115.16.0"]])
        # Bir slot seçildiğinde diğer slot argv'de hiç geçmemeli.
        self.assertNotIn("dev-lang/spidermonkey:115", argv_78)
        self.assertNotIn("dev-lang/spidermonkey:78", argv_115)

    def test_selecting_both_slots_targets_are_correctly_separated(self):
        p78 = self._make_pkg_dir("dev-lang", "spidermonkey-78.15.0", slot="78")
        p115 = self._make_pkg_dir("dev-lang", "spidermonkey-115.16.0", slot="115")
        packages = self._list_packages([p78, p115])
        ids = [pkg.id for pkg in packages]
        argv = PortageBackend().remove_argv(ids)
        self.assertIn("dev-lang/spidermonkey:78", argv)
        self.assertIn("dev-lang/spidermonkey:115", argv)
        # İkisi de AYRI argv öğeleri, tek bir dizeye katıştırılmamış.
        self.assertEqual(argv.count("dev-lang/spidermonkey:78"), 1)
        self.assertEqual(argv.count("dev-lang/spidermonkey:115"), 1)

    def test_three_slots_all_distinct(self):
        dirs = [
            self._make_pkg_dir("dev-lang", "foo-1.0", slot="0"),
            self._make_pkg_dir("dev-lang", "foo-2.0", slot="1"),
            self._make_pkg_dir("dev-lang", "foo-3.0", slot="2"),
        ]
        packages = self._list_packages(dirs)
        ids = sorted(pkg.id for pkg in packages)
        self.assertEqual(
            ids, ["dev-lang/foo:0", "dev-lang/foo:1", "dev-lang/foo:2"]
        )


class SingleSlotUnchangedTests(PortageTestBase):
    def test_ordinary_single_slot_package_id_is_unchanged(self):
        path = self._make_pkg_dir("app-editors", "vim-9.1.0", slot="0")
        pkg = self._list_packages([path])[0]
        self.assertEqual(pkg.id, "app-editors/vim")
        self.assertEqual(pkg.name, "app-editors/vim")
        self.assertEqual(pkg.version, "9.1.0")

    def test_single_package_with_no_slot_file_is_unchanged(self):
        # Çakışma yoksa SLOT hiç okunmaya çalışılmaz bile - tek
        # paketin id'si etkilenmez.
        path = self._make_pkg_dir("app-editors", "vim-9.1.0")  # SLOT yok
        pkg = self._list_packages([path])[0]
        self.assertEqual(pkg.id, "app-editors/vim")

    def test_unrelated_packages_different_names_not_merged(self):
        p1 = self._make_pkg_dir("app-editors", "vim-9.1.0", slot="0")
        p2 = self._make_pkg_dir("app-shells", "bash-5.2_p37", slot="0")
        packages = self._list_packages([p1, p2])
        ids = sorted(pkg.id for pkg in packages)
        self.assertEqual(ids, ["app-editors/vim", "app-shells/bash"])


class MissingOrDuplicateSlotTests(PortageTestBase):
    def test_missing_slot_file_on_collision_falls_back_to_bare_id(self):
        # Cakisma var ama SLOT dosyasi yok - uydurma bir "ad:" kimligi
        # URETILMEMELI, eski (cakisan ama gercek) hale dusulmeli.
        p1 = self._make_pkg_dir("dev-lang", "foo-1.0")  # SLOT yok
        p2 = self._make_pkg_dir("dev-lang", "foo-2.0", slot="1")
        packages = self._list_packages([p1, p2])
        ids = sorted(pkg.id for pkg in packages)
        # SLOT'u olan ayirt edildi, olmayan guvenli bicimde cak bos
        # kimlige dusmedi (":" ile bitmiyor), sadece eski cakisan
        # haline dustu.
        self.assertIn("dev-lang/foo:1", ids)
        self.assertIn("dev-lang/foo", ids)
        for id_ in ids:
            self.assertFalse(id_.endswith(":"))

    def test_empty_slot_file_on_collision_falls_back_to_bare_id(self):
        p1 = self._make_pkg_dir("dev-lang", "foo-1.0", slot="")  # bos dosya
        p2 = self._make_pkg_dir("dev-lang", "foo-2.0", slot="2")
        packages = self._list_packages([p1, p2])
        ids = sorted(pkg.id for pkg in packages)
        self.assertIn("dev-lang/foo:2", ids)
        self.assertIn("dev-lang/foo", ids)

    def test_whitespace_only_slot_file_treated_as_missing(self):
        p1 = self._make_pkg_dir("dev-lang", "foo-1.0", slot="\n  \n")
        p2 = self._make_pkg_dir("dev-lang", "foo-2.0", slot="2")
        packages = self._list_packages([p1, p2])
        ids = sorted(pkg.id for pkg in packages)
        self.assertIn("dev-lang/foo:2", ids)
        self.assertIn("dev-lang/foo", ids)

    def test_both_colliding_entries_missing_slot_stay_both_bare(self):
        # En kotu durum: hicbiri ayirt edilemiyor - eskisinden DAHA
        # KOTU degil, sadece hala cozulememis (karakterize ediliyor).
        p1 = self._make_pkg_dir("dev-lang", "foo-1.0")
        p2 = self._make_pkg_dir("dev-lang", "foo-2.0")
        packages = self._list_packages([p1, p2])
        ids = [pkg.id for pkg in packages]
        self.assertEqual(ids, ["dev-lang/foo", "dev-lang/foo"])

    def test_directory_vanishes_between_listing_and_slot_read(self):
        # Dizin var ama SLOT dosyasina erisilemiyor (ör. silinmis) -
        # OSError guvenle yakalanmali, cokme olmamali.
        p1 = self._make_pkg_dir("dev-lang", "foo-1.0")
        p2 = self._make_pkg_dir("dev-lang", "foo-2.0", slot="2")
        import shutil
        shutil.rmtree(p1)  # SLOT dosyasina erisimi imkansiz kil
        packages = self._list_packages([p1, p2])  # exception atmamali
        ids = sorted(pkg.id for pkg in packages)
        self.assertIn("dev-lang/foo:2", ids)
        self.assertIn("dev-lang/foo", ids)


class ExistingBehaviorRegressionTests(PortageTestBase):
    """Mevcut ayrıştırma/kaldırma davranışında gerileme olmadığını
    doğrular - bu testler düzeltmeden ÖNCE de geçerdi."""

    def test_empty_output_returns_no_packages(self):
        def fake_run(argv, timeout=None):
            return mock.Mock(returncode=0, stdout="")

        backend = PortageBackend()
        with mock.patch(
            "bulkuninstaller.backends.portage.host.run", side_effect=fake_run
        ):
            self.assertEqual(backend.list_packages(), [])

    def test_ls_failure_returns_empty_list(self):
        backend = PortageBackend()
        with mock.patch(
            "bulkuninstaller.backends.portage.host.run"
        ) as run_mock:
            run_mock.return_value = mock.Mock(returncode=1, stdout="")
            self.assertEqual(backend.list_packages(), [])

    def test_install_date_comes_from_directory_mtime(self):
        path = self._make_pkg_dir("app-editors", "vim-9.1.0", slot="0")
        pkg = self._list_packages([path])[0]
        self.assertAlmostEqual(pkg.install_date, os.stat(path).st_mtime)

    def test_remove_argv_builds_separate_argv_items(self):
        backend = PortageBackend()
        argv = backend.remove_argv(["app-editors/vim", "app-shells/bash"])
        self.assertEqual(
            argv,
            ["emerge", "--unmerge", "--ask=n", "app-editors/vim", "app-shells/bash"],
        )

    def test_ids_are_not_shell_joined(self):
        backend = PortageBackend()
        argv = backend.remove_argv(["pkg; rm -rf ~"])
        self.assertEqual(argv[-1], "pkg; rm -rf ~")
        self.assertEqual(len(argv), 4)

    def test_needs_root_is_true(self):
        self.assertTrue(PortageBackend.needs_root)


if __name__ == "__main__":
    unittest.main()
