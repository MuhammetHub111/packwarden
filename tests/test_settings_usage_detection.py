"""settings.py: Ayarlar'daki "Arkaplanda kullanım algıla" anahtarının
(1) sadece gerçekten kullanılabilir olduğunda (install.sh ile kurulu,
systemd birimi var) gösterilmesi ve (2) açılışta gerçek systemd
durumuyla kayıtlı tercihin senkronize edilmesi.

Gerçek `systemctl`/gerçek dosya sistemi HİÇ kullanılmaz -
`usage_service.is_installed/is_enabled` ve `prefs.get/set` tamamen
taklit edilir; gerçek kullanıcı ayarlarına (settings.json) hiç
yazılmaz/okunmaz, gerçek systemd servisi hiç etkinleştirilmez.

Çalıştırma (GTK gerektiği için ana sistemde):
    flatpak-spawn --host env PYTHONPATH=/mnt/D/packwarden/src \
        python3 -m unittest tests.test_settings_usage_detection -v
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

from bulkuninstaller.settings import SettingsDialog  # noqa: E402


class _ImmediateThread:
    """threading.Thread'in yerine - .start() hedefi senkron çalıştırır,
    gerçek bir thread/zamanlama hiç gerekmez."""

    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        self._target()


def _make_dialog(prefs_values=None):
    prefs_values = dict(prefs_values or {})
    with mock.patch(
        "bulkuninstaller.settings.threading.Thread",
        side_effect=lambda target=None, daemon=None: _ImmediateThread(target),
    ), mock.patch(
        "bulkuninstaller.settings.GLib.idle_add",
        side_effect=lambda func, *args: func(*args),
    ), mock.patch(
        "bulkuninstaller.settings.prefs.get",
        side_effect=lambda key: prefs_values.get(key),
    ), mock.patch(
        "bulkuninstaller.settings.prefs.set",
        side_effect=lambda key, value: prefs_values.__setitem__(key, value),
    ) as prefs_set:
        # parent_window gercek bir Gtk.Window olmak ZORUNDA (Adw.Window'un
        # transient_for'u GObject seviyesinde tip kontrolu yapiyor).
        dialog = SettingsDialog(app=object(), parent_window=Gtk.Window())
    return dialog, prefs_values, prefs_set


class UsageDetectionVisibilityTests(unittest.TestCase):
    def test_switch_is_shown_when_the_systemd_unit_is_installed(self):
        with mock.patch(
            "bulkuninstaller.settings.usage_service.is_installed",
            return_value=True,
        ), mock.patch(
            "bulkuninstaller.settings.usage_service.is_enabled",
            return_value=False,
        ):
            dialog, _prefs, _set = _make_dialog(
                {"background_usage_detection": False}
            )
        self.assertTrue(hasattr(dialog, "_background_usage_row"))

    def test_info_row_is_shown_instead_when_the_unit_is_not_installed(self):
        with mock.patch(
            "bulkuninstaller.settings.usage_service.is_installed",
            return_value=False,
        ) as is_installed, mock.patch(
            "bulkuninstaller.settings.usage_service.is_enabled"
        ) as is_enabled:
            dialog, _prefs, _set = _make_dialog()
        self.assertFalse(hasattr(dialog, "_background_usage_row"))
        is_installed.assert_called()
        # Kurulu olmadığı için gerçek systemd durumu hiç sorulmamalı.
        is_enabled.assert_not_called()


class UsageDetectionStateSyncTests(unittest.TestCase):
    def test_switch_and_pref_are_corrected_when_the_real_state_differs(self):
        with mock.patch(
            "bulkuninstaller.settings.usage_service.is_installed",
            return_value=True,
        ), mock.patch(
            "bulkuninstaller.settings.usage_service.is_enabled",
            return_value=True,
        ):
            dialog, prefs_values, prefs_set = _make_dialog(
                {"background_usage_detection": False}
            )
        self.assertTrue(dialog._background_usage_row.get_active())
        self.assertTrue(prefs_values["background_usage_detection"])
        prefs_set.assert_called_once_with("background_usage_detection", True)

    def test_nothing_changes_when_the_real_state_already_matches_the_pref(self):
        with mock.patch(
            "bulkuninstaller.settings.usage_service.is_installed",
            return_value=True,
        ), mock.patch(
            "bulkuninstaller.settings.usage_service.is_enabled",
            return_value=True,
        ):
            dialog, _prefs_values, prefs_set = _make_dialog(
                {"background_usage_detection": True}
            )
        self.assertTrue(dialog._background_usage_row.get_active())
        # Durum zaten aynıydı - gereksiz bir yazma olmamalı.
        prefs_set.assert_not_called()

    def test_syncing_does_not_trigger_a_real_systemctl_toggle(self):
        # Anahtarin durumunu senkronizasyon sirasinda GUNCELLEMEK
        # (set_active), kullanicinin ELLE tiklamasiyla AYNI "notify::active"
        # yolunu (ve dolayisiyla gercek bir systemctl enable/disable
        # cagrisini) tetiklememeli - handler_block_by_func ile susturulmus
        # olmali.
        with mock.patch(
            "bulkuninstaller.settings.usage_service.is_installed",
            return_value=True,
        ), mock.patch(
            "bulkuninstaller.settings.usage_service.is_enabled",
            return_value=True,
        ), mock.patch(
            "bulkuninstaller.settings.usage_service.set_enabled"
        ) as set_enabled:
            _dialog, _prefs, _set = _make_dialog(
                {"background_usage_detection": False}
            )
        set_enabled.assert_not_called()


if __name__ == "__main__":
    unittest.main()
