"""backends/zypper.py: multilib paket kimliği ayrıştırması.

Gerçek `rpm`/`zypper` HİÇ çalıştırılmaz - `host.run` taklit edilir. Bu
makinede zypper/rpm kurulu değil (Arch/CachyOS) - bu testler `rpm --qf`
çıktı biçimini (belgelenmiş, sabit format dizesi) taklit eder, gerçek
bir openSUSE sistemine karşı canlı doğrulanmadı.

"ad.mimari" söz diziminin zypper remove'da geçerli olduğu, zypper'ın
resmi man sayfasıyla (capability syntax: "NAME[.ARCH][ OP EDITION]",
remove komutu install ile aynı seçim söz dizimini kullanıyor)
doğrulandı - zypper'ın kendisi bu makinede yok, canlı çalıştırılmadı.

Çalıştırma:
    PYTHONPATH=src python3 -m unittest discover -s tests -v
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bulkuninstaller.backends.zypper import ZypperBackend  # noqa: E402


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

        backend = ZypperBackend()
        with mock.patch(
            "bulkuninstaller.backends.zypper.host.run", side_effect=fake_run
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
        self.assertEqual(ids, ["alacritty", "glibc"])

    def test_missing_architecture_field_falls_back_to_bare_name(self):
        text = "\n".join([
            "glibc\t\t2.35-1\t1000\tdesc\t(none)\t1700000000\tMIT",
            _qa_line("glibc", "i686"),
        ])
        packages = self._run_with_rpm_output(text)
        ids = sorted(p.id for p in packages)
        self.assertIn("glibc", ids)
        self.assertIn("glibc.i686", ids)

    def test_empty_output_returns_no_packages(self):
        self.assertEqual(self._run_with_rpm_output(""), [])

    def test_rpm_failure_returns_empty_list(self):
        backend = ZypperBackend()
        with mock.patch(
            "bulkuninstaller.backends.zypper.host.run"
        ) as run_mock:
            run_mock.return_value = mock.Mock(returncode=1, stdout="")
            self.assertEqual(backend.list_packages(), [])

    def test_fields_still_parsed_correctly_after_arch_insertion(self):
        text = _qa_line(
            "foo", "x86_64", version="2.3-4", size="2048",
            summary="a test package", vendor="openSUSE",
            installtime="1700000000", license_="GPL-2.0-or-later",
        )
        packages = self._run_with_rpm_output(text)
        pkg = packages[0]
        self.assertEqual(pkg.version, "2.3-4")
        self.assertEqual(pkg.size, 2048)  # %{SIZE} zaten bayt
        self.assertEqual(pkg.description, "a test package")
        self.assertEqual(pkg.publisher, "openSUSE")
        self.assertEqual(pkg.install_date, 1700000000.0)
        self.assertEqual(pkg.license, "GPL-2.0-or-later")

    def test_none_vendor_and_license_become_empty(self):
        text = _qa_line("foo", "x86_64", vendor="(none)", license_="(none)")
        pkg = self._run_with_rpm_output(text)[0]
        self.assertEqual(pkg.publisher, "")
        self.assertEqual(pkg.license, "")


class RemoveArgvMultilibTargetingTests(unittest.TestCase):
    """Kaldırma komutunun mimariye-nitelikli kimliği AYNEN iletip, kardeş
    mimariye dokunmadığını doğrular. zypper'ın "ad.mimari" (capability)
    söz dizimini resmi man sayfası doğruluyor - zypper'ın kendisi bu
    makinede yok, canlı çalıştırılmadı."""

    def test_qualified_id_is_passed_through_verbatim(self):
        backend = ZypperBackend()
        argv = backend.remove_argv(["glibc.i686"])
        self.assertEqual(
            argv,
            ["zypper", "--non-interactive", "remove", "--clean-deps", "glibc.i686"],
        )
        self.assertNotIn("glibc.x86_64", argv)
        self.assertNotIn("glibc", argv)

    def test_removing_one_architecture_id_does_not_include_the_other(self):
        backend = ZypperBackend()
        argv_i686 = backend.remove_argv(["glibc.i686"])
        argv_x86_64 = backend.remove_argv(["glibc.x86_64"])
        self.assertNotIn("glibc.x86_64", argv_i686)
        self.assertNotIn("glibc.i686", argv_x86_64)

    def test_unqualified_id_unchanged_for_ordinary_packages(self):
        backend = ZypperBackend()
        argv = backend.remove_argv(["alacritty"])
        self.assertEqual(argv[-1], "alacritty")


if __name__ == "__main__":
    unittest.main()
