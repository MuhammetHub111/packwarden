"""removal.py: PackWarden'ın kendi çalışan sürecini KAPATMAMASI.

Gerçek PackWarden sürecine HİÇBİR sinyal gönderilmez - `usage.close_
running` tamamen taklit edilir (gerçek /proc taraması/kill() hiç
çalışmaz). Gerçek `install.sh remove` de ÇALIŞTIRILMAZ - her backend'in
`remove()`'u taklit edilir, yalnızca ÇAĞRILIP ÇAĞRILMADIĞI ve hangi
id'lerle çağrıldığı doğrulanır.

`RemovalWindow` gerçek bir sınıf olarak (GTK widget'larıyla) kurulur -
yalnızca arka plan iş parçacığı, testin akışını öngörülebilir kılmak
için senkron çalışacak şekilde taklit edilir (`threading.Thread`).
`GLib.idle_add` ile planlanan son geri çağırma (`_on_done`) hiç
tetiklenmez (ana döngü çalıştırılmıyor) - bu testin ihtiyaç duyduğu
tüm doğrulamalar (hangi paketler için close_running çağrıldı, hangi
backend'lerin remove()'u çağrıldı) bundan ÖNCE, worker içinde olur.

Çalıştırma (GTK gerektiği için ana sistemde):
    flatpak-spawn --host env PYTHONPATH=/mnt/D/packwarden/src \
        python3 -m unittest tests.test_removal_self_close -v
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import gi  # noqa: E402
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk  # noqa: E402

from bulkuninstaller.backends.base import Package, RemoveResult  # noqa: E402
from bulkuninstaller import removal  # noqa: E402


def _pkg(source, name, pkg_id=None):
    return Package(
        id=pkg_id or name, name=name, version="1.0", size=0,
        description="", source=source,
    )


class _Item:
    def __init__(self, pkg):
        self.pkg = pkg


class _ImmediateThread:
    """threading.Thread yerine geçer - arka planda değil, HEMEN ve
    senkron çalışır, böylece worker() bitmeden test devam etmez."""

    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        self._target()


class _FakeBackend:
    def __init__(self, backend_id):
        self.id = backend_id
        self.calls: list[list[str]] = []

    def remove(self, ids):
        self.calls.append(list(ids))
        return RemoveResult(ok=True, output="")


def _make_main_window(backends):
    win = Gtk.Window()
    win._backends = backends
    win._launcher_map = {}
    return win


class SelfCloseRunningSkipTests(unittest.TestCase):
    def setUp(self):
        self.close_running_patcher = mock.patch(
            "bulkuninstaller.removal.usage.close_running"
        )
        self.close_running_mock = self.close_running_patcher.start()
        self.close_running_mock.return_value = False

        self.thread_patcher = mock.patch(
            "bulkuninstaller.removal.threading.Thread",
            side_effect=lambda target=None, daemon=None: _ImmediateThread(target),
        )
        self.thread_patcher.start()

        self.idle_add_patcher = mock.patch(
            "bulkuninstaller.removal.GLib.idle_add"
        )
        self.idle_add_patcher.start()

    def tearDown(self):
        self.close_running_patcher.stop()
        self.thread_patcher.stop()
        self.idle_add_patcher.stop()

    def test_close_running_is_never_called_for_the_packwarden_package(self):
        packwarden_pkg = _pkg("packwarden", "PackWarden", "/home/u/.local/share/packwarden")
        backend = _FakeBackend("packwarden")
        main_win = _make_main_window([backend])
        win = removal.RemovalWindow(main_win, [_Item(packwarden_pkg)])

        win._start(backup_base=None)

        self.close_running_mock.assert_not_called()

    def test_install_sh_remove_path_still_runs_for_packwarden(self):
        # "install.sh remove" GERÇEKTEN çalıştırılmıyor - backend.remove()
        # taklit edilmiş durumda; yalnızca ÇAĞRILDIĞI doğrulanıyor.
        packwarden_pkg = _pkg(
            "packwarden", "PackWarden", "/home/u/.local/share/packwarden"
        )
        backend = _FakeBackend("packwarden")
        main_win = _make_main_window([backend])
        win = removal.RemovalWindow(main_win, [_Item(packwarden_pkg)])

        win._start(backup_base=None)

        self.assertEqual(
            backend.calls, [["/home/u/.local/share/packwarden"]]
        )

    def test_other_packages_close_running_behavior_is_unchanged(self):
        # packwarden'a özgü atlama, BAŞKA kaynaklardaki paketlerin
        # close_running davranışını değiştirmemeli.
        other_pkg = _pkg("pacman", "firefox")
        backend = _FakeBackend("pacman")
        main_win = _make_main_window([backend])
        win = removal.RemovalWindow(main_win, [_Item(other_pkg)])

        win._start(backup_base=None)

        self.close_running_mock.assert_called_once()
        called_pkg = self.close_running_mock.call_args[0][0]
        self.assertEqual(called_pkg.source, "pacman")

    def test_bulk_removal_with_packwarden_and_others_still_removes_others(self):
        # Kritik senaryo: PackWarden başka paketlerle BİRLİKTE seçilmiş.
        # PackWarden için süreç kapatma adımı atlandığı için worker
        # kesintiye uğramamalı - DİĞER backend'in remove()'u da
        # çağrılmalı.
        packwarden_pkg = _pkg(
            "packwarden", "PackWarden", "/home/u/.local/share/packwarden"
        )
        other_pkg = _pkg("pacman", "firefox")
        packwarden_backend = _FakeBackend("packwarden")
        pacman_backend = _FakeBackend("pacman")
        main_win = _make_main_window([packwarden_backend, pacman_backend])
        win = removal.RemovalWindow(
            main_win, [_Item(packwarden_pkg), _Item(other_pkg)]
        )

        win._start(backup_base=None)

        self.assertEqual(
            packwarden_backend.calls, [["/home/u/.local/share/packwarden"]]
        )
        self.assertEqual(pacman_backend.calls, [["firefox"]])
        # close_running yalnızca pacman paketi için çağrıldı, packwarden
        # için hiç çağrılmadı.
        self.assertEqual(self.close_running_mock.call_count, 1)
        self.assertEqual(
            self.close_running_mock.call_args[0][0].source, "pacman"
        )

    def test_mixed_order_packwarden_second_still_not_closed(self):
        # Sıra önemli değil - packwarden listenin neresinde olursa
        # olsun hiç kapatılmamalı.
        other_pkg = _pkg("pacman", "firefox")
        packwarden_pkg = _pkg(
            "packwarden", "PackWarden", "/home/u/.local/share/packwarden"
        )
        pacman_backend = _FakeBackend("pacman")
        packwarden_backend = _FakeBackend("packwarden")
        main_win = _make_main_window([pacman_backend, packwarden_backend])
        win = removal.RemovalWindow(
            main_win, [_Item(other_pkg), _Item(packwarden_pkg)]
        )

        win._start(backup_base=None)

        self.assertEqual(self.close_running_mock.call_count, 1)
        self.assertEqual(
            self.close_running_mock.call_args[0][0].source, "pacman"
        )
        self.assertEqual(pacman_backend.calls, [["firefox"]])
        self.assertEqual(
            packwarden_backend.calls,
            [["/home/u/.local/share/packwarden"]],
        )


if __name__ == "__main__":
    unittest.main()
