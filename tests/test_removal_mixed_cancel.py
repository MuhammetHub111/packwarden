"""removal.py: karma backend'li toplu kaldırmada, bir backend'in
yetkilendirmesi iptal edilse bile DİĞER backend'lerin gerçekten
başarmış olduğu kaldırmaların kaybolmaması.

Eskiden `cancelled` tek, paylaşılan bir bayraktı - bir backend iptal
olunca TÜM işlem "iptal edildi" sayılıyor, başarıyla kaldırılan diğer
paketlerin kalıntıları hiç temizlenmiyordu. Burada her backend'in
sonucu (`RemoveResult.cancelled`) BAĞIMSIZ değerlendiriliyor mu,
yalnızca GERÇEKTEN kaldırılan paketlere ait kalıntılar mı siliniyor,
diye test ediliyor.

Gerçek paket kaldırma/sistem değişikliği YAPILMAZ - backend'ler tamamen
sahte (`_ScriptedBackend`), `close_running`/`remove_leftovers`/
`find_package_leftovers`/Flatpak sorguları taklit edilir. Arka plan
worker'ı senkron çalışacak şekilde taklit edilir, `GLib.idle_add`
yalnızca ÇAĞRI ARGÜMANLARINI yakalamak için taklit edilir.

Çalıştırma (GTK gerektiği için ana sistemde):
    flatpak-spawn --host env PYTHONPATH=/mnt/D/packwarden/src \
        python3 -m unittest tests.test_removal_mixed_cancel -v
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
from bulkuninstaller.leftovers import Leftover  # noqa: E402
from bulkuninstaller import removal  # noqa: E402


def _pkg(source, name, pkg_id=None):
    return Package(
        id=pkg_id or name, name=name, version="1.0", size=0,
        description="", source=source,
    )


class _Item:
    def __init__(self, pkg):
        self.pkg = pkg


class _CheckStub:
    """Gtk.CheckButton'ın RemovalWindow._selected_leftovers() için
    ihtiyaç duyduğu tek metodu taşıyan hafif bir sahte nesne."""
    def get_active(self):
        return True


class _ImmediateThread:
    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        self._target()


class _ScriptedBackend:
    def __init__(self, backend_id, display_name, result):
        self.id = backend_id
        self.display_name = display_name
        self._result = result
        self.calls = []

    def remove(self, ids):
        self.calls.append(list(ids))
        return self._result


def _make_window(pkgs, backends):
    main_win = Gtk.Window()
    main_win._backends = backends
    main_win._launcher_map = {}
    return removal.RemovalWindow(main_win, [_Item(p) for p in pkgs])


class MixedBackendCancelTests(unittest.TestCase):
    def setUp(self):
        self.close_running_patcher = mock.patch(
            "bulkuninstaller.removal.usage.close_running", return_value=False
        )
        self.close_running_patcher.start()

        self.thread_patcher = mock.patch(
            "bulkuninstaller.removal.threading.Thread",
            side_effect=lambda target=None, daemon=None: _ImmediateThread(target),
        )
        self.thread_patcher.start()

        self.idle_add_patcher = mock.patch("bulkuninstaller.removal.GLib.idle_add")
        self.idle_add_mock = self.idle_add_patcher.start()

    def tearDown(self):
        self.close_running_patcher.stop()
        self.thread_patcher.stop()
        self.idle_add_patcher.stop()

    def _on_done_args(self):
        """_on_done'a planlanan (errors, leftover_errors, removed_count,
        cancelled_count, backup_path, cleaned, closed_running,
        affected_flatpak_apps) argümanlarını döner."""
        self.assertTrue(self.idle_add_mock.called)
        args = self.idle_add_mock.call_args[0]
        return args[1:]

    def _clear_checks(self, win):
        # Gerçek arka plan taraması (RemovalWindow.__init__ -> _scan())
        # bu makinede şans eseri "foo"/"bar" adlı bir klasör bulursa
        # testi kararsız kılmasın - kalıntı seçimini açıkça boşaltıyoruz.
        win._checks = []

    def test_pacman_cancelled_flatpak_succeeds_is_not_reported_as_total_cancel(self):
        pacman = _ScriptedBackend(
            "pacman", "Pacman", RemoveResult(ok=False, output="", cancelled=True)
        )
        flatpak = _ScriptedBackend(
            "flatpak", "Flatpak", RemoveResult(ok=True, output="")
        )
        pkgs = [_pkg("pacman", "Foo"), _pkg("flatpak", "Bar", "org.bar")]
        win = _make_window(pkgs, [pacman, flatpak])
        self._clear_checks(win)

        with mock.patch(
            "bulkuninstaller.removal._installed_flatpak_ids",
            side_effect=[{"org.bar"}, set()],
        ), mock.patch("bulkuninstaller.removal._clean_unused_flatpak_data"):
            win._start(backup_base=None)

        errors, leftover_errors, removed_count, cancelled_count, _bp, _cleaned, \
            _closed, affected = self._on_done_args()

        self.assertEqual(errors, [])
        self.assertEqual(leftover_errors, [])
        self.assertEqual(removed_count, 1)
        self.assertEqual(cancelled_count, 1)
        self.assertEqual(affected, set())
        # Flatpak backend'i GERÇEKTEN çağrıldı - pacman'in iptali onu
        # atlatmadı/engellemedi.
        self.assertEqual(flatpak.calls, [["org.bar"]])

    def test_all_backends_cancelled_reports_zero_removed(self):
        pacman = _ScriptedBackend(
            "pacman", "Pacman", RemoveResult(ok=False, output="", cancelled=True)
        )
        dnf = _ScriptedBackend(
            "dnf", "DNF", RemoveResult(ok=False, output="", cancelled=True)
        )
        pkgs = [_pkg("pacman", "Foo"), _pkg("dnf", "Baz")]
        win = _make_window(pkgs, [pacman, dnf])
        self._clear_checks(win)

        win._start(backup_base=None)

        errors, leftover_errors, removed_count, cancelled_count, *_rest = (
            self._on_done_args()
        )
        self.assertEqual(errors, [])
        self.assertEqual(removed_count, 0)
        self.assertEqual(cancelled_count, 2)

    def test_all_backends_succeed_reports_full_count_and_no_cancellation(self):
        pacman = _ScriptedBackend(
            "pacman", "Pacman", RemoveResult(ok=True, output="")
        )
        dnf = _ScriptedBackend("dnf", "DNF", RemoveResult(ok=True, output=""))
        pkgs = [_pkg("pacman", "Foo"), _pkg("dnf", "Baz")]
        win = _make_window(pkgs, [pacman, dnf])
        self._clear_checks(win)

        win._start(backup_base=None)

        errors, leftover_errors, removed_count, cancelled_count, *_rest = (
            self._on_done_args()
        )
        self.assertEqual(errors, [])
        self.assertEqual(removed_count, 2)
        self.assertEqual(cancelled_count, 0)

    def test_genuine_failure_is_not_confused_with_cancellation(self):
        # pkexec reddi DEĞİL, gerçek bir kaldırma hatası (ör. bağımlılık
        # çakışması) - result.cancelled=False, result.ok=False.
        pacman = _ScriptedBackend(
            "pacman", "Pacman",
            RemoveResult(ok=False, output="error: dependency conflict"),
        )
        flatpak = _ScriptedBackend(
            "flatpak", "Flatpak", RemoveResult(ok=True, output="")
        )
        pkgs = [_pkg("pacman", "Foo"), _pkg("flatpak", "Bar", "org.bar")]
        win = _make_window(pkgs, [pacman, flatpak])
        self._clear_checks(win)

        with mock.patch(
            "bulkuninstaller.removal._installed_flatpak_ids",
            side_effect=[{"org.bar"}, set()],
        ), mock.patch("bulkuninstaller.removal._clean_unused_flatpak_data"):
            win._start(backup_base=None)

        errors, leftover_errors, removed_count, cancelled_count, *_rest = (
            self._on_done_args()
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("Pacman", errors[0])
        self.assertEqual(removed_count, 1)
        self.assertEqual(cancelled_count, 0)


class LeftoverFilteringOnPartialSuccessTests(unittest.TestCase):
    """Kısmi başarıda, SADECE gerçekten kaldırılan pakete ait kalıntılar
    silinmeli - hâlâ kurulu (iptal edilmiş) bir paketin ayar klasörü
    yanlışlıkla silinmemeli."""

    def setUp(self):
        self.close_running_patcher = mock.patch(
            "bulkuninstaller.removal.usage.close_running", return_value=False
        )
        self.close_running_patcher.start()
        self.thread_patcher = mock.patch(
            "bulkuninstaller.removal.threading.Thread",
            side_effect=lambda target=None, daemon=None: _ImmediateThread(target),
        )
        self.thread_patcher.start()
        self.idle_add_patcher = mock.patch("bulkuninstaller.removal.GLib.idle_add")
        self.idle_add_mock = self.idle_add_patcher.start()

    def tearDown(self):
        self.close_running_patcher.stop()
        self.thread_patcher.stop()
        self.idle_add_patcher.stop()

    def test_only_the_successfully_removed_packages_leftovers_are_deleted(self):
        pacman = _ScriptedBackend(
            "pacman", "Pacman", RemoveResult(ok=False, output="", cancelled=True)
        )
        flatpak = _ScriptedBackend(
            "flatpak", "Flatpak", RemoveResult(ok=True, output="")
        )
        foo_pkg = _pkg("pacman", "Foo")
        bar_pkg = _pkg("flatpak", "Bar", "org.bar")
        win = removal.RemovalWindow(
            Gtk.Window(), [_Item(foo_pkg), _Item(bar_pkg)]
        )
        win._main._backends = [pacman, flatpak]
        win._main._launcher_map = {}

        leftover_foo = Leftover(path="/home/x/.config/foo", size=10)
        leftover_bar = Leftover(path="/home/x/.var/app/org.bar", size=20)
        win._checks = [(_CheckStub(), leftover_foo), (_CheckStub(), leftover_bar)]

        with mock.patch(
            "bulkuninstaller.removal._installed_flatpak_ids",
            side_effect=[{"org.bar"}, set()],
        ), mock.patch("bulkuninstaller.removal._clean_unused_flatpak_data"), \
            mock.patch(
                "bulkuninstaller.removal.find_package_leftovers"
            ) as find_mock, \
            mock.patch("bulkuninstaller.removal.remove_leftovers") as remove_mock:
            find_mock.return_value = [("App data", [leftover_bar])]
            remove_mock.return_value = []

            win._start(backup_base=None)

            # Sadece GERÇEKTEN kaldırılan paket (bar) ile yeniden tarandı.
            find_mock.assert_called_once_with([bar_pkg])
            # remove_leftovers SADECE bar'ın kalıntısıyla çağrıldı - foo'nun
            # (hâlâ kurulu) ayar klasörü hiç silinmedi.
            remove_mock.assert_called_once_with([leftover_bar])

        args = self.idle_add_mock.call_args[0]
        cleaned = args[6]
        self.assertEqual(cleaned, 1)

    def test_full_success_does_not_trigger_an_extra_rescan(self):
        # Tam başarıda kullanıcının işaretlediği kalıntılar zaten doğru
        # kümedir - gereksiz bir find_package_leftovers çağrısı olmamalı.
        pacman = _ScriptedBackend(
            "pacman", "Pacman", RemoveResult(ok=True, output="")
        )
        foo_pkg = _pkg("pacman", "Foo")
        win = removal.RemovalWindow(Gtk.Window(), [_Item(foo_pkg)])
        win._main._backends = [pacman]
        win._main._launcher_map = {}

        leftover_foo = Leftover(path="/home/x/.config/foo", size=10)
        win._checks = [(_CheckStub(), leftover_foo)]

        with mock.patch(
            "bulkuninstaller.removal.find_package_leftovers"
        ) as find_mock, mock.patch(
            "bulkuninstaller.removal.remove_leftovers"
        ) as remove_mock:
            remove_mock.return_value = []
            win._start(backup_base=None)
            find_mock.assert_not_called()
            remove_mock.assert_called_once_with([leftover_foo])


if __name__ == "__main__":
    unittest.main()
