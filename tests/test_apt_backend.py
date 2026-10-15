"""backends/apt.py: multiarch paket kimliği ayrıştırması.

Gerçek `dpkg-query`/`apt-get` HİÇ çalıştırılmaz - `host.run` taklit
edilir. Bu makinede apt/dpkg kurulu değil (Arch/CachyOS) - bu testler
dpkg-query'nin ÇIKTI BİÇİMİNİ (belgelenmiş, sabit format dizesi) taklit
eder, gerçek bir apt sistemine karşı canlı doğrulanmadı.

Çalıştırma:
    PYTHONPATH=src python3 -m unittest discover -s tests -v
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bulkuninstaller.backends.apt import AptBackend  # noqa: E402


def _qi_line(name, arch, version="1.0-1", size="100", summary="desc", maint="Me <m@e.com>"):
    return f"{name}\t{arch}\t{version}\t{size}\t{summary}\t{maint}"


class ListPackagesMultiarchTests(unittest.TestCase):
    def _run_with_dpkg_output(self, text: str) -> list:
        def fake_run(argv, timeout=None):
            if argv[0] == "dpkg-query":
                return mock.Mock(returncode=0, stdout=text)
            return mock.Mock(returncode=1, stdout="")

        backend = AptBackend()
        with mock.patch(
            "bulkuninstaller.backends.apt.host.run", side_effect=fake_run
        ):
            return backend.list_packages()

    def test_ordinary_single_arch_package_id_is_unchanged(self):
        text = _qi_line("alacritty", "amd64")
        packages = self._run_with_dpkg_output(text)
        self.assertEqual(len(packages), 1)
        self.assertEqual(packages[0].id, "alacritty")
        self.assertEqual(packages[0].name, "alacritty")

    def test_multiarch_pair_gets_distinct_qualified_ids(self):
        text = "\n".join([
            _qi_line("libc6", "amd64"),
            _qi_line("libc6", "i386"),
        ])
        packages = self._run_with_dpkg_output(text)
        ids = sorted(p.id for p in packages)
        self.assertEqual(ids, ["libc6:amd64", "libc6:i386"])
        # id ve name birbirinden ayırt edilebilir olmalı - arayüzde iki
        # özdeş satır görünmemeli.
        self.assertEqual(len(set(ids)), 2)

    def test_three_way_multiarch_all_distinct(self):
        text = "\n".join([
            _qi_line("foo", "amd64"),
            _qi_line("foo", "i386"),
            _qi_line("foo", "armhf"),
        ])
        packages = self._run_with_dpkg_output(text)
        ids = sorted(p.id for p in packages)
        self.assertEqual(ids, ["foo:amd64", "foo:armhf", "foo:i386"])

    def test_unrelated_packages_with_different_names_are_not_merged(self):
        text = "\n".join([
            _qi_line("libc6", "amd64"),
            _qi_line("alacritty", "amd64"),
        ])
        packages = self._run_with_dpkg_output(text)
        ids = sorted(p.id for p in packages)
        # Aynı mimaride FARKLI adlar - çakışma yok, hiçbiri değişmemeli.
        self.assertEqual(ids, ["alacritty", "libc6"])

    def test_missing_architecture_field_falls_back_to_bare_name(self):
        # Mimari alanı bozuk/boş gelirse (beklenmez ama olursa) var
        # olmayan bir "ad:" kimliği UYDURULMAMALI - eski (belirsiz ama
        # en azından gerçek) davranışa düşülmeli.
        text = "\n".join([
            "libc6\t\t2.35-1\t100\tdesc\tMe <m@e.com>",
            _qi_line("libc6", "i386"),
        ])
        packages = self._run_with_dpkg_output(text)
        ids = sorted(p.id for p in packages)
        # Boş mimarili satır çıplak "libc6" kalır (uydurma kimlik yok);
        # dolu mimarili satır yine de ayırt edilir.
        self.assertIn("libc6", ids)
        self.assertIn("libc6:i386", ids)

    def test_empty_output_returns_no_packages(self):
        self.assertEqual(self._run_with_dpkg_output(""), [])

    def test_dpkg_query_failure_returns_empty_list(self):
        backend = AptBackend()
        with mock.patch("bulkuninstaller.backends.apt.host.run") as run_mock:
            run_mock.return_value = mock.Mock(returncode=1, stdout="")
            self.assertEqual(backend.list_packages(), [])

    def test_fields_still_parsed_correctly_after_architecture_insertion(self):
        # Alan sırası değişti (Architecture araya girdi) - Version/Size/
        # Summary/Maintainer hâlâ doğru sütunlardan okunmalı.
        text = _qi_line(
            "foo", "amd64", version="2.3-4", size="2048",
            summary="a test package", maint="Jane Doe <jane@example.com>",
        )
        packages = self._run_with_dpkg_output(text)
        pkg = packages[0]
        self.assertEqual(pkg.version, "2.3-4")
        self.assertEqual(pkg.size, 2048 * 1024)
        self.assertEqual(pkg.description, "a test package")
        self.assertEqual(pkg.publisher, "Jane Doe")


class RemoveArgvMultiarchTargetingTests(unittest.TestCase):
    """Kaldırma komutunun mimariye-nitelikli kimliği AYNEN iletip, kardeş
    mimariye dokunmadığını doğrular (apt'in kendi belgelenmiş
    'ad:mimari' söz dizimine güveniliyor - bu makinede apt yok, canlı
    doğrulanamadı)."""

    def test_qualified_id_is_passed_through_verbatim(self):
        backend = AptBackend()
        argv = backend.remove_argv(["libc6:i386"])
        self.assertEqual(
            argv,
            [
                "env", "DEBIAN_FRONTEND=noninteractive",
                "apt-get", "purge", "-y", "--autoremove", "libc6:i386",
            ],
        )
        # Kardeş mimari argv'de hiçbir şekilde yer almamalı.
        self.assertNotIn("libc6:amd64", argv)
        self.assertNotIn("libc6", argv)  # yalnızca nitelikli hâli olmalı

    def test_removing_one_architecture_id_does_not_include_the_other(self):
        backend = AptBackend()
        # İkisi de arayüzde listelensin ama yalnızca biri seçilsin.
        argv_i386 = backend.remove_argv(["libc6:i386"])
        argv_amd64 = backend.remove_argv(["libc6:amd64"])
        self.assertNotIn("libc6:amd64", argv_i386)
        self.assertNotIn("libc6:i386", argv_amd64)

    def test_unqualified_id_unchanged_for_ordinary_packages(self):
        backend = AptBackend()
        argv = backend.remove_argv(["alacritty"])
        self.assertEqual(argv[-1], "alacritty")


if __name__ == "__main__":
    unittest.main()
