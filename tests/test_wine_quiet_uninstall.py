"""backends/wine.py: QuietUninstallString tercihi.

Gerçek, canlı bulgu (Notepad++ 8.9.9.1, gerçek NSIS kurulumu, izole
bir WINEPREFIX'te yeniden üretildi): registry girdisi HEM
UninstallString HEM QuietUninstallString taşıyor - PackWarden şimdiye
kadar yalnızca düz (etkileşimli olabilen) UninstallString'i
kullanıyordu. QuietUninstallString varsa artık ONA öncelik veriliyor -
toplu/otomatik bir araç için beklenmedik bir onay penceresinin
arkasında süresiz beklemek yerine, sessiz/otomasyona uygun yol tercih
ediliyor (bkz. backends/wine.py: _uninstall_string).

Gerçek paket kaldırma/registry değişikliği YAPILMAZ - `host.run`
taklit edilir; `_parse_uninstall_entries` kısmı GERÇEK, geçici bir
.reg metniyle (gerçek dosya ayrıştırma mantığını da sınayan) test
edilir.

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
from bulkuninstaller.backends.wine import WineBackend, _uninstall_string  # noqa: E402


class UninstallStringHelperTests(unittest.TestCase):
    """_uninstall_string(): saf fonksiyon, hiçbir yan etkisi yok."""

    def test_prefers_quiet_uninstall_string_when_both_present(self):
        entry = {
            "UninstallString": r'"C:\Program Files\App\uninstall.exe"',
            "QuietUninstallString": r'"C:\Program Files\App\uninstall.exe" /S',
        }
        self.assertEqual(
            _uninstall_string(entry),
            r'"C:\Program Files\App\uninstall.exe" /S',
        )

    def test_falls_back_to_plain_uninstall_string_when_no_quiet_variant(self):
        entry = {"UninstallString": r'"C:\Program Files\App\uninstall.exe"'}
        self.assertEqual(
            _uninstall_string(entry),
            r'"C:\Program Files\App\uninstall.exe"',
        )

    def test_uses_quiet_uninstall_string_alone_if_thats_all_there_is(self):
        entry = {"QuietUninstallString": r'"C:\Program Files\App\uninstall.exe" /S'}
        self.assertEqual(
            _uninstall_string(entry),
            r'"C:\Program Files\App\uninstall.exe" /S',
        )

    def test_returns_none_when_neither_field_exists(self):
        self.assertIsNone(_uninstall_string({}))


def _write_fake_system_reg(path, key, display_name, uninstall_string,
                            quiet_uninstall_string=None):
    lines = [
        f"[Software\\\\Microsoft\\\\Windows\\\\CurrentVersion\\\\Uninstall\\\\{key}] 1700000000",
        f'"DisplayName"="{display_name}"',
        f'"UninstallString"="{uninstall_string}"',
    ]
    if quiet_uninstall_string is not None:
        lines.append(f'"QuietUninstallString"="{quiet_uninstall_string}"')
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


class RealRegistryParsingSelectsQuietStringTests(unittest.TestCase):
    """GERÇEK _parse_uninstall_entries() ayrıştırma mantığı üzerinden
    uçtan uca: gerçek bir .reg metin parçası yazılır, gerçek kod bunu
    okur, doğru UninstallString'i seçer."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.prefix = self.tmp.name
        wine._entries_cache.clear()

    def tearDown(self):
        self.tmp.cleanup()
        wine._entries_cache.clear()

    def test_notepad_plus_plus_style_entry_prefers_the_quiet_string(self):
        # Gercek Notepad++ 8.9.9.1 kaydinin AYNISI (izole bir
        # WINEPREFIX'te gercekten kurulup okunarak dogrulandi).
        reg_path = os.path.join(self.prefix, "system.reg")
        _write_fake_system_reg(
            reg_path, "Notepad++", "Notepad++ (64-bit x64)",
            r"C:\\Program Files\\Notepad++\\uninstall.exe",
            r"C:\\Program Files\\Notepad++\\uninstall.exe /S",
        )
        entry = wine._find_entry(self.prefix, "Notepad++")
        self.assertIsNotNone(entry)
        self.assertEqual(
            _uninstall_string(entry),
            r"C:\Program Files\Notepad++\uninstall.exe /S",
        )

    def test_package_without_a_quiet_variant_still_works_as_before(self):
        reg_path = os.path.join(self.prefix, "system.reg")
        _write_fake_system_reg(
            reg_path, "OldApp", "Old App",
            r"C:\\Program Files\\OldApp\\uninstall.exe",
        )
        entry = wine._find_entry(self.prefix, "OldApp")
        self.assertEqual(
            _uninstall_string(entry), r"C:\Program Files\OldApp\uninstall.exe"
        )


class BackendUsesQuietStringTests(unittest.TestCase):
    """WineBackend.remove_argv()/remove() GERÇEKTEN sessiz komutu mu
    çalıştırıyor? `host.run` taklit edilir - gerçek wine hiç çalışmaz."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.prefix = self.tmp.name
        wine._entries_cache.clear()
        reg_path = os.path.join(self.prefix, "system.reg")
        _write_fake_system_reg(
            reg_path, "Notepad++", "Notepad++ (64-bit x64)",
            r"C:\\Program Files\\Notepad++\\uninstall.exe",
            r"C:\\Program Files\\Notepad++\\uninstall.exe /S",
        )
        self.pkg_id = f"{self.prefix}::Notepad++"

    def tearDown(self):
        self.tmp.cleanup()
        wine._entries_cache.clear()

    def test_remove_argv_uses_the_quiet_uninstall_string(self):
        backend = WineBackend()
        argv = backend.remove_argv([self.pkg_id])
        self.assertIn("/S", argv)
        self.assertTrue(any("uninstall.exe" in a for a in argv))

    def test_remove_invokes_host_run_with_the_quiet_argv(self):
        backend = WineBackend()
        with mock.patch(
            "bulkuninstaller.backends.wine.host.run"
        ) as run_mock, mock.patch(
            "bulkuninstaller.backends.wine._key_exists", return_value=False,
        ):
            run_mock.return_value = mock.Mock(
                returncode=0, stdout="", stderr=""
            )
            backend.remove([self.pkg_id])
        run_mock.assert_called_once()
        called_argv = run_mock.call_args[0][0]
        self.assertIn("/S", called_argv)

    def test_list_packages_includes_app_even_if_only_quiet_string_exists(self):
        reg_path = os.path.join(self.prefix, "system.reg")
        _write_fake_system_reg(
            reg_path, "QuietOnlyApp", "Quiet Only App", "",
            r"C:\\Program Files\\QuietOnlyApp\\uninstall.exe /S",
        )
        # UninstallString bos birakildi (bazi NSIS varyasyonlarinda
        # gorulebilir) - sadece QuietUninstallString varsa bile paket
        # hala listelenebilmeli, "kaldirma yolu yok" diye atlanmamali.
        wine._entries_cache.clear()
        with mock.patch(
            "bulkuninstaller.backends.wine._prefixes", return_value=[self.prefix]
        ):
            backend = WineBackend()
            names = [p.name for p in backend.list_packages()]
        self.assertIn("Quiet Only App", names)


if __name__ == "__main__":
    unittest.main()
