from gi.repository import Adw, Gtk

from . import VERSION, prefs
from .i18n import _
from .updater import latest_version, restart_app, update_available


class SettingsDialog(Adw.PreferencesDialog):
    def __init__(self, app):
        super().__init__(title=_("Settings"))
        self._app = app

        page = Adw.PreferencesPage(
            title=_("General"), icon_name="emblem-system-symbolic"
        )
        self.add(page)

        list_group = Adw.PreferencesGroup(title=_("Package list"))
        page.add(list_group)

        apps_only_row = Adw.SwitchRow(
            title=_("Show applications only"),
            subtitle=_("Hides libraries and system packages"),
            active=bool(prefs.get("apps_only")),
        )
        apps_only_row.connect("notify::active", self._on_apps_only_changed)
        list_group.add(apps_only_row)

        safety_group = Adw.PreferencesGroup(title=_("Safety"))
        page.add(safety_group)

        protect_row = Adw.SwitchRow(
            title=_("Protect system packages"),
            subtitle=_(
                "Shows an extra warning before removing packages "
                "your system needs to run"
            ),
            active=bool(prefs.get("protect_system")),
        )
        protect_row.connect(
            "notify::active",
            lambda row, _p: prefs.set("protect_system", row.get_active()),
        )
        safety_group.add(protect_row)

        language_group = Adw.PreferencesGroup(title=_("Language"))
        page.add(language_group)

        self._language_values = ["auto", "tr", "en"]
        language_row = Adw.ComboRow(
            title=_("Interface language"),
            subtitle=_("Takes effect after restarting the app"),
            model=Gtk.StringList.new(
                [_("Automatic (system)"), "Türkçe", "English"]
            ),
        )
        current = prefs.get("language")
        if current in self._language_values:
            language_row.set_selected(self._language_values.index(current))
        language_row.connect("notify::selected", self._on_language_changed)
        language_group.add(language_row)

        has_update = update_available()
        group = Adw.PreferencesGroup(
            title=_("Updates"),
            description=(
                _("A newer version is ready. Restart to use it.")
                if has_update
                else _("You are running the latest version.")
            ),
        )
        page.add(group)

        running_row = Adw.ActionRow(
            title=_("Running version"), subtitle=VERSION
        )
        group.add(running_row)

        latest_row = Adw.ActionRow(
            title=_("Latest version on disk"), subtitle=latest_version()
        )
        group.add(latest_row)

        button = Gtk.Button(
            label=_("Update and Restart") if has_update else _("Restart App"),
            valign=Gtk.Align.CENTER,
        )
        if has_update:
            button.add_css_class("suggested-action")
        button.connect("clicked", self._on_restart)

        button_row = Adw.ActionRow(title="")
        button_row.add_suffix(button)
        group.add(button_row)

    def _on_language_changed(self, row, _pspec):
        index = row.get_selected()
        if 0 <= index < len(self._language_values):
            prefs.set("language", self._language_values[index])
            self.add_toast(Adw.Toast(
                title=_("Takes effect after restarting the app")
            ))

    def _on_apps_only_changed(self, row, _pspec):
        window = self._app.props.active_window
        if window is not None and hasattr(window, "set_apps_only"):
            window.set_apps_only(row.get_active())
        else:
            prefs.set("apps_only", row.get_active())

    def _on_restart(self, _button):
        restart_app()
        self._app.quit()
