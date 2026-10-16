"""removal.py: RemovalWindow'un kalıntı listesinde Wine registry
girdileri artık DÜRÜST bir şekilde gösteriliyor.

Kök gerçek (leftovers.py'nin kendi docstring'i ve backends/wine.py
_force_remove ile doğrulandı): bir registry kalıntısının işareti
kaldırılsa da, "Yedekle ve Kaldır" seçilse de, WineBackend.remove()
paketin kendisini kaldırırken bu kaydı HER DURUMDA siler - kullanıcıya
burada GERÇEK bir seçenek sunulmuyordu, üstüne "yedekle ve kaldır"
seçilirse bu girdi backup.py'nin arşivine de GİRMİYOR (gerçek bir dosya
yolu değil). Eski davranış bunu hiç belirtmiyordu. Bu test, kontrol
kutusunun artık kilitli (her zaman işaretli, etkileşimsiz) ve "yedeğe
dahil edilmez" notunun gösterildiğini doğruluyor - SIRADAN (dosya
tabanlı) kalıntıların davranışının değişmediğini de.

Gerçek paket kaldırma/sistem değişikliği YAPILMAZ - `_on_scanned()`
doğrudan, hazırlanmış bir kalıntı listesiyle çağrılır.

Çalıştırma (GTK gerektiği için ana sistemde):
    flatpak-spawn --host env PYTHONPATH=/mnt/D/packwarden/src \
        python3 -m unittest tests.test_removal_registry_backup_honesty -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import gi  # noqa: E402
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from bulkuninstaller.backends.base import Package  # noqa: E402
from bulkuninstaller.i18n import _  # noqa: E402
from bulkuninstaller.leftovers import Leftover  # noqa: E402
from bulkuninstaller import removal  # noqa: E402


def _pkg():
    return Package(
        id="Foo", name="Foo", version="1.0", size=0, description="",
        source="wine",
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


class RegistryLeftoverCheckboxIsLockedTests(unittest.TestCase):
    def test_registry_checkbox_is_always_checked_and_not_interactive(self):
        win = _make_window()
        registry_item = Leftover(
            path="Windows Registry/Foo", size=0, kind="registry",
            registry_prefix="/tmp/some-prefix",
            registry_key="HKLM\\Software\\Foo",
        )
        win._on_scanned([("Registry entry", [registry_item])])

        self.assertEqual(len(win._checks), 1)
        check, item = win._checks[0]
        self.assertIs(item, registry_item)
        self.assertTrue(check.get_active())
        self.assertFalse(check.get_sensitive())

    def test_registry_checkbox_has_an_explanatory_tooltip(self):
        win = _make_window()
        registry_item = Leftover(
            path="Windows Registry/Foo", size=0, kind="registry",
            registry_prefix="/tmp/some-prefix",
            registry_key="HKLM\\Software\\Foo",
        )
        win._on_scanned([("Registry entry", [registry_item])])
        check, _item = win._checks[0]
        self.assertTrue(check.get_tooltip_text())

    def test_registry_row_subtitle_notes_it_is_not_backed_up(self):
        win = _make_window()
        registry_item = Leftover(
            path="Windows Registry/Foo", size=0, kind="registry",
            registry_prefix="/tmp/some-prefix",
            registry_key="HKLM\\Software\\Foo",
        )
        win._on_scanned([("Registry entry", [registry_item])])
        check, _item = win._checks[0]
        row = check.get_ancestor(Adw.ActionRow)
        self.assertIn(_("not included in the backup"), row.get_subtitle())


class OrdinaryFileLeftoverCheckboxUnaffectedTests(unittest.TestCase):
    """Gerileme kontrolü: sıradan (dosya tabanlı) kalıntıların kontrol
    kutusu hâlâ eskisi gibi tıklanabilir, hiçbir not eklenmiyor."""

    def test_ordinary_leftover_checkbox_stays_interactive(self):
        win = _make_window()
        file_item = Leftover(path="/home/x/.config/foo", size=100)
        win._on_scanned([("Settings", [file_item])])

        check, item = win._checks[0]
        self.assertIs(item, file_item)
        self.assertTrue(check.get_sensitive())
        self.assertFalse(check.get_tooltip_text())

    def test_ordinary_leftover_subtitle_is_unchanged(self):
        win = _make_window()
        file_item = Leftover(path="/home/x/.config/foo", size=100)
        win._on_scanned([("Settings", [file_item])])
        check, _item = win._checks[0]
        row = check.get_ancestor(Adw.ActionRow)
        self.assertEqual(row.get_subtitle(), "/home/x/.config/foo")


if __name__ == "__main__":
    unittest.main()
