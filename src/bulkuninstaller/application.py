import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

import os  # noqa: E402
import threading  # noqa: E402

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, VERSION, host, prefs  # noqa: E402
from .backends.selfinstall import INSTALL_DIR as SELF_INSTALL_DIR  # noqa: E402
from .backends.selfinstall import SelfInstallBackend  # noqa: E402
from .i18n import _  # noqa: E402
from .window import MainWindow  # noqa: E402


def _seed_default_ignored_packwarden():
    """Taze bir kurulumda "Yok Sayilanlar" listesine PackWarden'in
    KENDISINI varsayilan olarak ekler - kullanici uygulamayi ilk actiginda
    kendi kendini "kullanilmiyor" diye onermesin. SADECE bu tek paket icin
    ve SADECE settings.json daha once hic olusturulmamissa (bkz.
    prefs.is_first_run()) calisir - kullanici sonradan elle "Geri al"
    derse (yoksayilanlardan cikarirsa) bir daha GERI EKLENMEZ, cunku o
    andan sonra dosya zaten var ve is_first_run() bir sonraki acilista
    False doner.

    Anahtar formati _pkg_key(pkg) = f"{pkg.source}:{pkg.id}" ile AYNI
    olmali (bkz. unusedwindow.py) - burada gercek bir tarama yapmadan,
    hangi kurulum yontemi GECERLI oldugunu doğrudan kontrol ederek
    hesapliyoruz: Flatpak sandbox icindeyse Flatpak kimligi, degilse
    (install.sh ile kurulduysa) kendi kurulum yolu.
    """
    if not prefs.is_first_run():
        return
    if host.IN_FLATPAK:
        key = f"flatpak:{APP_ID}"
    elif SelfInstallBackend().is_available():
        key = f"{SelfInstallBackend.id}:{os.path.expanduser(SELF_INSTALL_DIR)}"
    else:
        return  # pacman/AUR gibi baska bir yontemle kuruldu - kendini
        # tanimlayacak guvenilir bir anahtarimiz yok, hic dokunma.
    prefs.set("ignored_unused_programs", {key: "PackWarden"})


class PackWardenApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )

        _seed_default_ignored_packwarden()
        self.apply_theme(prefs.get("theme"))
        self._settings_dialog = None
        self._unused_window = None

        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", self._on_about)
        self.add_action(about_action)

        settings_action = Gio.SimpleAction.new("settings", None)
        settings_action.connect("activate", self._on_settings)
        self.add_action(settings_action)
        self.set_accels_for_action("app.settings", ["<Ctrl>comma"])

        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Ctrl>q"])
        self.set_accels_for_action("win.search", ["<Ctrl>f"])

    def apply_theme(self, theme):
        style_manager = Adw.StyleManager.get_default()

        if theme == "dark":
            style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        elif theme == "light":
            style_manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        else:
            style_manager.set_color_scheme(Adw.ColorScheme.DEFAULT)

    def do_activate(self):
        # Uygulama simgesi kurulu temada yoksa (geliştirme/betik kurulumu)
        # kendi simge klasörümüzü aramaya ekle; Hakkında penceresi ve
        # görev çubuğu logoyu böyle bulur
        icons_dir = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "..", "..", "data", "icons"
        ))
        if os.path.isdir(icons_dir):
            theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
            if icons_dir not in (theme.get_search_path() or []):
                theme.add_search_path(icons_dir)

        window = self.props.active_window
        if not window:
            window = MainWindow(application=self)
        window.present()

        if prefs.get("auto_update"):
            self._check_for_update_silently()

    def _check_for_update_silently(self):
        def worker():
            from .updater import fetch_remote_version
            remote = fetch_remote_version()
            GLib.idle_add(self._on_auto_update_checked, remote)

        threading.Thread(target=worker, daemon=True).start()

    def _on_auto_update_checked(self, remote):
        from .updatewindow import open_if_newer
        open_if_newer(self, remote)
        return GLib.SOURCE_REMOVE

    def _on_settings(self, *_args):
        from .settings import SettingsDialog
        self.close_unused_if_open()
        self._settings_dialog = SettingsDialog(self)
        self._settings_dialog.connect(
            "closed", lambda *_a: setattr(self, "_settings_dialog", None)
        )
        self._settings_dialog.present(self.props.active_window)

    def close_settings_if_open(self):
        """Kullanılmayan Programlar gibi ayrı (gerçek) bir pencere
        üzerine açılmadan ÖNCE çağrılır - Ayarlar, ana pencerenin
        İÇİNE gömülü bir panel olduğu için konumu/boyutu Kullanılmayan
        Programlar'ınkiyle (ayrı bir pencere, konumunu pencere yöneticisi
        belirliyor) tam örtüşmüyor; ikisi aynı anda açık kalırsa Ayarlar'ın
        alt kısmı Kullanılmayan Programlar'ın arkasından taşıp görünüyordu
        (canlı doğrulandı). İkisinin asla aynı anda görünmemesini
        sağlayarak bu görsel çakışmayı engeller."""
        if self._settings_dialog is not None:
            self._settings_dialog.force_close()

    def close_unused_if_open(self):
        """Kullanılmayan Programlar açılmadan ÖNCE close_settings_if_open()
        çağrılıyor (yukarıda); bunun simetriği - Ayarlar açılmadan önce
        TERS yönde de aynı çakışma yaşanıyordu (Kullanılmayan Programlar
        zaten açıkken Ayarlar da açılınca, canlı doğrulandı), çünkü ikisi
        de aynı iki konumlandırma sistemiyle çakışıyor, yön farketmiyor."""
        if self._unused_window is not None:
            self._unused_window.close()

    def _on_about(self, *_args):
        about = Adw.AboutDialog(
            application_name="PackWarden",
            application_icon=APP_ID,
            version=VERSION,
            developer_name="MuhammetHub111",
            website="https://github.com/MuhammetHub111/packwarden",
            issue_url="https://github.com/MuhammetHub111/packwarden/issues",
            license_type=Gtk.License.GPL_3_0,
            comments=_(
                "PackWarden is a bulk application manager for Linux. "
                "It shows all the applications installed on your system "
                "in one window and lets you remove the ones you no "
                "longer need, cleanly and safely, on any distribution."
            ),
        )
        about.present(self.props.active_window)


def main(argv):
    app = PackWardenApp()
    return app.run(argv)
