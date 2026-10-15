"""backends/apk.py: ad/sürüm ayrıştırması ve kaldırma komutu kurulumu.

Gerçek `apk` HİÇ çalıştırılmaz - `host.run` taklit edilir. Bu makinede
apk/Alpine kurulu değil (Arch/CachyOS) - bu testler, Alpine'in resmi
abuild belgelerinde doğrulanan "pkgver tire içeremez" kuralına dayanan
"apk info -v" çıktı biçimini taklit eder; gerçek bir Alpine sistemine
karşı canlı çalıştırılmadı.

Çalıştırma:
    PYTHONPATH=src python3 -m unittest discover -s tests -v
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bulkuninstaller.backends.apk import ApkBackend  # noqa: E402


class ListPackagesParsingTests(unittest.TestCase):
    def _run_with_info_output(self, text: str) -> list:
        def fake_run(argv, timeout=None):
            if argv[:2] == ["apk", "info"]:
                return mock.Mock(returncode=0, stdout=text)
            return mock.Mock(returncode=1, stdout="")

        backend = ApkBackend()
        with mock.patch(
            "bulkuninstaller.backends.apk.host.run", side_effect=fake_run
        ):
            return backend.list_packages()

    def test_simple_name_no_internal_hyphens(self):
        packages = self._run_with_info_output("btop-1.4.7-r0\n")
        self.assertEqual(len(packages), 1)
        self.assertEqual(packages[0].id, "btop")
        self.assertEqual(packages[0].name, "btop")
        self.assertEqual(packages[0].version, "1.4.7-r0")

    def test_name_with_one_internal_hyphen(self):
        # Alpine'de tire içeren paket adları çok yaygın (py3-pip gibi) -
        # pkgver'in tire İÇEREMEMESİ (abuild belgelerinde doğrulandı)
        # sayesinde sağdan son iki tire her zaman sürüm/release'dir.
        packages = self._run_with_info_output("py3-pip-21.0-r0\n")
        self.assertEqual(len(packages), 1)
        self.assertEqual(packages[0].id, "py3-pip")
        self.assertEqual(packages[0].version, "21.0-r0")

    def test_name_with_multiple_internal_hyphens(self):
        packages = self._run_with_info_output("linux-firmware-amdgpu-20240115-r0\n")
        self.assertEqual(len(packages), 1)
        self.assertEqual(packages[0].id, "linux-firmware-amdgpu")
        self.assertEqual(packages[0].version, "20240115-r0")

    def test_version_with_single_trailing_letter(self):
        # abuild belgeleri: "final number may have a single letter
        # following it" (ör. 1.5a) - tire olmadığı için ayrıştırma
        # yine bozulmamalı.
        packages = self._run_with_info_output("somepkg-1.5a-r2\n")
        self.assertEqual(packages[0].id, "somepkg")
        self.assertEqual(packages[0].version, "1.5a-r2")

    def test_version_with_underscore_prerelease_suffix(self):
        packages = self._run_with_info_output("somepkg-1.2.3_rc2-r0\n")
        self.assertEqual(packages[0].id, "somepkg")
        self.assertEqual(packages[0].version, "1.2.3_rc2-r0")

    def test_multiple_packages_each_on_own_line(self):
        text = "alacritty-0.17.0-r2\nbtop-1.4.7-r0\npy3-pip-21.0-r0\n"
        packages = self._run_with_info_output(text)
        ids = sorted(p.id for p in packages)
        self.assertEqual(ids, ["alacritty", "btop", "py3-pip"])

    def test_blank_lines_are_skipped(self):
        text = "btop-1.4.7-r0\n\n\nalacritty-0.17.0-r2\n"
        packages = self._run_with_info_output(text)
        self.assertEqual(len(packages), 2)

    def test_empty_output_returns_no_packages(self):
        self.assertEqual(self._run_with_info_output(""), [])

    def test_malformed_line_without_two_hyphens_falls_back_to_whole_line(self):
        # "ad-sürüm-release" biçimine uymayan bir satır - ad olarak
        # TÜMÜ kullanılır, sürüm boş kalır; uydurma bir bölme yapılmaz.
        packages = self._run_with_info_output("nohyphenatall\n")
        self.assertEqual(len(packages), 1)
        self.assertEqual(packages[0].id, "nohyphenatall")
        self.assertEqual(packages[0].version, "")

    def test_line_with_exactly_one_hyphen_falls_back_to_whole_line(self):
        # rsplit(2) için gereken İKİ tireyi karşılamıyor - mevcut kod
        # bunu da olduğu gibi ada koyuyor (karakterize ediliyor, bir
        # doğruluk iddiası değil).
        packages = self._run_with_info_output("only-onehyphen\n")
        self.assertEqual(len(packages), 1)
        self.assertEqual(packages[0].id, "only-onehyphen")
        self.assertEqual(packages[0].version, "")

    def test_apk_info_failure_returns_empty_list(self):
        backend = ApkBackend()
        with mock.patch("bulkuninstaller.backends.apk.host.run") as run_mock:
            run_mock.return_value = mock.Mock(returncode=1, stdout="")
            self.assertEqual(backend.list_packages(), [])

    def test_duplicate_lines_currently_produce_duplicate_entries(self):
        # Mevcut davranış KARAKTERİZE ediliyor, doğru/yanlış olduğu
        # iddia edilmiyor: apk info -v normalde aynı paketi iki kez
        # basmaz, ama kod bunu tespit edip birleştirmiyor - girdi
        # tekrarlanırsa çıktı da tekrarlanır. Gerçek bir hata
        # doğrulanmadığı için üretim kodu değiştirilmedi.
        packages = self._run_with_info_output("btop-1.4.7-r0\nbtop-1.4.7-r0\n")
        self.assertEqual(len(packages), 2)
        self.assertEqual(packages[0].id, packages[1].id)

    def test_other_fields_default_to_empty_or_zero(self):
        # apk.py boyut/açıklama/yayıncı/kurulum tarihi/lisans/
        # install_reason hiç doldurmuyor - bu bir hata değil (bkz.
        # inceleme raporu), burada yalnızca mevcut hâli sabitleniyor.
        pkg = self._run_with_info_output("btop-1.4.7-r0\n")[0]
        self.assertEqual(pkg.size, 0)
        self.assertEqual(pkg.description, "")
        self.assertEqual(pkg.publisher, "")
        self.assertIsNone(pkg.install_date)
        self.assertEqual(pkg.license, "")
        self.assertEqual(pkg.install_reason, "")
        self.assertIsNone(pkg.required_by)


class RemoveArgvTests(unittest.TestCase):
    def test_builds_del_with_separate_argv_items(self):
        backend = ApkBackend()
        argv = backend.remove_argv(["btop", "alacritty"])
        self.assertEqual(argv, ["apk", "del", "btop", "alacritty"])

    def test_ids_are_not_shell_joined(self):
        # Zararlı bir metin TEK bir argv öğesi olarak kalmalı, bir
        # shell dizesine katıştırılıp yorumlanmamalı.
        backend = ApkBackend()
        argv = backend.remove_argv(["pkg; rm -rf ~"])
        self.assertEqual(argv, ["apk", "del", "pkg; rm -rf ~"])
        self.assertEqual(len(argv), 3)

    def test_single_package_targets_only_that_id(self):
        backend = ApkBackend()
        argv = backend.remove_argv(["btop"])
        self.assertNotIn("alacritty", argv)
        self.assertEqual(argv[-1], "btop")

    def test_empty_ids_produces_bare_command_without_targets(self):
        # Mevcut davranış karakterize ediliyor - removal.py zaten boş
        # id listesiyle hiç çağırmıyor (bkz. pacman/apt/dnf/zypper
        # incelemeleri), bu yüzden bu yol pratikte erişilmiyor.
        backend = ApkBackend()
        argv = backend.remove_argv([])
        self.assertEqual(argv, ["apk", "del"])

    def test_needs_root_is_true(self):
        self.assertTrue(ApkBackend.needs_root)


class RemoveCallChainTests(unittest.TestCase):
    """Backend.remove() (base.py, apk'ye özel değiştirilmemiş) ile
    birlikte komut başarısızlığının/iptalin nasıl ele alındığını
    apk'ye özel argv ile doğrular."""

    def test_successful_removal(self):
        backend = ApkBackend()
        with mock.patch(
            "bulkuninstaller.backends.apk.host.run_privileged"
        ) as run_mock:
            run_mock.return_value = mock.Mock(
                returncode=0, stdout="OK", stderr=""
            )
            result = backend.remove(["btop"])
        self.assertTrue(result.ok)
        run_mock.assert_called_once_with(["apk", "del", "btop"])

    def test_command_failure_is_reported_not_silently_swallowed(self):
        backend = ApkBackend()
        with mock.patch(
            "bulkuninstaller.backends.apk.host.run_privileged"
        ) as run_mock:
            run_mock.return_value = mock.Mock(
                returncode=1, stdout="", stderr="ERROR: unable to delete btop"
            )
            result = backend.remove(["btop"])
        self.assertFalse(result.ok)
        self.assertIn("unable to delete", result.output)
        self.assertEqual(result.failed_ids, ["btop"])

    def test_pkexec_dismissal_is_treated_as_cancelled(self):
        # pkexec: 126 = kullanıcı yetkilendirme penceresini kapattı.
        backend = ApkBackend()
        with mock.patch(
            "bulkuninstaller.backends.apk.host.run_privileged"
        ) as run_mock:
            run_mock.return_value = mock.Mock(returncode=126, stdout="", stderr="")
            result = backend.remove(["btop"])
        self.assertTrue(result.cancelled)
        self.assertFalse(result.ok)

    def test_timeout_or_portal_exception_is_caught(self):
        backend = ApkBackend()
        with mock.patch(
            "bulkuninstaller.backends.apk.host.run_privileged"
        ) as run_mock:
            run_mock.side_effect = Exception("zaman aşımı")
            result = backend.remove(["btop"])  # exception sızdırmamalı
        self.assertFalse(result.ok)
        self.assertEqual(result.failed_ids, ["btop"])


if __name__ == "__main__":
    unittest.main()
