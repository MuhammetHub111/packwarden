"""backends/pacman.py: ayrıştırma ve kaldırma komutu kurulumu.

Gerçek pacman komutu HİÇ çalıştırılmaz - `host.run` taklit edilir.
Burada hata bulunmadı (bkz. inceleme raporu); bu dosya ŞU AN doğru olan
davranışı sabitler - özellikle "Required By" alanının (protected.py'nin
15 eşiği buna dayanıyor) satır kaydırmadan etkilenmediğini ve
remove_argv'nin güvenli, shell'siz bir argv listesi ürettiğini.

Çalıştırma:
    PYTHONPATH=src python3 -m unittest discover -s tests -v
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bulkuninstaller.backends.pacman import PacmanBackend  # noqa: E402


class RemoveArgvTests(unittest.TestCase):
    def test_builds_noconfirm_recursive_removal(self):
        backend = PacmanBackend()
        argv = backend.remove_argv(["firefox", "vlc"])
        self.assertEqual(
            argv, ["pacman", "-Rns", "--noconfirm", "firefox", "vlc"]
        )

    def test_ids_are_appended_verbatim_not_shell_joined(self):
        # argv bir LİSTE olmalı - tek bir shell metnine katıştırılmamalı.
        backend = PacmanBackend()
        argv = backend.remove_argv(["pkg; rm -rf ~"])
        # Zararlı metin, YORUMLANMADAN tek bir argv öğesi olarak kalmalı.
        self.assertEqual(argv[-1], "pkg; rm -rf ~")
        self.assertEqual(len(argv), 4)

    def test_needs_root_is_true(self):
        self.assertTrue(PacmanBackend.needs_root)


class RequiredByParsingTests(unittest.TestCase):
    """protected.py'nin REQUIRED_BY_THRESHOLD=15 kontrolü bu sayıma
    dayanıyor - yanlış sayım, korunması gereken bir paketin uyarısız
    kaldırılmasına yol açabilir."""

    def test_none_means_zero(self):
        self.assertEqual(
            PacmanBackend._required_by({"Required By": "None"}), 0
        )

    def test_missing_field_is_unknown(self):
        self.assertIsNone(PacmanBackend._required_by({}))

    def test_counts_space_separated_names(self):
        fields = {"Required By": "firefox vlc gimp"}
        self.assertEqual(PacmanBackend._required_by(fields), 3)

    def test_long_single_line_list_is_counted_fully(self):
        # Canlı doğrulandı: pacman bu alanı ASLA satır kaydırmıyor,
        # kaç bağımlı paket olursa olsun tek satırda basıyor.
        names = " ".join(f"pkg{i}" for i in range(700))
        self.assertEqual(
            PacmanBackend._required_by({"Required By": names}), 700
        )


class InstallReasonTests(unittest.TestCase):
    def test_explicit(self):
        self.assertEqual(
            PacmanBackend._install_reason(
                {"Install Reason": "Explicitly installed"}
            ),
            "explicit",
        )

    def test_dependency(self):
        self.assertEqual(
            PacmanBackend._install_reason(
                {"Install Reason": "Installed as a dependency for another package"}
            ),
            "dependency",
        )

    def test_unknown_is_empty(self):
        self.assertEqual(PacmanBackend._install_reason({}), "")


class ListPackagesParsingTests(unittest.TestCase):
    """`pacman -Qi` çıktısını sahte `host.run` ile ayrıştırır - gerçek
    pacman hiç çalışmaz. Çok kayıtlı, satır-kaydırmalı ve boş bir blok
    ayrı ayrı test edilir."""

    def _run_with_qi_output(self, qi_text: str) -> list:
        def fake_run(argv, timeout=None):
            if argv[-1] == "-Qi" or "-Qi" in argv:
                return mock.Mock(returncode=0, stdout=qi_text)
            return mock.Mock(returncode=1, stdout="")

        backend = PacmanBackend()
        with mock.patch(
            "bulkuninstaller.backends.pacman.host.run", side_effect=fake_run
        ):
            return backend.list_packages()

    def test_two_records_separated_by_blank_line(self):
        qi = (
            "Name            : alacritty\n"
            "Version         : 0.17.0-1.2\n"
            "Installed Size  : 8.40 MiB\n"
            "Required By     : None\n"
            "\n"
            "Name            : btop\n"
            "Version         : 1.4.7-1.1\n"
            "Installed Size  : 1.80 MiB\n"
            "Required By     : None\n"
        )
        packages = self._run_with_qi_output(qi)
        self.assertEqual([p.name for p in packages], ["alacritty", "btop"])

    def test_last_record_without_trailing_blank_line_is_kept(self):
        qi = "Name            : alacritty\nRequired By     : None\n"
        packages = self._run_with_qi_output(qi)
        self.assertEqual(len(packages), 1)

    def test_wrapped_optional_deps_does_not_corrupt_next_field(self):
        # "Optional Deps" canlı olarak satır kaydırdığı doğrulandı -
        # devam satırları atlanır ama bu, SONRAKİ (Required By) alanını
        # bozmamalı.
        qi = (
            "Name            : ffmpeg\n"
            "Optional Deps   : avisynthplus: AviSynthPlus support\n"
            "                  ladspa: LADSPA filters\n"
            "Required By     : mpv vlc\n"
        )
        packages = self._run_with_qi_output(qi)
        self.assertEqual(len(packages), 1)
        self.assertEqual(packages[0].name, "ffmpeg")
        self.assertEqual(
            PacmanBackend._required_by({"Required By": "mpv vlc"}), 2
        )

    def test_empty_output_returns_no_packages(self):
        self.assertEqual(self._run_with_qi_output(""), [])

    def test_qi_failure_returns_empty_list(self):
        backend = PacmanBackend()
        with mock.patch(
            "bulkuninstaller.backends.pacman.host.run"
        ) as run_mock:
            run_mock.return_value = mock.Mock(returncode=1, stdout="")
            self.assertEqual(backend.list_packages(), [])


if __name__ == "__main__":
    unittest.main()
