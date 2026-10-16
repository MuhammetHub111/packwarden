"""removal.py: RemovalWindow._on_remove(), "Yedekle ve Kaldır" seçilince
AÇILAN klasör seçme diyaloğu kullanıcı cevap verene kadar BEKLEYEN bir
kilit alır.

Bu diyalog asenkron - `_on_remove()` diyaloğu açıp hemen geri döner,
gerçek sonuç `_on_backup_folder_chosen()`e ayrıca gelir. Bu bekleme
sırasında `self._busy` hiç değişmiyordu (canlı, güvenli bir betikle
doğrulandı: ikinci bir "Kaldır" tıklaması GERÇEKTEN ikinci bir
`_start()`i tetikleyebiliyordu - bkz. önceki denetim turu). Burada bu
boşluğun kapandığı, kilidin iptal/hata durumunda GERÇEKTEN bırakıldığı
ve normal (yedeksiz) akışın etkilenmediği test ediliyor.

Gerçek paket kaldırma/sistem değişikliği YAPILMAZ - `Gtk.FileDialog`
taklit edilir, gerçek bir dosya seçici hiç açılmaz; `create_backup`
hiç çağrılmadan (ya da en fazla BİR kez çağrıldığı doğrulanarak) test
edilir.

Çalıştırma (GTK gerektiği için ana sistemde):
    flatpak-spawn --host env PYTHONPATH=/mnt/D/packwarden/src \
        python3 -m unittest tests.test_removal_backup_dialog_lock -v
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import gi  # noqa: E402
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from bulkuninstaller.backends.base import Package  # noqa: E402
from bulkuninstaller import removal  # noqa: E402


def _pkg():
    return Package(
        id="Foo", name="Foo", version="1.0", size=0, description="",
        source="pacman",
    )


class _Item:
    def __init__(self, pkg):
        self.pkg = pkg


def _make_window():
    main_win = Gtk.Window()
    main_win._backends = []
    main_win._launcher_map = {}
    main_win._toast_overlay = Adw.ToastOverlay()
    return removal.RemovalWindow(main_win, [_Item(_pkg())])


class BackupDialogPendingLockTests(unittest.TestCase):
    def test_choosing_backup_locks_busy_before_the_dialog_callback_fires(self):
        win = _make_window()
        with mock.patch("bulkuninstaller.removal.Gtk.FileDialog") as DialogCls:
            DialogCls.return_value = mock.Mock()
            win._on_remove(None, True)
        self.assertTrue(win._busy)

    def test_second_removal_request_is_blocked_while_the_dialog_is_pending(self):
        win = _make_window()
        with mock.patch("bulkuninstaller.removal.Gtk.FileDialog") as DialogCls:
            DialogCls.return_value = mock.Mock()
            win._on_remove(None, True)  # ilk istek: yedekli kaldır

        win._start = mock.Mock()
        win._on_remove(None, False)  # ikinci istek: yedeksiz kaldır
        win._start.assert_not_called()

    def test_second_removal_request_with_backup_again_is_also_blocked(self):
        win = _make_window()
        with mock.patch("bulkuninstaller.removal.Gtk.FileDialog") as DialogCls:
            DialogCls.return_value = mock.Mock()
            win._on_remove(None, True)
            # Aynı kullanıcı ikinci kez "Yedekle ve Kaldır"a basıyor -
            # ikinci bir Gtk.FileDialog da açılmamalı.
            DialogCls.reset_mock()
            win._on_remove(None, True)
            DialogCls.assert_not_called()

    def test_only_one_create_backup_call_can_ever_happen_from_this_race(self):
        # Gerçek create_backup() hiç çağrılmaz (backup_base verilmedi) -
        # burada asıl doğrulanan, _start()'ın (create_backup'ı çağıran
        # tek yer) ikinci kez TETİKLENEMEDİĞİ.
        win = _make_window()
        start_calls = []
        real_start = win._start

        def counting_start(backup_base):
            start_calls.append(backup_base)

        win._start = counting_start
        with mock.patch("bulkuninstaller.removal.Gtk.FileDialog") as DialogCls:
            DialogCls.return_value = mock.Mock()
            win._on_remove(None, True)
        win._on_remove(None, False)
        win._on_remove(None, True)
        self.assertEqual(start_calls, [])


class BackupDialogCancelAndErrorReleaseLockTests(unittest.TestCase):
    def _busy_window_after_choosing_backup(self):
        win = _make_window()
        with mock.patch("bulkuninstaller.removal.Gtk.FileDialog") as DialogCls:
            DialogCls.return_value = mock.Mock()
            win._on_remove(None, True)
        self.assertTrue(win._busy)
        return win

    def test_user_cancelling_the_picker_releases_the_lock(self):
        win = self._busy_window_after_choosing_backup()
        fake_dialog = mock.Mock()
        fake_dialog.select_folder_finish.side_effect = GLib.Error(
            "Dismissed by user"
        )
        win._on_backup_folder_chosen(fake_dialog, mock.Mock())
        self.assertFalse(win._busy)
        self.assertTrue(win._backup_button.get_sensitive())
        self.assertTrue(win._nobackup_button.get_sensitive())

    def test_picker_error_also_releases_the_lock(self):
        # GLib.Error, kullanıcı iptali İLE gerçek bir hatayı AYNI yoldan
        # taşır - ikisi de aynı dalda yakalanır, ikisi de kilidi bırakmalı.
        win = self._busy_window_after_choosing_backup()
        fake_dialog = mock.Mock()
        fake_dialog.select_folder_finish.side_effect = GLib.Error(
            "Portal backend crashed"
        )
        win._on_backup_folder_chosen(fake_dialog, mock.Mock())
        self.assertFalse(win._busy)

    def test_removal_can_be_started_normally_after_the_lock_is_released(self):
        win = self._busy_window_after_choosing_backup()
        fake_dialog = mock.Mock()
        fake_dialog.select_folder_finish.side_effect = GLib.Error("cancelled")
        win._on_backup_folder_chosen(fake_dialog, mock.Mock())

        win._start = mock.Mock()
        win._on_remove(None, False)
        win._start.assert_called_once_with(backup_base=None)

    def test_successful_folder_choice_proceeds_into_the_normal_removal_flow(self):
        win = self._busy_window_after_choosing_backup()
        fake_dialog = mock.Mock()
        fake_folder = mock.Mock()
        fake_folder.get_path.return_value = "/tmp/chosen-backup-dir"
        fake_dialog.select_folder_finish.return_value = fake_folder

        win._start = mock.Mock()
        win._on_backup_folder_chosen(fake_dialog, mock.Mock())
        win._start.assert_called_once_with(backup_base="/tmp/chosen-backup-dir")


class NoBackupFlowUnaffectedTests(unittest.TestCase):
    def test_remove_without_backup_never_touches_the_file_dialog(self):
        win = _make_window()
        with mock.patch("bulkuninstaller.removal.Gtk.FileDialog") as DialogCls:
            win._start = mock.Mock()
            win._on_remove(None, False)
            DialogCls.assert_not_called()
            win._start.assert_called_once_with(backup_base=None)


if __name__ == "__main__":
    unittest.main()
