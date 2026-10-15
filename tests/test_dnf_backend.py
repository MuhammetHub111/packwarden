"""backends/dnf.py: multilib paket kimliği ayrıştırması.

Gerçek `rpm`/`dnf` HİÇ çalıştırılmaz - `host.run` taklit edilir. Bu
makinede dnf/rpm kurulu değil (Arch/CachyOS) - bu testler `rpm --qf`
çıktı biçimini (belgelenmiş, sabit format dizesi) taklit eder, gerçek
bir Fedora/RHEL sistemine karşı canlı doğrulanmadı.

Çalıştırma:
    PYTHONPATH=src python3 -m unittest discover -s tests -v
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bulkuninstaller.backends.dnf import DnfBackend  # noqa: E402


def _qa_line(
    name, arch, version="1.0-1", size="1000", summary="desc",
    vendor="(none)", installtime="1700000000", license_="MIT",
):
    return (
        f"{name}\t{arch}\t{version}\t{size}\t{summary}\t"
        f"{vendor}\t{installtime}\t{license_}"
    )


class ListPackagesMultilibTests(unittest.TestCase):
    def _run_with_rpm_output(self, text: str) -> list:
        def fake_run(argv, timeout=None):
            if argv[0] == "rpm":
                return mock.Mock(returncode=0, stdout=text)
            return mock.Mock(returncode=1, stdout="")

        backend = DnfBackend()
        with mock.patch(
            "bulkuninstaller.backends.dnf.host.run", side_effect=fake_run
        ):
            return backend.list_packages()

    def test_ordinary_single_arch_package_id_is_unchanged(self):
        text = _qa_line("alacritty", "x86_64")
        packages = self._run_with_rpm_output(text)
        self.assertEqual(len(packages), 1)
        self.assertEqual(packages[0].id, "alacritty")
        self.assertEqual(packages[0].name, "alacritty")

    def test_multilib_pair_gets_distinct_dot_qualified_ids(self):
        text = "\n".join([
            _qa_line("glibc", "x86_64"),
            _qa_line("glibc", "i686"),
        ])
        packages = self._run_with_rpm_output(text)
        ids = sorted(p.id for p in packages)
        self.assertEqual(ids, ["glibc.i686", "glibc.x86_64"])
        self.assertEqual(len(set(ids)), 2)

    def test_three_way_multilib_all_distinct(self):
        text = "\n".join([
            _qa_line("foo", "x86_64"),
            _qa_line("foo", "i686"),
            _qa_line("foo", "noarch"),
        ])
        packages = self._run_with_rpm_output(text)
        ids = sorted(p.id for p in packages)
        self.assertEqual(ids, ["foo.i686", "foo.noarch", "foo.x86_64"])

    def test_unrelated_packages_with_different_names_are_not_merged(self):
        text = "\n".join([
            _qa_line("glibc", "x86_64"),
            _qa_line("alacritty", "x86_64"),
        ])
        packages = self._run_with_rpm_output(text)
        ids = sorted(p.id for p in packages)
        # Aynı mimaride FARKLI adlar - çakışma yok, hiçbiri değişmemeli.
        self.assertEqual(ids, ["alacritty", "glibc"])

    def test_missing_architecture_field_falls_back_to_bare_name(self):
        text = "\n".join([
            "glibc\t\t2.35-1\t1000\tdesc\t(none)\t1700000000\tMIT",
            _qa_line("glibc", "i686"),
        ])
        packages = self._run_with_rpm_output(text)
        ids = sorted(p.id for p in packages)
        self.assertIn("glibc", ids)       # uydurma "glibc." kimliği yok
        self.assertIn("glibc.i686", ids)  # dolu mimarili satır ayrıldı

    def test_empty_output_returns_no_packages(self):
        self.assertEqual(self._run_with_rpm_output(""), [])

    def test_rpm_failure_returns_empty_list(self):
        backend = DnfBackend()
        with mock.patch("bulkuninstaller.backends.dnf.host.run") as run_mock:
            run_mock.return_value = mock.Mock(returncode=1, stdout="")
            self.assertEqual(backend.list_packages(), [])

    def test_fields_still_parsed_correctly_after_arch_insertion(self):
        text = _qa_line(
            "foo", "x86_64", version="2.3-4", size="2048",
            summary="a test package", vendor="Fedora Project",
            installtime="1700000000", license_="GPL-2.0-or-later",
        )
        packages = self._run_with_rpm_output(text)
        pkg = packages[0]
        self.assertEqual(pkg.version, "2.3-4")
        self.assertEqual(pkg.size, 2048)  # %{SIZE} zaten bayt, çarpılmaz
        self.assertEqual(pkg.description, "a test package")
        self.assertEqual(pkg.publisher, "Fedora Project")
        self.assertEqual(pkg.install_date, 1700000000.0)
        self.assertEqual(pkg.license, "GPL-2.0-or-later")

    def test_none_vendor_and_license_become_empty(self):
        text = _qa_line("foo", "x86_64", vendor="(none)", license_="(none)")
        pkg = self._run_with_rpm_output(text)[0]
        self.assertEqual(pkg.publisher, "")
        self.assertEqual(pkg.license, "")


class RemoveArgvMultilibTargetingTests(unittest.TestCase):
    """Kaldırma komutunun mimariye-nitelikli kimliği AYNEN iletip, kardeş
    mimariye dokunmadığını doğrular (dnf/rpm'in belgelenmiş "ad.mimari"
    söz dizimine güveniliyor - bu makinede dnf/rpm yok, canlı
    doğrulanamadı)."""

    def test_qualified_id_is_passed_through_verbatim(self):
        backend = DnfBackend()
        argv = backend.remove_argv(["glibc.i686"])
        self.assertEqual(argv, ["dnf", "remove", "-y", "glibc.i686"])
        self.assertNotIn("glibc.x86_64", argv)
        self.assertNotIn("glibc", argv)  # yalnızca nitelikli hâli olmalı

    def test_removing_one_architecture_id_does_not_include_the_other(self):
        backend = DnfBackend()
        argv_i686 = backend.remove_argv(["glibc.i686"])
        argv_x86_64 = backend.remove_argv(["glibc.x86_64"])
        self.assertNotIn("glibc.x86_64", argv_i686)
        self.assertNotIn("glibc.i686", argv_x86_64)

    def test_unqualified_id_unchanged_for_ordinary_packages(self):
        backend = DnfBackend()
        argv = backend.remove_argv(["alacritty"])
        self.assertEqual(argv[-1], "alacritty")


if __name__ == "__main__":
    unittest.main()
