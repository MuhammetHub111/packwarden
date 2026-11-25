"""usage_daemon.py: bağımsız arkaplan kullanım algılama döngüsünün
yardımcı işlevleri (_load_packages, _handle_stop).

Gerçek paket yöneticileri, gerçek systemd/sinyal işleme ve gerçek
~/.config/bulkuninstaller/usage.json HİÇ kullanılmaz - tüm backend'ler
ve build_maps() taklit edilir. `main()`'in sonsuz döngüsü hiç
çalıştırılmaz; sadece döngünün dışındaki yardımcı işlevler test edilir.

Çalıştırma (GTK gerekmez, bare sandbox'ta çalışır):
    PYTHONPATH=src python3 -m unittest tests.test_usage_daemon -v
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bulkuninstaller import usage_daemon  # noqa: E402


def _backend(packages=None, raises=False):
    backend = mock.Mock()
    if raises:
        backend.list_packages.side_effect = RuntimeError("backend is broken")
    else:
        backend.list_packages.return_value = list(packages or [])
    return backend


class HandleStopTests(unittest.TestCase):
    def tearDown(self):
        usage_daemon._stop = False

    def test_sets_the_module_level_stop_flag(self):
        self.assertFalse(usage_daemon._stop)
        usage_daemon._handle_stop(15, None)
        self.assertTrue(usage_daemon._stop)


class LoadPackagesTests(unittest.TestCase):
    def test_merges_game_and_regular_backend_packages(self):
        game_backend = _backend(packages=["game-a", "game-b"])
        regular_backend = _backend(packages=["pkg-a"])
        with mock.patch(
            "bulkuninstaller.games.available_game_backends",
            return_value=[game_backend],
        ), mock.patch(
            "bulkuninstaller.usage_daemon.available_backends",
            return_value=[regular_backend],
        ), mock.patch(
            "bulkuninstaller.usage_daemon.build_maps",
            return_value=({}, {"launcher": "map"}, {}),
        ):
            packages, launcher_map = usage_daemon._load_packages()
        self.assertEqual(packages, ["game-a", "game-b", "pkg-a"])
        self.assertEqual(launcher_map, {"launcher": "map"})

    def test_a_broken_backend_is_skipped_without_crashing(self):
        broken = _backend(raises=True)
        healthy = _backend(packages=["pkg-a"])
        with mock.patch(
            "bulkuninstaller.games.available_game_backends", return_value=[],
        ), mock.patch(
            "bulkuninstaller.usage_daemon.available_backends",
            return_value=[broken, healthy],
        ), mock.patch(
            "bulkuninstaller.usage_daemon.build_maps",
            return_value=({}, {}, {}),
        ):
            packages, _launcher_map = usage_daemon._load_packages()
        self.assertEqual(packages, ["pkg-a"])

    def test_a_failing_build_maps_falls_back_to_an_empty_launcher_map(self):
        with mock.patch(
            "bulkuninstaller.games.available_game_backends", return_value=[],
        ), mock.patch(
            "bulkuninstaller.usage_daemon.available_backends",
            return_value=[_backend(packages=["pkg-a"])],
        ), mock.patch(
            "bulkuninstaller.usage_daemon.build_maps",
            side_effect=RuntimeError("icon cache is corrupt"),
        ):
            packages, launcher_map = usage_daemon._load_packages()
        self.assertEqual(packages, ["pkg-a"])
        self.assertEqual(launcher_map, {})


if __name__ == "__main__":
    unittest.main()
