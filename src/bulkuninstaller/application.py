import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

import os  # noqa: E402

from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

from . import APP_ID, VERSION  # noqa: E402
from .i18n import _  # noqa: E402
from .window import MainWindow  # noqa: E402


class PackWardenApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )

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
            license_type=7,  # Gtk.License.GPL_3_0
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
