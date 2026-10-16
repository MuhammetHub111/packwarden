"""removal.py: RemovalWindow._on_done()'ın sonuç mesajı seçimi.

Üç ayrı konuyu birlikte kapsar:
- Başarı/iptal/hata durumlarının birbirinden ayrı, doğru metinlerle
  bildirilmesi (karma backend + iptal düzeltmesi).
- Paket kaldırma hatasıyla kalıntı silme hatasının AYRI başlıklarla
  gösterilmesi ("paketler kaldırılamadı" sadece kalıntı hatasında
  yanlış ve yanıltıcıydı).
- Liste yenileme (`refresh()`) ve pencere kapanmasının (`close()`) HER
  durumda (tam başarı/kısmi başarı/tam iptal/hata) tutarlı çalışması -
  eskiden "tamamen iptal" durumunda bu ikisi hiç çalışmıyordu.

Gerçek paket kaldırma/sistem değişikliği YAPILMAZ - `_on_done()`
doğrudan, hazırlanmış argümanlarla çağrılır; worker thread'i hiç
tetiklenmez. `Adw.AlertDialog` gerçek diyalog göstermemesi için
taklit edilir.

Çalıştırma (GTK gerektiği için ana sistemde):
    flatpak-spawn --host env PYTHONPATH=/mnt/D/packwarden/src \
        python3 -m unittest tests.test_removal_on_done_messages -v
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import gi  # noqa: E402
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from bulkuninstaller.backends.base import Package  # noqa: E402
from bulkuninstaller.i18n import _  # noqa: E402
from bulkuninstaller import removal  # noqa: E402


def _pkg(source, name):
    return Package(
        id=name, name=name, version="1.0", size=0, description="",
        source=source,
    )


class _Item:
    def __init__(self, pkg):
        self.pkg = pkg


def _make_window(pkgs):
    main_win = Gtk.Window()
    main_win._backends = []
    main_win._launcher_map = {}
    main_win._toast_overlay = Adw.ToastOverlay()
    win = removal.RemovalWindow(main_win, [_Item(p) for p in pkgs])
    win._main.refresh = mock.Mock()
    win.close = mock.Mock()
    win._main._toast_overlay.add_toast = mock.Mock()
    return win


def _toast_titles(win):
    return [
        call.args[0].get_property("title")
        for call in win._main._toast_overlay.add_toast.call_args_list
    ]


class OnDoneRefreshCloseConsistencyTests(unittest.TestCase):
    def test_full_cancellation_still_refreshes_and_closes(self):
        # Eskiden "cancelled" dalı burada erken return yapıp refresh()/
        # close()'u hiç çağırmıyordu.
        win = _make_window([_pkg("pacman", "Foo")])
        win._on_done([], [], 0, 1, None, 0)
        win._main.refresh.assert_called_once()
        win.close.assert_called_once()

    def test_errors_branch_still_refreshes_and_closes(self):
        win = _make_window([_pkg("pacman", "Foo")])
        with mock.patch("bulkuninstaller.removal.Adw.AlertDialog") as dialog_cls:
            dialog_cls.return_value = mock.Mock()
            win._on_done(["Pacman:\nerror"], [], 0, 0, None, 0)
        win._main.refresh.assert_called_once()
        win.close.assert_called_once()

    def test_full_success_still_refreshes_and_closes(self):
        win = _make_window([_pkg("pacman", "Foo")])
        win._on_done([], [], 1, 0, None, 0)
        win._main.refresh.assert_called_once()
        win.close.assert_called_once()


class OnDoneMessageSelectionTests(unittest.TestCase):
    def test_full_success_shows_simple_count_with_no_cancellation_toast(self):
        win = _make_window([_pkg("pacman", "Foo")])
        win._on_done([], [], 1, 0, None, 0)
        self.assertEqual(
            _toast_titles(win),
            [_("Uninstalled {count} packages").format(count=1)],
        )

    def test_partial_success_with_cancellation_shows_both_counts(self):
        win = _make_window([_pkg("pacman", "Foo"), _pkg("flatpak", "Bar")])
        win._on_done([], [], 1, 1, None, 0)
        titles = _toast_titles(win)
        self.assertIn(
            _("Uninstalled {count} of {total} packages").format(count=1, total=2),
            titles,
        )
        self.assertIn(
            _("{count} removal(s) were cancelled").format(count=1), titles
        )

    def test_full_cancellation_shows_only_the_authorization_toast(self):
        win = _make_window([_pkg("pacman", "Foo")])
        win._on_done([], [], 0, 1, None, 0)
        self.assertEqual(_toast_titles(win), [_("Authorization was cancelled")])

    def test_cleaned_count_toast_still_shown_on_success(self):
        win = _make_window([_pkg("pacman", "Foo")])
        with mock.patch(
            "bulkuninstaller.removal.prefs.get", return_value="trash"
        ):
            win._on_done([], [], 1, 0, None, 3)
        self.assertIn(
            _("Moved {count} leftover items to Trash").format(count=3),
            _toast_titles(win),
        )

    def test_closed_running_and_backup_toasts_are_unaffected(self):
        # Gerileme kontrolü: bu düzeltme öncesi de var olan iki toast.
        win = _make_window([_pkg("pacman", "Foo")])
        win._on_done(
            [], [], 1, 0, "/tmp/backup-x", 0, closed_running=["Foo"],
        )
        titles = _toast_titles(win)
        self.assertIn(
            _("Closed {count} running app(s) before removal").format(count=1),
            titles,
        )
        self.assertIn(
            _("Backup saved: {path}").format(path="/tmp/backup-x"), titles
        )


class ErrorHeadingSeparationTests(unittest.TestCase):
    """Paket kaldırma hatası ile kalıntı silme hatası AYRI başlıklarla
    gösterilmeli - paketler zaten başarıyla kaldırılmışken sadece bir
    kalıntı dosyası silinemediğinde "paketler kaldırılamadı" demek
    yanlış ve yanıltıcıdır."""

    def _present_and_get_kwargs(self, win, errors, leftover_errors):
        with mock.patch("bulkuninstaller.removal.Adw.AlertDialog") as dialog_cls:
            dialog_cls.return_value = mock.Mock()
            win._on_done(errors, leftover_errors, 1, 0, None, 0)
            return dialog_cls.call_args.kwargs

    def test_package_removal_error_heading_mentions_packages(self):
        win = _make_window([_pkg("pacman", "Foo")])
        kwargs = self._present_and_get_kwargs(win, ["Pacman:\nerror"], [])
        self.assertEqual(
            kwargs["heading"], _("Some packages could not be removed")
        )

    def test_leftover_only_error_heading_mentions_leftovers_not_packages(self):
        win = _make_window([_pkg("pacman", "Foo")])
        kwargs = self._present_and_get_kwargs(
            win, [], ["/home/x/.config/foo: permission denied"]
        )
        self.assertEqual(
            kwargs["heading"], _("Some leftover files could not be removed")
        )

    def test_both_error_kinds_present_heading_still_mentions_packages(self):
        win = _make_window([_pkg("pacman", "Foo")])
        kwargs = self._present_and_get_kwargs(
            win, ["Pacman:\nerror"], ["/home/x/.config/foo: permission denied"]
        )
        self.assertEqual(
            kwargs["heading"], _("Some packages could not be removed")
        )
        self.assertIn("Pacman", kwargs["body"])
        self.assertIn("permission denied", kwargs["body"])

    def test_no_errors_at_all_shows_no_dialog(self):
        win = _make_window([_pkg("pacman", "Foo")])
        with mock.patch("bulkuninstaller.removal.Adw.AlertDialog") as dialog_cls:
            win._on_done([], [], 1, 0, None, 0)
            dialog_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
