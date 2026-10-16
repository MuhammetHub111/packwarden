"""removal.py: RemovalWindow'un "Vazgeç" düğmesi, kaldırma GERÇEKTEN
başladıktan sonra artık tıklanabilir değil - eskiden işlem sürerken de
tıklanabilir kalıyordu ve arka plandaki kaldırmayı hiç durdurmadan
sadece pencereyi kapatıyordu, kullanıcıya yanlış bir "iptal ettim"
izlenimi veriyordu.

Gerçek paket kaldırma/sistem değişikliği YAPILMAZ - `_set_busy()`
doğrudan çağrılır, hiçbir backend/worker thread tetiklenmez.

Çalıştırma (GTK gerektiği için ana sistemde):
    flatpak-spawn --host env PYTHONPATH=/mnt/D/packwarden/src \
        python3 -m unittest tests.test_removal_cancel_button -v
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

from bulkuninstaller.backends.base import Package  # noqa: E402
from bulkuninstaller import removal  # noqa: E402


def _pkg(source, name):
    return Package(
        id=name, name=name, version="1.0", size=0, description="",
        source=source,
    )


class _Item:
    def __init__(self, pkg):
        self.pkg = pkg


def _make_window():
    main_win = Gtk.Window()
    main_win._backends = []
    main_win._launcher_map = {}
    return removal.RemovalWindow(main_win, [_Item(_pkg("pacman", "Foo"))])


class CancelButtonBusyStateTests(unittest.TestCase):
    def test_cancel_is_enabled_before_removal_starts(self):
        win = _make_window()
        self.assertTrue(win._cancel_button.get_sensitive())

    def test_cancel_is_disabled_once_removal_is_busy(self):
        win = _make_window()
        win._set_busy(True)
        self.assertFalse(win._cancel_button.get_sensitive())

    def test_cancel_shows_a_tooltip_explaining_why_while_busy(self):
        win = _make_window()
        win._set_busy(True)
        self.assertTrue(win._cancel_button.get_tooltip_text())

    def test_cancel_is_re_enabled_once_busy_ends(self):
        # _on_done() her durumda (başarı/hata/iptal) _set_busy(False)
        # çağırıyor - bu da aynı yolu kullanıyor.
        win = _make_window()
        win._set_busy(True)
        win._set_busy(False)
        self.assertTrue(win._cancel_button.get_sensitive())

    def test_tooltip_is_cleared_once_busy_ends(self):
        win = _make_window()
        win._set_busy(True)
        win._set_busy(False)
        self.assertFalse(win._cancel_button.get_tooltip_text())

    def test_other_action_buttons_still_toggle_with_busy_as_before(self):
        # Gerileme kontrolü: bu düzeltme sadece Vazgeç'e bir satır ekliyor,
        # Yedekle/Yedeksiz-Kaldır düğmelerinin mevcut davranışını bozmuyor.
        win = _make_window()
        win._set_busy(True)
        self.assertFalse(win._backup_button.get_sensitive())
        self.assertFalse(win._nobackup_button.get_sensitive())
        win._set_busy(False)
        self.assertTrue(win._backup_button.get_sensitive())
        self.assertTrue(win._nobackup_button.get_sensitive())


class CancelButtonClickBehaviorTests(unittest.TestCase):
    def test_clicking_cancel_before_busy_still_closes_the_window(self):
        # İşlem başlamadan önceki gerçek iptal davranışı (sadece pencereyi
        # kapatmak) DEĞİŞMEMELİ - kullanıcı henüz "Kaldır"a basmadıysa
        # Vazgeç hâlâ normal bir "pencereyi kapat" düğmesidir.
        win = _make_window()
        win.close = mock.Mock()
        win._cancel_button.emit("clicked")
        win.close.assert_called_once()

    def test_clicking_cancel_before_busy_never_touches_any_backend(self):
        win = _make_window()
        backend = mock.Mock(id="pacman")
        win._main._backends = [backend]
        win.close = mock.Mock()
        win._cancel_button.emit("clicked")
        backend.remove.assert_not_called()

    def test_disabled_cancel_button_does_not_fire_clicked_on_a_real_click(self):
        # GTK4 kuralı: devre dışı (sensitive=False) bir widget gerçek bir
        # fare tıklamasından "clicked" sinyali ALMAZ. Burada .emit() ile
        # sinyali DOĞRUDAN tetiklemiyoruz (o, devre dışı olsa da her zaman
        # çalışır) - sadece get_sensitive()'ın False olduğunu, yani gerçek
        # bir tıklamanın GTK tarafından hiç işleme alınmayacağını doğruluyoruz.
        win = _make_window()
        win._set_busy(True)
        self.assertFalse(win._cancel_button.get_sensitive())


if __name__ == "__main__":
    unittest.main()
