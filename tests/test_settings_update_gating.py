"""settings.py: Ayarlar'daki "Güncellemeler" bölümü, kendiliğinden
güncelleme mekanizmasının (updater.py) GERÇEKTEN işe yarayabileceği
kurulumlarda gösterilir.

updater.py'nin download_and_install()/restart_app() akışı yalnızca
install.sh ile yapılan self-install kurulumu için tasarlandı. Flatpak
içinde (host.IN_FLATPAK) ~/.local/share/packwarden sandbox'ta
yönlendirilir ve gerçek /app ağacına hiç dokunmaz; pacman/AUR gibi bir
dağıtım paketiyle kurulduğunda da (SelfInstallBackend.is_available()
False) aynı yol o kurulumla hiç ilişkili olmayan ayrı bir kopya
oluşturur - ikisinde de "Check for Updates"/"Update" hiçbir şey
değiştirmeden "başarılı" görünür. Bu testler, bu iki durumda gerçek
güncelleme arayüzünün (switch + "Check for Updates" düğmesi) hiç
kurulmadığını, ama NORMAL bir self-install kurulumunda hâlâ eskisi
gibi kurulduğunu doğrular.

Gerçek ağ isteği/güncelleme/sistem değişikliği YAPILMAZ -
`host.IN_FLATPAK` ve `SelfInstallBackend.is_available()` taklit
edilir, `SettingsDialog` hiç `present()` edilmez.

Çalıştırma (GTK gerektiği için ana sistemde):
    flatpak-spawn --host env PYTHONPATH=/mnt/D/packwarden/src \
        python3 -m unittest tests.test_settings_update_gating -v
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import gi  # noqa: E402
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from bulkuninstaller.settings import SettingsDialog  # noqa: E402


def _make_dialog():
    # SettingsDialog.__init__ içinde `app`, yalnızca self._app = app
    # olarak saklanıyor - __init__ sırasında hiçbir özelliğine erişilmiyor.
    return SettingsDialog(app=object())


class UpdateUiHiddenWhenMechanismCannotWorkTests(unittest.TestCase):
    def test_flatpak_install_hides_the_real_update_ui(self):
        with mock.patch("bulkuninstaller.host.IN_FLATPAK", True), mock.patch(
            "bulkuninstaller.backends.selfinstall.SelfInstallBackend.is_available",
            return_value=True,
        ):
            dialog = _make_dialog()
        self.assertFalse(hasattr(dialog, "_check_button"))

    def test_non_selfinstall_distribution_package_hides_the_real_update_ui(self):
        # Örn. pacman/AUR ile kurulu - ~/.local/share/packwarden hiç yok.
        with mock.patch("bulkuninstaller.host.IN_FLATPAK", False), mock.patch(
            "bulkuninstaller.backends.selfinstall.SelfInstallBackend.is_available",
            return_value=False,
        ):
            dialog = _make_dialog()
        self.assertFalse(hasattr(dialog, "_check_button"))

    def test_both_conditions_true_still_hides_the_real_update_ui(self):
        with mock.patch("bulkuninstaller.host.IN_FLATPAK", True), mock.patch(
            "bulkuninstaller.backends.selfinstall.SelfInstallBackend.is_available",
            return_value=False,
        ):
            dialog = _make_dialog()
        self.assertFalse(hasattr(dialog, "_check_button"))


class UpdateUiShownForAWorkingSelfInstallTests(unittest.TestCase):
    """Gerileme kontrolü: kurulum türünü yanlış belirleyip ÇALIŞAN bir
    self-install kurulumunda güncelleme seçeneğini yanlışlıkla
    gizlememeli."""

    def test_normal_selfinstall_keeps_the_real_update_ui(self):
        with mock.patch("bulkuninstaller.host.IN_FLATPAK", False), mock.patch(
            "bulkuninstaller.backends.selfinstall.SelfInstallBackend.is_available",
            return_value=True,
        ):
            dialog = _make_dialog()
        self.assertTrue(hasattr(dialog, "_check_button"))
        self.assertTrue(dialog._check_button.get_sensitive())


if __name__ == "__main__":
    unittest.main()
