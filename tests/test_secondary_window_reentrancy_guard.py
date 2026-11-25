"""PackWarden 0.15.3 sağlık kontrolü bulgusu: "settings"/"unused-apps"
eylemleri art arda iki kez (ör. menü öğesine çift tıklama, ya da art
arda iki D-Bus Activate çağrısı) tetiklenirse, bir koruma olmadan İKİ
AYRI pencere açılıyordu - bu, gerçek uygulamada D-Bus üzerinden art
arda iki çağrı yapılarak canlı olarak tekrar üretildi (hem Ayarlar hem
Kullanılmayan Programlar için).

Düzeltme: zaten açık bir pencere varsa (uygulamanın kendi
`_settings_dialog`/`_unused_window` referansı dolu ise) yeni bir pencere
OLUŞTURMAK yerine var olanı öne getiriyoruz (`.present()`).

Bu testler ağır gerçek `MainWindow`/`PackWardenApp` kurulumunu
GEREKTİRMEDEN, sadece koruma mantığını izole ediyor - `UnusedAppsWindow`/
`SettingsDialog` sınıflarının kendileri taklit edilir, gerçek pencere
hiç oluşturulmaz. Gerçek kullanıcı ayarlarına/sistemine dokunulmaz.

Çalıştırma (GTK gerektiği için ana sistemde):
    flatpak-spawn --host env PYTHONPATH=/mnt/D/packwarden/src \
        python3 -m unittest tests.test_secondary_window_reentrancy_guard -v
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import gi  # noqa: E402
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from bulkuninstaller import window as window_module  # noqa: E402
from bulkuninstaller import application as application_module  # noqa: E402


class _FakeDestroySignalWidget:
    """UnusedAppsWindow/SettingsDialog yerine gecen, sadece bu testin
    ihtiyac duydugu yuzeyi (connect/present) tasiyan sahte nesne.

    "close-request" ve "destroy" AYRI AYRI izleniyor - gercek GTK'da da
    bu ikisi farkli zamanlarda ateslenir (close-request ONCE, senkron;
    destroy biraz SONRA) ve asil duzeltme tam da bu farka dayaniyor."""

    def __init__(self, *_args, **_kwargs):
        self.present_calls = 0
        self._destroy_handler = None
        self._close_request_handler = None

    def connect(self, signal, handler):
        if signal == "destroy":
            self._destroy_handler = handler
        elif signal == "close-request":
            self._close_request_handler = handler

    def present(self):
        self.present_calls += 1


class UnusedAppsWindowDoubleOpenGuardTests(unittest.TestCase):
    def test_second_trigger_before_close_reuses_the_existing_window_not_a_new_one(self):
        fake_window = _FakeDestroySignalWidget()

        class _FakeApp:
            _unused_window = None

        class _FakeMainWindow:
            def get_application(self):
                return fake_app

        fake_app = _FakeApp()
        fake_main = _FakeMainWindow()

        with mock.patch(
            "bulkuninstaller.window.UnusedAppsWindow",
            return_value=fake_window,
        ) as ctor:
            window_module.MainWindow._open_unused_apps(fake_main)
            window_module.MainWindow._open_unused_apps(fake_main)

        ctor.assert_called_once()
        self.assertEqual(fake_window.present_calls, 2)

    def test_after_the_window_is_destroyed_a_new_trigger_opens_a_fresh_one(self):
        class _FakeApp:
            _unused_window = None

        class _FakeMainWindow:
            def get_application(self):
                return fake_app

        fake_app = _FakeApp()
        fake_main = _FakeMainWindow()
        first_window = _FakeDestroySignalWidget()
        second_window = _FakeDestroySignalWidget()

        with mock.patch(
            "bulkuninstaller.window.UnusedAppsWindow",
            side_effect=[first_window, second_window],
        ) as ctor:
            window_module.MainWindow._open_unused_apps(fake_main)
            # pencere gercekten kapandiginda "destroy" handler'i
            # app._unused_window'u None'a sifirlar - bunu simule ediyoruz.
            first_window._destroy_handler()
            window_module.MainWindow._open_unused_apps(fake_main)

        self.assertEqual(ctor.call_count, 2)

    def test_reopening_after_close_request_but_before_destroy_opens_a_fresh_window(self):
        # Gercek yaris durumunun kendisi: pencere kapanmaya BASLADI
        # (close-request ateslendi) ama GObject'in "destroy" sinyali
        # henuz gelmedi - tam da bu dar aralikta yeni bir acma istegi
        # gelirse, eski (artik kapanmakta olan) pencereye .present()
        # cagrilmamali, YENI bir pencere acilmali. Canli testte nadiren
        # (40 dongude 1) "Gtk-WARNING: A window is shown after it has
        # been destroyed" uretiyordu - bu test tam olarak bu senaryoyu
        # izole ediyor.
        class _FakeApp:
            _unused_window = None

        class _FakeMainWindow:
            def get_application(self):
                return fake_app

        fake_app = _FakeApp()
        fake_main = _FakeMainWindow()
        first_window = _FakeDestroySignalWidget()
        second_window = _FakeDestroySignalWidget()

        with mock.patch(
            "bulkuninstaller.window.UnusedAppsWindow",
            side_effect=[first_window, second_window],
        ) as ctor:
            window_module.MainWindow._open_unused_apps(fake_main)
            # close-request ateslendi (WM kapanmayi istedi) - "destroy"
            # HENUZ gelmedi, kasitli olarak cagirmiyoruz.
            first_window._close_request_handler()
            window_module.MainWindow._open_unused_apps(fake_main)

        self.assertEqual(ctor.call_count, 2)
        self.assertEqual(first_window.present_calls, 1)
        self.assertEqual(second_window.present_calls, 1)


class SettingsDialogDoubleOpenGuardTests(unittest.TestCase):
    def _make_fake_app(self):
        app = application_module.PackWardenApp.__new__(
            application_module.PackWardenApp
        )
        app._settings_dialog = None
        app.props = mock.Mock(active_window=None)
        return app

    def test_second_trigger_before_close_reuses_the_existing_dialog_not_a_new_one(self):
        app = self._make_fake_app()
        fake_dialog = _FakeDestroySignalWidget()

        with mock.patch(
            "bulkuninstaller.settings.SettingsDialog",
            return_value=fake_dialog,
        ) as ctor:
            app._on_settings()
            app._on_settings()

        ctor.assert_called_once()
        self.assertEqual(fake_dialog.present_calls, 2)

    def test_after_the_dialog_is_destroyed_a_new_trigger_opens_a_fresh_one(self):
        app = self._make_fake_app()
        first_dialog = _FakeDestroySignalWidget()
        second_dialog = _FakeDestroySignalWidget()

        with mock.patch(
            "bulkuninstaller.settings.SettingsDialog",
            side_effect=[first_dialog, second_dialog],
        ) as ctor:
            app._on_settings()
            first_dialog._destroy_handler()
            app._on_settings()

        self.assertEqual(ctor.call_count, 2)

    def test_reopening_after_close_request_but_before_destroy_opens_a_fresh_dialog(self):
        # Ayni gercek yaris durumu (bkz. UnusedAppsWindow tarafindaki
        # aynisi) - close-request ateslendi, "destroy" HENUZ gelmedi.
        app = self._make_fake_app()
        first_dialog = _FakeDestroySignalWidget()
        second_dialog = _FakeDestroySignalWidget()

        with mock.patch(
            "bulkuninstaller.settings.SettingsDialog",
            side_effect=[first_dialog, second_dialog],
        ) as ctor:
            app._on_settings()
            first_dialog._close_request_handler()
            app._on_settings()

        self.assertEqual(ctor.call_count, 2)
        self.assertEqual(first_dialog.present_calls, 1)
        self.assertEqual(second_dialog.present_calls, 1)


if __name__ == "__main__":
    unittest.main()
