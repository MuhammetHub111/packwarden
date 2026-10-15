"""removal.py: `_installed_flatpak_ids()` ve `_clean_unused_flatpak_data()`.

Bu iki fonksiyon önceden dosyada BİREBİR aynı gövdeyle iki kez
tanımlıydı (ilk tanımlar hiç çağrılamıyordu, Python ikincisiyle sessizce
değiştiriyordu) - burada kalan TEK tanımın davranışını, hiçbir gerçek
"flatpak" komutu veya GI Flatpak binding'i çalıştırmadan doğrular.

Çalıştırma (GTK/Adw gerektiği için ana sistemde):
    flatpak-spawn --host env PYTHONPATH=/mnt/D/packwarden/src \
        python3 -m unittest discover -s /mnt/D/packwarden/tests -v
"""

import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bulkuninstaller import removal  # noqa: E402


class InstalledFlatpakIdsTests(unittest.TestCase):
    def test_parses_output_into_a_set(self):
        with mock.patch("bulkuninstaller.removal.host.run") as run_mock:
            run_mock.return_value = mock.Mock(
                stdout="org.kde.okular\ncom.usebottles.bottles\n\n  \n"
            )
            result = removal._installed_flatpak_ids()
        self.assertEqual(result, {"org.kde.okular", "com.usebottles.bottles"})
        run_mock.assert_called_once_with(
            ["flatpak", "list", "--app", "--columns=application"], timeout=15
        )

    def test_returns_empty_set_on_failure(self):
        with mock.patch("bulkuninstaller.removal.host.run") as run_mock:
            run_mock.side_effect = Exception("flatpak bulunamadı")
            result = removal._installed_flatpak_ids()
        self.assertEqual(result, set())


def _install_fake_flatpak_gi(prune_calls, require_version_error=None):
    """sys.modules'e gerçek 'gi'/'Flatpak' yerine sahte bir modül ağacı
    enjekte eder - prune_local_repo() GERÇEKTEN çağrılmaz, sadece
    `prune_calls` listesine kaydedilir."""

    class _FakeInstallation:
        def __init__(self, kind):
            self._kind = kind

        def prune_local_repo(self):
            prune_calls.append(self._kind)

    class _Installation:
        @staticmethod
        def new_system():
            return _FakeInstallation("system")

        @staticmethod
        def new_user():
            return _FakeInstallation("user")

    fake_flatpak_mod = types.ModuleType("gi.repository.Flatpak")
    fake_flatpak_mod.Installation = _Installation

    fake_repository_mod = types.ModuleType("gi.repository")
    fake_repository_mod.Flatpak = fake_flatpak_mod

    def _require_version(namespace, version):
        if require_version_error is not None:
            raise require_version_error

    fake_gi_mod = types.ModuleType("gi")
    fake_gi_mod.require_version = _require_version
    fake_gi_mod.repository = fake_repository_mod

    return {
        "gi": fake_gi_mod,
        "gi.repository": fake_repository_mod,
        "gi.repository.Flatpak": fake_flatpak_mod,
    }


class CleanUnusedFlatpakDataTests(unittest.TestCase):
    def test_runs_uninstall_unused_and_prunes_both_installations(self):
        prune_calls: list[str] = []
        fake_modules = _install_fake_flatpak_gi(prune_calls)
        with mock.patch.dict(sys.modules, fake_modules):
            with mock.patch("bulkuninstaller.removal.host.run") as run_mock:
                run_mock.return_value = mock.Mock(returncode=0)
                removal._clean_unused_flatpak_data()
        run_mock.assert_called_once_with(
            ["flatpak", "uninstall", "--unused", "-y"], timeout=120
        )
        self.assertEqual(sorted(prune_calls), ["system", "user"])

    def test_host_run_failure_does_not_prevent_prune(self):
        prune_calls: list[str] = []
        fake_modules = _install_fake_flatpak_gi(prune_calls)
        with mock.patch.dict(sys.modules, fake_modules):
            with mock.patch("bulkuninstaller.removal.host.run") as run_mock:
                run_mock.side_effect = Exception("flatpak çalışmadı")
                removal._clean_unused_flatpak_data()  # exception atmamalı
        self.assertEqual(sorted(prune_calls), ["system", "user"])

    def test_missing_flatpak_gi_binding_does_not_raise(self):
        fake_modules = _install_fake_flatpak_gi(
            prune_calls=[], require_version_error=ValueError("Flatpak yok")
        )
        with mock.patch.dict(sys.modules, fake_modules):
            with mock.patch("bulkuninstaller.removal.host.run") as run_mock:
                run_mock.return_value = mock.Mock(returncode=0)
                removal._clean_unused_flatpak_data()  # exception atmamalı


if __name__ == "__main__":
    unittest.main()
