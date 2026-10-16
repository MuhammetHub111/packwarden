"""backends/nix.py: ad/sürüm ayrıştırması (Nix'in resmi "soldan ilk
tire+rakam" kuralı).

Gerçek `nix-env` HİÇ çalıştırılmaz - `host.run` taklit edilir. Bu
makinede Nix kurulu değil - test girdileri Nix'in kendi resmi referans
kılavuzunda ve NixOS Discourse'ta verilen GERÇEK örnek sürüm
biçimleridir; gerçek bir Nix kurulumuna karşı canlı doğrulanmadı.

Çalıştırma:
    PYTHONPATH=src python3 -m unittest discover -s tests -v
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bulkuninstaller.backends.nix import NixBackend  # noqa: E402


class ListPackagesParsingTests(unittest.TestCase):
    def _run_with_query_output(self, text: str) -> list:
        def fake_run(argv, timeout=None):
            if argv[:2] == ["nix-env", "-q"]:
                return mock.Mock(returncode=0, stdout=text)
            return mock.Mock(returncode=1, stdout="")

        backend = NixBackend()
        with mock.patch(
            "bulkuninstaller.backends.nix.host.run", side_effect=fake_run
        ):
            return backend.list_packages()

    def test_hyphenated_unstable_date_version_is_split_correctly(self):
        # Nix'in kendi resmi örneği - rpartition ile YANLIŞ bölünürdü.
        pkg = self._run_with_query_output("mypkg-1.17.1-unstable-2025-05-24\n")[0]
        self.assertEqual(pkg.name, "mypkg")
        self.assertEqual(pkg.version, "1.17.1-unstable-2025-05-24")
        self.assertEqual(pkg.id, "mypkg")

    def test_hyphenated_short_version_is_split_correctly(self):
        # Nix'in kendi resmi örneği.
        pkg = self._run_with_query_output("somelib-2.42-67\n")[0]
        self.assertEqual(pkg.name, "somelib")
        self.assertEqual(pkg.version, "2.42-67")

    def test_hyphenated_package_name_with_simple_version(self):
        pkg = self._run_with_query_output("git-minimal-2.42.0\n")[0]
        self.assertEqual(pkg.name, "git-minimal")
        self.assertEqual(pkg.version, "2.42.0")

    def test_multi_hyphen_package_name_with_simple_version(self):
        pkg = self._run_with_query_output("systemd-minimal-libs-255.4\n")[0]
        self.assertEqual(pkg.name, "systemd-minimal-libs")
        self.assertEqual(pkg.version, "255.4")

    def test_simple_name_no_internal_hyphens(self):
        pkg = self._run_with_query_output("firefox-115.0.2\n")[0]
        self.assertEqual(pkg.name, "firefox")
        self.assertEqual(pkg.version, "115.0.2")

    def test_package_with_no_version_suffix_keeps_whole_line_as_name(self):
        # Tire+rakam hiç yok - tüm satır ad olarak kalır, sürüm boş.
        pkg = self._run_with_query_output("hello\n")[0]
        self.assertEqual(pkg.name, "hello")
        self.assertEqual(pkg.version, "")

    def test_malformed_line_starting_with_hyphen_digit_keeps_whole_line(self):
        # "-1.0" gibi bastan tireyle baslayan bozuk bir satir - ad
        # BOS kalmamali, tum satir ada dusmeli.
        pkg = self._run_with_query_output("-1.0\n")[0]
        self.assertEqual(pkg.name, "-1.0")
        self.assertEqual(pkg.version, "")

    def test_name_segment_ending_in_digit_before_the_real_version(self):
        # "python3" adin bir parcasi - ondan sonraki tire harfle
        # ("pip") devam ettigi icin surum siniri SAYILMAMALI, gercek
        # surum siniri "pip" SONRASINDAKI tire olmali.
        pkg = self._run_with_query_output("python3-pip-23.3.1\n")[0]
        self.assertEqual(pkg.name, "python3-pip")
        self.assertEqual(pkg.version, "23.3.1")

    def test_multiple_packages_each_on_own_line(self):
        text = "\n".join([
            "firefox-115.0.2",
            "mypkg-1.17.1-unstable-2025-05-24",
            "git-minimal-2.42.0",
        ])
        packages = self._run_with_query_output(text)
        names = sorted(p.name for p in packages)
        self.assertEqual(names, ["firefox", "git-minimal", "mypkg"])

    def test_blank_lines_are_skipped(self):
        text = "firefox-115.0.2\n\n\ngit-minimal-2.42.0\n"
        packages = self._run_with_query_output(text)
        self.assertEqual(len(packages), 2)

    def test_empty_output_returns_no_packages(self):
        self.assertEqual(self._run_with_query_output(""), [])

    def test_query_failure_returns_empty_list(self):
        backend = NixBackend()
        with mock.patch("bulkuninstaller.backends.nix.host.run") as run_mock:
            run_mock.return_value = mock.Mock(returncode=1, stdout="")
            self.assertEqual(backend.list_packages(), [])

    def test_id_matches_name_without_version(self):
        pkg = self._run_with_query_output("somelib-2.42-67\n")[0]
        self.assertEqual(pkg.id, pkg.name)
        self.assertNotIn("2.42", pkg.id)


class RemoveArgvTests(unittest.TestCase):
    def test_builds_uninstall_with_separate_argv_items(self):
        backend = NixBackend()
        argv = backend.remove_argv(["firefox", "git-minimal"])
        self.assertEqual(argv, ["nix-env", "-e", "firefox", "git-minimal"])

    def test_ids_are_not_shell_joined(self):
        backend = NixBackend()
        argv = backend.remove_argv(["pkg; rm -rf ~"])
        self.assertEqual(argv, ["nix-env", "-e", "pkg; rm -rf ~"])
        self.assertEqual(len(argv), 3)

    def test_needs_root_is_false(self):
        self.assertFalse(NixBackend.needs_root)


if __name__ == "__main__":
    unittest.main()
