"""unusedwindow.py: Kullanılmayan Programlar penceresinin PackWarden
0.15.3'te Adw.Dialog'dan gerçek, bağımsız bir Adw.Window'a geçişi.

Bu geçişin amacı: pencere artık pencere yöneticisinin kendi native
resize/konum mekanizmasını kullanıyor (dialog_resize.py'nin elle
sürükleme/köşe-kilidi koduna artık hiç ihtiyaç yok) ve Ayarlar'la aynı
tek-dialog-slotunu paylaşmıyor (bkz. application.py'deki eski
close_settings_if_open/close_unused_if_open çakışması).

Gerçek tarama/kullanım verisi, gerçek systemd, gerçek ~/.config/
bulkuninstaller/ HİÇ kullanılmaz - `threading.Thread`/`GLib.idle_add`/
`GLib.timeout_add_seconds` senkron hale getirilir, `usage.*` ve
`prefs.*` tamamen taklit edilir.

Çalıştırma (GTK gerektiği için ana sistemde):
    flatpak-spawn --host env PYTHONPATH=/mnt/D/packwarden/src \
        python3 -m unittest tests.test_unused_window_architecture -v
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

from bulkuninstaller import unusedwindow  # noqa: E402
from bulkuninstaller.unusedwindow import UnusedAppsWindow  # noqa: E402


class _ImmediateThread:
    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        self._target()


class _FakeMainWindow(Gtk.Window):
    """UnusedAppsWindow'un __init__/_scan sirasinda main_window'dan
    bekledigi minimum yuzey - gercek ana pencere/tarama hic kurulmaz.
    Gercek bir Gtk.Window olmak ZORUNLU: Adw.Window'un transient_for'u
    GObject seviyesinde tip kontrolu yapiyor, duck-typed bir nesne
    kabul etmiyor."""

    def __init__(self):
        super().__init__()
        self._items = []
        self._launcher_map = {}

    def _is_app(self, _pkg):
        return True

    def get_application(self):
        return object()


def _make_window(prefs_values=None, main=None):
    prefs_values = dict(prefs_values or {"background_usage_detection": True})
    main = main or _FakeMainWindow()
    patches = [
        mock.patch(
            "bulkuninstaller.unusedwindow.threading.Thread",
            side_effect=lambda target=None, daemon=None: _ImmediateThread(target),
        ),
        mock.patch(
            "bulkuninstaller.unusedwindow.GLib.idle_add",
            side_effect=lambda func, *args: func(*args),
        ),
        mock.patch(
            "bulkuninstaller.unusedwindow.GLib.timeout_add_seconds",
            return_value=1,
        ),
        mock.patch(
            "bulkuninstaller.unusedwindow.prefs.get",
            side_effect=lambda key: prefs_values.get(key),
        ),
        mock.patch(
            "bulkuninstaller.unusedwindow.prefs.set",
            side_effect=lambda key, value: prefs_values.__setitem__(key, value),
        ),
        mock.patch(
            "bulkuninstaller.unusedwindow.usage.scan_and_record",
        ),
        mock.patch(
            "bulkuninstaller.unusedwindow.usage.get_seen_map",
            return_value={},
        ),
    ]
    for p in patches:
        p.start()
    try:
        window = UnusedAppsWindow(main)
    finally:
        for p in reversed(patches):
            p.stop()
    return window, prefs_values, main


class WindowClassTests(unittest.TestCase):
    def test_is_a_real_independent_adw_window_not_a_dialog(self):
        window, _prefs, _main = _make_window()
        self.assertIsInstance(window, Adw.Window)
        self.assertNotIsInstance(window, Adw.Dialog)

    def test_initial_size_is_preserved_at_780x920(self):
        window, _prefs, _main = _make_window()
        self.assertEqual(window.get_default_size(), (780, 920))

    def test_transient_for_the_given_main_window_is_set(self):
        main = _FakeMainWindow()
        window, _prefs, _main = _make_window(main=main)
        self.assertIs(window.get_transient_for(), main)

    def test_content_is_set_via_set_content_not_set_child(self):
        # Adw.Window'da icerik set_content() ile verilir - Adw.Dialog'un
        # set_child()'i degil. get_content() None DONMEMELI.
        window, _prefs, _main = _make_window()
        self.assertIsNotNone(window.get_content())


class NoManualResizeMachineryTests(unittest.TestCase):
    """dialog_resize.add_resize_grip ve onunla gelen kose-kilidi/satir-
    pinleme makinesi artik bu pencere icin KULLANILMIYOR - WM'in kendi
    native resize'i yeterli. Bu nitelikler/metodlar artik hic olmamali."""

    def test_add_resize_grip_is_not_imported_by_this_module(self):
        self.assertFalse(hasattr(unusedwindow, "add_resize_grip"))

    def test_row_pinning_state_no_longer_exists(self):
        window, _prefs, _main = _make_window()
        for attr in (
            "_row_count_pinned", "_row_count_pin_pending",
            "_scrolled_pinned_height", "_window_pinned_height",
        ):
            self.assertFalse(
                hasattr(window, attr), f"{attr} hala mevcut olmamali"
            )

    def test_row_pinning_methods_no_longer_exist(self):
        window, _prefs, _main = _make_window()
        for name in (
            "_grow_for_banner", "_row_height", "_current_chrome_height",
            "_on_live_resize", "_on_manual_resize",
            "_pin_scrolled_for_row_count", "present_when_ready",
            "_present_if_pending", "_present_no_anim",
        ):
            self.assertFalse(
                hasattr(window, name), f"{name} hala mevcut olmamali"
            )

    def test_scrolled_list_simply_fills_available_space(self):
        window, _prefs, _main = _make_window()
        self.assertTrue(window._scrolled.get_vexpand())


class HintBannerNoLongerResizesWindowTests(unittest.TestCase):
    def test_showing_the_banner_only_toggles_its_own_visibility(self):
        window, _prefs, _main = _make_window(
            {"background_usage_detection": True}
        )
        window._hint_banner.set_visible(False)
        window._set_hint_banner_visible(True)
        self.assertTrue(window._hint_banner.get_visible())

    def test_hiding_the_banner_only_toggles_its_own_visibility(self):
        window, _prefs, _main = _make_window(
            {"background_usage_detection": False}
        )
        window._hint_banner.set_visible(True)
        window._set_hint_banner_visible(False)
        self.assertFalse(window._hint_banner.get_visible())


class CloseLifecycleTests(unittest.TestCase):
    # _make_window(), GLib.timeout_add_seconds'i SADECE pencere
    # KURULURKEN (constructor icinde) taklit ediyor - sahte, sabit bir
    # id (1) donduruyor. Kapanma burada, kurulumdan SONRA, ayri bir
    # adimda tetiklendigi icin asagidaki GERCEK GLib.source_remove(1)
    # cagrisi o sahte id'yi kaldirmaya calisir; bu, surecin GERCEKTEN
    # kullandigi ilk birkac GLib kaynak id'siyle rastlantisal olarak
    # cakisip cakismamasina gore bazen zararsiz ama gurultulu bir GLib
    # uyarisi basiyordu ("Source ID 1 was not found" - canli
    # dogrulandi: tek basina calisinca hic cikmiyor, bu sinif
    # icindeki ONCEKI bir test ayni sahte id'yi zaten "kullanmis"
    # olunca cikiyor). Gercek calisan uygulamada id HER ZAMAN gercek
    # ve taze oldugu icin bu sadece bir test taklit artifact'i - ama
    # gereksiz/yanlis-alarm uyarilari testteki gercek sorunlari
    # gizleyebilir, bu yuzden source_remove de taklit ediliyor.
    def setUp(self):
        self._source_remove_patch = mock.patch(
            "bulkuninstaller.unusedwindow.GLib.source_remove"
        )
        self._source_remove_patch.start()
        self.addCleanup(self._source_remove_patch.stop)

    def test_close_request_cancels_the_rescan_timer(self):
        window, _prefs, _main = _make_window()
        self.assertIsNotNone(window._rescan_source)
        allow_close = window._on_close_request(window)
        self.assertFalse(allow_close)
        self.assertIsNone(window._rescan_source)

    def test_close_request_signal_is_connected_and_allows_closing(self):
        # Adw.Window'da "closed" sinyali yok (Adw.Dialog'a ozel) - dogru
        # sinyal "close-request" (Gtk.Window). __init__ bu sinyale
        # basariyla baglanabildigine gore (bkz. _make_window, hata
        # firlatmadi) sinyal gercekten var; burada da emit() ile
        # handler'in gercekten calisip "kapanmaya izin ver" (False)
        # dondugunu dogruluyoruz.
        window, _prefs, _main = _make_window()
        allow_close = window.emit("close-request")
        self.assertFalse(allow_close)


class ToastCssProviderIsInstalledOnlyOnceTests(unittest.TestCase):
    """PackWarden 0.15.3 saglik kontrolu bulgusu: her yeni UnusedAppsWindow
    (yani pencere HER acilisinda) kendi Gtk.CssProvider'ini olusturup
    Gdk.Display'e ekliyor, hic kaldirmiyordu - pencereyi ac/kapat
    dongusunde ayni kurallara sahip saglayicilar kalici olarak
    birikiyordu (canli bellek olcumuyle dogrulandi). Duzeltme: tek,
    modul seviyesinde paylasilan bir saglayici + "zaten kuruldu mu"
    korumasi (bkz. unusedwindow._ensure_toast_css_installed)."""

    def setUp(self):
        # Modul seviyesindeki paylasilan durumu her testten once sifirla
        # - baska testlerin/daha once acilmis gercek pencerelerin bu
        # bayragi coktan True birakmis olmasi, buradaki "ilk cagri
        # gercekten ekliyor mu" iddiasini gizleyebilir.
        self._original = unusedwindow._toast_css_provider
        unusedwindow._toast_css_provider = None

    def tearDown(self):
        unusedwindow._toast_css_provider = self._original

    def test_creating_several_windows_adds_the_provider_exactly_once(self):
        with mock.patch(
            "bulkuninstaller.unusedwindow.Gtk.StyleContext.add_provider_for_display",
        ) as add_provider:
            _make_window()
            _make_window()
            _make_window()
        add_provider.assert_called_once()

    def test_the_shared_provider_object_is_reused_across_windows(self):
        _make_window()
        first = unusedwindow._toast_css_provider
        self.assertIsNotNone(first)
        _make_window()
        self.assertIs(unusedwindow._toast_css_provider, first)


if __name__ == "__main__":
    unittest.main()
