"""Güncelleme penceresi.

Ayarlar'daki "Güncellemeleri Denetle" yeni sürüm bulunca açılır:
ilerleme çubuğu (yüzde + MB), açılır ayrıntı günlüğü ve altta
İptal / Güncelleme notları / Güncelle düğmeleri.
"""

import threading

from gi.repository import Adw, GLib, Gtk

from . import VERSION
from .backends.base import format_size
from .i18n import _
from .updater import RELEASES_URL, download_and_install, restart_app


class UpdateWindow(Adw.Window):
    def __init__(self, parent, app, remote_version):
        super().__init__(
            transient_for=parent,
            modal=True,
            title=_("PackWarden Update"),
            default_width=460,
            default_height=360,
        )
        self._app = app
        self._cancelled = False
        self._running = False
        self._finished = False

        header = Adw.HeaderBar()

        heading = Gtk.Label(
            label=_("PackWarden {version} is ready").format(
                version=remote_version
            ),
            css_classes=["title-2"],
        )
        subtitle = Gtk.Label(
            label=_("Current version: {version}").format(version=VERSION),
            css_classes=["dim-label"],
        )

        self._bar = Gtk.ProgressBar(show_text=True, text="0%")
        self._size_label = Gtk.Label(label="", css_classes=["dim-label"])

        self._log_view = Gtk.Label(
            label="", xalign=0, yalign=0, wrap=True,
            css_classes=["dim-label", "monospace", "caption"],
        )
        log_scroll = Gtk.ScrolledWindow(
            child=self._log_view, min_content_height=90,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
        )
        expander = Gtk.Expander(label=_("Details"), child=log_scroll)

        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12,
            margin_top=18, margin_bottom=12, margin_start=18, margin_end=18,
        )
        content.append(heading)
        content.append(subtitle)
        content.append(self._bar)
        content.append(self._size_label)
        content.append(expander)

        self._cancel_button = Gtk.Button(label=_("Cancel"))
        self._cancel_button.connect("clicked", self._on_cancel)

        notes_button = Gtk.Button(label=_("Release notes"))
        notes_button.connect("clicked", self._on_notes)

        self._update_button = Gtk.Button(
            label=_("Update"), css_classes=["suggested-action"]
        )
        self._update_button.connect("clicked", self._on_update)

        action_bar = Gtk.ActionBar()
        action_bar.pack_start(self._cancel_button)
        action_bar.pack_start(notes_button)
        action_bar.pack_end(self._update_button)

        toolbar_view = Adw.ToolbarView(content=content)
        toolbar_view.add_top_bar(header)
        toolbar_view.add_bottom_bar(action_bar)
        self.set_content(toolbar_view)

    # ---------------- Düğmeler ----------------

    def _on_notes(self, _button):
        Gtk.UriLauncher(uri=RELEASES_URL).launch(self, None, None)

    def _on_cancel(self, _button):
        if self._running:
            self._cancelled = True
        else:
            self.close()

    def _on_update(self, _button):
        if self._finished:
            restart_app()
            self._app.quit()
            return
        if self._running:
            return
        self._running = True
        self._update_button.set_sensitive(False)

        def progress(fraction, done, total):
            GLib.idle_add(self._on_progress, fraction, done, total)

        def log(message):
            GLib.idle_add(self._on_log, message)

        def worker():
            try:
                ok = download_and_install(
                    progress, log, lambda: self._cancelled
                )
            except Exception as exc:
                log(_("Update failed: {error}").format(error=exc))
                ok = False
            GLib.idle_add(self._on_done, ok)

        threading.Thread(target=worker, daemon=True).start()

    # ---------------- Ana döngü geri çağrıları ----------------

    def _on_progress(self, fraction, done, total):
        if fraction is None:
            self._bar.pulse()
            self._bar.set_text(format_size(done))
        else:
            self._bar.set_fraction(fraction)
            self._bar.set_text(f"{int(fraction * 100)}%")
        if total:
            self._size_label.set_label(
                f"{format_size(done)} / {format_size(total)}"
            )
        else:
            self._size_label.set_label(format_size(done))
        return GLib.SOURCE_REMOVE

    def _on_log(self, message):
        current = self._log_view.get_label()
        self._log_view.set_label(
            (current + "\n" + _(message)).strip()
        )
        return GLib.SOURCE_REMOVE

    def _on_done(self, ok):
        self._running = False
        if ok:
            self._finished = True
            self._bar.set_fraction(1.0)
            self._bar.set_text("100%")
            self._on_log(_("Update installed. Restart the app to use it."))
            self._update_button.set_label(_("Restart Now"))
            self._update_button.set_sensitive(True)
            self._cancel_button.set_label(_("Close"))
        else:
            self._update_button.set_label(_("Update"))
            self._update_button.set_sensitive(True)
            self._cancelled = False
        return GLib.SOURCE_REMOVE
