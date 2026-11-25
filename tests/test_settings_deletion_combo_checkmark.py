"""settings.py: "Kalıntı silme yöntemi" açılır listesindeki onay
işaretinin, seçim değiştikten SONRA popup tekrar açıldığında doğru
seçeneğe taşındığını doğrular.

Bu dosyanın VAR OLMA SEBEBİ: gerçek kullanıcı raporu - "tık işaretler
değişmıyor ayar değiştiince tık aynı yerde kalıyor". Kök neden:
_wide_popup_factory()'nin onay işareti opacity'si sadece `bind`
sırasında BİR KEZ ayarlanıyordu; GtkListView kısa (2 öğelik) bir liste
için öğeleri geri dönüştürmediğinden `bind` bir daha hiç tetiklenmiyor,
dolayısıyla seçim değiştikten sonra onay işareti hâlâ ESKİ öğenin
yanında kalıyordu. Düzeltme: `combo_row`'un "selected-item" değişimi
dinlenip tüm bağlı kutular buna göre yeniden çiziliyor (bkz.
settings.py: _wide_popup_factory, `bound`/`refresh`).

Gerçek Adw.ComboRow + gerçek Gtk.Popover.popup() kullanılıyor (sahte/
taklit bir widget ağacı DEĞİL) - GTK gerektiği için ana sistemde
çalıştırılmalı, bkz. diğer unusedwindow/settings testleri.

Çalıştırma:
    PYTHONPATH=src python3 -m unittest discover -s tests -v
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

from bulkuninstaller.settings import SettingsDialog  # noqa: E402


def _find_all(widget, pred, out):
    if pred(widget):
        out.append(widget)
    child = widget.get_first_child()
    while child is not None:
        _find_all(child, pred, out)
        child = child.get_next_sibling()


def _find_popover(widget):
    if isinstance(widget, Gtk.Popover):
        return widget
    child = widget.get_first_child()
    while child is not None:
        found = _find_popover(child)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def _check_opacities(row):
    """{görünen metin: onay işareti opacity} - popup'taki her satır için."""
    boxes = []
    _find_all(
        row,
        lambda w: isinstance(w, Gtk.Box)
        and isinstance(w.get_first_child(), Gtk.Label)
        and isinstance(w.get_last_child(), Gtk.Image),
        boxes,
    )
    return {box.get_first_child().get_text(): box.get_last_child().get_opacity() for box in boxes}


class DeletionComboCheckmarkFollowsSelectionTests(unittest.TestCase):
    def test_checkmark_moves_to_the_newly_selected_option(self):
        # `_()` host'un dil ayarına göre Türkçe/İngilizce döndürüyor
        # (bkz. i18n._detect_lang, import anında bir kez hesaplanıyor) -
        # başlık/seçenek metinleriyle eşleştirme yapabilmek için burada
        # İngilizce'ye sabitleniyor, host'un gerçek dilinden bağımsız.
        with mock.patch(
            "bulkuninstaller.settings.usage_service.is_installed",
            return_value=False,
        ), mock.patch("bulkuninstaller.i18n._LANG", "en"):
            dialog = SettingsDialog(app=object(), parent_window=Gtk.Window())

        combos = []
        _find_all(dialog, lambda w: isinstance(w, Adw.ComboRow), combos)
        row = next(c for c in combos if c.get_title() == "Leftover deletion method")

        dialog.present()

        results = {}

        def step1():
            row.set_selected(0)  # "Move to Trash"
            popover = _find_popover(row)
            popover.popup()
            GLib.timeout_add(300, step2)
            return False

        def step2():
            results["after_index_0"] = _check_opacities(row)
            _find_popover(row).popdown()
            GLib.timeout_add(100, step3)
            return False

        def step3():
            row.set_selected(1)  # "Delete permanently"
            popover = _find_popover(row)
            popover.popup()
            GLib.timeout_add(300, step4)
            return False

        def step4():
            results["after_index_1"] = _check_opacities(row)
            loop.quit()
            return False

        GLib.timeout_add(200, step1)
        loop = GLib.MainLoop()
        GLib.timeout_add(5000, loop.quit)
        loop.run()

        self.assertIn("after_index_0", results)
        self.assertIn("after_index_1", results)

        before = results["after_index_0"]
        self.assertEqual(before.get("Move to Trash"), 1.0)
        self.assertEqual(before.get("Delete permanently"), 0.0)

        after = results["after_index_1"]
        self.assertEqual(
            after.get("Move to Trash"), 0.0,
            "Seçim değiştikten sonra eski seçeneğin onay işareti hâlâ "
            "görünür kalmamalı",
        )
        self.assertEqual(
            after.get("Delete permanently"), 1.0,
            "Onay işareti yeni seçilen seçeneğe taşınmalı",
        )


if __name__ == "__main__":
    unittest.main()
