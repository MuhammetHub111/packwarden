import threading

from gi.repository import Adw, GLib, Gtk

from . import VERSION, prefs, usage_service
from .i18n import _
from .updater import fetch_remote_version


class SettingsDialog(Adw.PreferencesDialog):
    def __init__(self, app):
        super().__init__(title=_("Settings"))
        self._app = app

        page = Adw.PreferencesPage(
            title=_("General"),
            icon_name="emblem-system-symbolic",
        )
        self.add(page)

        # ---------------------------------------------------------
        # Package list
        # ---------------------------------------------------------
        list_group = Adw.PreferencesGroup(title=_("Package list"))
        page.add(list_group)

        apps_only_row = Adw.SwitchRow(
            title=_("Show applications only"),
            subtitle=_("Hides libraries and system packages"),
            active=bool(prefs.get("apps_only")),
        )
        apps_only_row.connect(
            "notify::active",
            self._on_apps_only_changed,
        )
        list_group.add(apps_only_row)

        # ---------------------------------------------------------
        # Safety
        # ---------------------------------------------------------
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
            lambda row, _p: prefs.set(
                "protect_system",
                row.get_active(),
            ),
        )
        safety_group.add(protect_row)

        # Leftover deletion method
        self._leftover_delete_modes = ["trash", "permanent"]

        deletion_row = Adw.ComboRow(
            title=_("Leftover deletion method"),
            subtitle=_("Choose what happens to leftover files"),
            model=Gtk.StringList.new(
                [
                    _("Move to Trash"),
                    _("Delete permanently"),
                ]
            ),
        )

        current_mode = prefs.get("leftover_delete_mode")
        if current_mode in self._leftover_delete_modes:
            deletion_row.set_selected(
                self._leftover_delete_modes.index(current_mode)
            )

        deletion_row.connect(
            "notify::selected",
            self._on_leftover_delete_mode_changed,
        )
        safety_group.add(deletion_row)

        # ---------------------------------------------------------
        # Privacy
        # ---------------------------------------------------------
        privacy_group = Adw.PreferencesGroup(
            title=_("Privacy"),
            description=_(
                "Off by default. Everything stays on this device — no "
                "network access, no process names or window titles are "
                "stored, only a package id and a timestamp."
            ),
        )
        page.add(privacy_group)

        self._background_usage_row = Adw.SwitchRow(
            title=_("Detect app usage in the background"),
            subtitle=_(
                "Without this, app usage isn't updated while PackWarden "
                "is closed, so the Unused Apps list can be inaccurate"
            ),
            active=bool(prefs.get("background_usage_detection")),
        )
        self._background_usage_row.connect(
            "notify::active",
            self._on_background_usage_changed,
        )
        privacy_group.add(self._background_usage_row)

        # ---------------------------------------------------------
        # Language
        # ---------------------------------------------------------
        language_group = Adw.PreferencesGroup(title=_("Language"))
        page.add(language_group)

        self._language_values = ["auto", "tr", "en"]

        language_row = Adw.ComboRow(
            title=_("Interface language"),
            subtitle=_("Takes effect after restarting the app"),
            model=Gtk.StringList.new(
                [
                    _("Automatic (system)"),
                    "Türkçe",
                    "English",
                ]
            ),
        )

        current_language = prefs.get("language")
        if current_language in self._language_values:
            language_row.set_selected(
                self._language_values.index(current_language)
            )

        language_row.connect(
            "notify::selected",
            self._on_language_changed,
        )
        language_group.add(language_row)

        # ---------------------------------------------------------
        # Appearance
        # ---------------------------------------------------------
        theme_group = Adw.PreferencesGroup(title=_("Appearance"))
        page.add(theme_group)

        self._theme_values = ["system", "light", "dark"]

        theme_row = Adw.ComboRow(
            title=_("Theme"),
            subtitle=_("Choose application appearance"),
            model=Gtk.StringList.new(
                [
                    _("System default"),
                    _("Light"),
                    _("Dark"),
                ]
            ),
        )

        current_theme = prefs.get("theme")
        if current_theme in self._theme_values:
            theme_row.set_selected(
                self._theme_values.index(current_theme)
            )

        theme_row.connect(
            "notify::selected",
            self._on_theme_changed,
        )
        theme_group.add(theme_row)

        # ---------------------------------------------------------
        # Updates
        # ---------------------------------------------------------
        updates_group = Adw.PreferencesGroup(title=_("Updates"))
        page.add(updates_group)

        running_row = Adw.ActionRow(
            title=_("Running version"),
            subtitle=VERSION,
        )
        updates_group.add(running_row)

        auto_update_row = Adw.SwitchRow(
            title=_("Automatic Updates"),
            subtitle=_(
                "Automatically checks for a new version on startup and "
                "opens the update dialog when one is found"
            ),
            active=bool(prefs.get("auto_update")),
        )
        auto_update_row.connect(
            "notify::active",
            lambda row, _p: prefs.set(
                "auto_update",
                row.get_active(),
            ),
        )
        updates_group.add(auto_update_row)

        self._check_button = Gtk.Button(
            label=_("Check for Updates"),
            valign=Gtk.Align.CENTER,
            css_classes=["suggested-action"],
        )
        self._check_button.connect(
            "clicked",
            self._on_check_updates,
        )

        self._check_spinner = Gtk.Spinner(
            valign=Gtk.Align.CENTER,
        )

        check_row = Adw.ActionRow(title="")
        check_row.add_suffix(self._check_spinner)
        check_row.add_suffix(self._check_button)
        updates_group.add(check_row)

    # -------------------------------------------------------------
    # Theme
    # -------------------------------------------------------------
    def _on_theme_changed(self, row, _pspec):
        index = row.get_selected()

        if not (0 <= index < len(self._theme_values)):
            return

        theme = self._theme_values[index]
        prefs.set("theme", theme)
        self._app.apply_theme(theme)

    # -------------------------------------------------------------
    # Background usage detection
    # -------------------------------------------------------------
    def _on_background_usage_changed(self, row, _pspec):
        wanted = row.get_active()
        # systemctl senkron olarak birkaç saniye sürebilir (daemon-reload,
        # servis başlatma) — ana thread'de çağrılırsa arayüz o süre boyunca
        # tamamen kilitlenir. Diğer ayarlardaki güncelleme kontrolü gibi
        # arka plan thread'ine alınıyor.
        row.set_sensitive(False)

        def worker():
            ok, error = usage_service.set_enabled(wanted)
            GLib.idle_add(self._on_background_usage_done, row, wanted, ok, error)

        threading.Thread(target=worker, daemon=True).start()

    def _on_background_usage_done(self, row, wanted, ok, error):
        row.set_sensitive(True)
        if not ok:
            # Anahtarı gerçek duruma geri al — sinyali tekrar tetiklememek
            # için handler'ı geçici olarak susturuyoruz.
            row.handler_block_by_func(self._on_background_usage_changed)
            row.set_active(not wanted)
            row.handler_unblock_by_func(self._on_background_usage_changed)

            dialog = Adw.AlertDialog(
                heading=_("Could not change this setting"),
                body=error,
            )
            dialog.add_response("ok", _("OK"))
            dialog.present(self)
            return GLib.SOURCE_REMOVE
        prefs.set("background_usage_detection", wanted)
        return GLib.SOURCE_REMOVE

    # -------------------------------------------------------------
    # Leftover deletion method
    # -------------------------------------------------------------
    def _on_leftover_delete_mode_changed(self, row, _pspec):
        index = row.get_selected()

        if not (0 <= index < len(self._leftover_delete_modes)):
            return

        prefs.set(
            "leftover_delete_mode",
            self._leftover_delete_modes[index],
        )

    # -------------------------------------------------------------
    # Language
    # -------------------------------------------------------------
    def _on_language_changed(self, row, _pspec):
        index = row.get_selected()

        if not (0 <= index < len(self._language_values)):
            return

        prefs.set(
            "language",
            self._language_values[index],
        )

        dialog = Adw.AlertDialog(
            heading=_("Restart to apply?"),
            body=_(
                "The interface language changes after the app restarts."
            ),
        )
        dialog.add_response(
            "later",
            _("Later"),
        )
        dialog.add_response(
            "restart",
            _("Restart Now"),
        )
        dialog.set_response_appearance(
            "restart",
            Adw.ResponseAppearance.SUGGESTED,
        )
        dialog.set_default_response("restart")
        dialog.set_close_response("later")
        dialog.connect(
            "response",
            self._on_language_restart_response,
        )
        dialog.present(self)

    def _on_language_restart_response(self, _dialog, response):
        if response == "restart":
            from .updater import restart_app

            restart_app()
            self._app.quit()

    # -------------------------------------------------------------
    # Applications-only
    # -------------------------------------------------------------
    def _on_apps_only_changed(self, row, _pspec):
        window = self._app.props.active_window

        if window is not None and hasattr(window, "set_apps_only"):
            window.set_apps_only(row.get_active())
        else:
            prefs.set(
                "apps_only",
                row.get_active(),
            )

    # -------------------------------------------------------------
    # Update checking
    # -------------------------------------------------------------
    def _on_check_updates(self, _button):
        self._check_button.set_sensitive(False)
        self._check_spinner.set_spinning(True)

        def worker():
            remote = fetch_remote_version()

            GLib.idle_add(
                self._on_check_done,
                remote,
            )

        threading.Thread(
            target=worker,
            daemon=True,
        ).start()

    def _on_check_done(self, remote):
        self._check_button.set_sensitive(True)
        self._check_spinner.set_spinning(False)

        from .updatewindow import open_if_newer

        if remote is None:
            dialog = Adw.AlertDialog(
                heading=_("Could not check for updates"),
                body=_(
                    "Please check your internet connection and try again."
                ),
            )
            dialog.add_response(
                "ok",
                _("OK"),
            )
            dialog.present(self)

        elif open_if_newer(self._app, remote):
            pass

        else:
            dialog = Adw.AlertDialog(
                heading=_("You are up to date"),
                body=_("You are running the latest version."),
            )
            dialog.add_response(
                "ok",
                _("OK"),
            )
            dialog.present(self)

        return GLib.SOURCE_REMOVE