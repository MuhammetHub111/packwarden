import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

import os  # noqa: E402
import threading  # noqa: E402

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, VERSION, prefs  # noqa: E402
from .i18n import _  # noqa: E402
from .window import MainWindow  # noqa: E402


class PackWardenApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )

        self.apply_theme(prefs.get("theme"))
        
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
        SettingsDialog(self).present(self.props.active_window)

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
