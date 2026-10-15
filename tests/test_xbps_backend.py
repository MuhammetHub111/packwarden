"""backends/xbps.py: durum kodu filtresi ve ad/sürüm ayrıştırması.

Gerçek `xbps-query`/`xbps-remove` HİÇ çalıştırılmaz - `host.run` taklit
edilir. Bu makinede xbps/Void Linux kurulu değil - durum kodlarının
anlamı resmi `xbps-query(1)` man sayfasından alındı, gerçek bir Void
sistemine karşı canlı doğrulanmadı.

Çalıştırma:
    PYTHONPATH=src python3 -m unittest discover -s tests -v
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bulkuninstaller.backends.xbps import XbpsBackend  # noqa: E402


class StateCodeFilterTests(unittest.TestCase):
    """xbps-query(1): ii=tam kurulu, uu=yapılandırılmamış,
    hr=yarı kaldırılmış, ??=bilinmeyen. Yalnızca "ii" normal uygulama
    listesine alınmalı."""

    def _run_with_query_output(self, text: str) -> list:
        def fake_run(argv, timeout=None):
            if argv[:2] == ["xbps-query", "-l"]:
                return mock.Mock(returncode=0, stdout=text)
            return mock.Mock(returncode=1, stdout="")

        backend = XbpsBackend()
        with mock.patch(
            "bulkuninstaller.backends.xbps.host.run", side_effect=fake_run
        ):
            return backend.list_packages()

    def test_ii_installed_package_is_kept(self):
        packages = self._run_with_query_output("ii btop-1.4.7_1 a resource monitor\n")
        self.assertEqual(len(packages), 1)
        self.assertEqual(packages[0].id, "btop")

    def test_uu_unconfigured_package_is_excluded(self):
        packages = self._run_with_query_output("uu btop-1.4.7_1 a resource monitor\n")
        self.assertEqual(packages, [])

    def test_hr_half_removed_package_is_excluded(self):
        packages = self._run_with_query_output("hr btop-1.4.7_1 a resource monitor\n")
        self.assertEqual(packages, [])

    def test_unknown_state_package_is_excluded(self):
        packages = self._run_with_query_output("?? btop-1.4.7_1 a resource monitor\n")
        self.assertEqual(packages, [])

    def test_mixed_states_only_ii_survive(self):
        text = "\n".join([
            "ii alacritty-0.17.0_2 terminal emulator",
            "uu somepkg-1.0_1 not yet configured",
            "hr oldpkg-2.0_1 half removed",
            "ii btop-1.4.7_1 a resource monitor",
            "?? weirdpkg-1.0_1 unknown state",
        ])
        packages = self._run_with_query_output(text)
        ids = sorted(p.id for p in packages)
        self.assertEqual(ids, ["alacritty", "btop"])


class ListPackagesParsingTests(unittest.TestCase):
    def _run_with_query_output(self, text: str) -> list:
        def fake_run(argv, timeout=None):
            if argv[:2] == ["xbps-query", "-l"]:
                return mock.Mock(returncode=0, stdout=text)
            return mock.Mock(returncode=1, stdout="")

        backend = XbpsBackend()
        with mock.patch(
            "bulkuninstaller.backends.xbps.host.run", side_effect=fake_run
        ):
            return backend.list_packages()

    def test_name_and_version_split_on_last_hyphen(self):
        pkg = self._run_with_query_output("ii alacritty-0.17.0_2 terminal\n")[0]
        self.assertEqual(pkg.id, "alacritty")
        self.assertEqual(pkg.version, "0.17.0_2")

    def test_hyphenated_package_name_is_preserved(self):
        pkg = self._run_with_query_output("ii linux-firmware-20240115_1 firmware\n")[0]
        self.assertEqual(pkg.id, "linux-firmware")
        self.assertEqual(pkg.version, "20240115_1")

    def test_description_with_multiple_words_is_captured(self):
        pkg = self._run_with_query_output(
            "ii btop-1.4.7_1 a monitor of resources for the system\n"
        )[0]
        self.assertEqual(pkg.description, "a monitor of resources for the system")

    def test_missing_description_is_empty(self):
        pkg = self._run_with_query_output("ii btop-1.4.7_1\n")[0]
        self.assertEqual(pkg.description, "")

    def test_pkgver_without_any_hyphen_falls_back_to_whole_string(self):
        pkg = self._run_with_query_output("ii nohyphenatall somedesc\n")[0]
        self.assertEqual(pkg.id, "nohyphenatall")
        self.assertEqual(pkg.version, "")

    def test_blank_lines_are_skipped(self):
        text = "ii alacritty-0.17.0_2 terminal\n\n\nii btop-1.4.7_1 monitor\n"
        packages = self._run_with_query_output(text)
        self.assertEqual(len(packages), 2)

    def test_line_with_only_status_code_is_skipped(self):
        # len(parts) < 2 - pkgver bile yok, kaydedilmemeli.
        packages = self._run_with_query_output("ii\n")
        self.assertEqual(packages, [])

    def test_empty_output_returns_no_packages(self):
        self.assertEqual(self._run_with_query_output(""), [])

    def test_query_failure_returns_empty_list(self):
        backend = XbpsBackend()
        with mock.patch("bulkuninstaller.backends.xbps.host.run") as run_mock:
            run_mock.return_value = mock.Mock(returncode=1, stdout="")
            self.assertEqual(backend.list_packages(), [])

    def test_other_fields_default_to_empty_or_zero(self):
        # xbps.py boyut/yayıncı/kurulum tarihi/lisans/install_reason/
        # required_by hiç doldurmuyor - hata değil (bkz. inceleme
        # raporu), burada yalnızca mevcut hâli sabitleniyor.
        pkg = self._run_with_query_output("ii btop-1.4.7_1 monitor\n")[0]
        self.assertEqual(pkg.size, 0)
        self.assertEqual(pkg.publisher, "")
        self.assertIsNone(pkg.install_date)
        self.assertEqual(pkg.license, "")
        self.assertEqual(pkg.install_reason, "")
        self.assertIsNone(pkg.required_by)


class RemoveArgvTests(unittest.TestCase):
    """Filtrelemenin remove_argv/removal.py etkileşimini değiştirmediğini
    doğrular - bu fonksiyon hiç değişmedi, yalnızca girdi (zaten "ii"
    olan paketlerin id'leri) değişmeden akıyor."""

    def test_builds_remove_with_separate_argv_items(self):
        backend = XbpsBackend()
        argv = backend.remove_argv(["btop", "alacritty"])
        self.assertEqual(argv, ["xbps-remove", "-Ry", "btop", "alacritty"])

    def test_ids_are_not_shell_joined(self):
        backend = XbpsBackend()
        argv = backend.remove_argv(["pkg; rm -rf ~"])
        self.assertEqual(argv, ["xbps-remove", "-Ry", "pkg; rm -rf ~"])
        self.assertEqual(len(argv), 3)

    def test_needs_root_is_true(self):
        self.assertTrue(XbpsBackend.needs_root)


if __name__ == "__main__":
    unittest.main()
