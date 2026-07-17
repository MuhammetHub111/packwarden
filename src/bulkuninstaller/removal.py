"""Kaldırma onayı penceresi.

"Seçilenleri Kaldır" düğmesine basınca açılır. Üstte kaldırılacak
paketler, altında o paketlere ait kalıntı dosyalar kategorilere
ayrılmış ve tiklenebilir hâlde listelenir. Alt çubukta üç yol vardır:
Vazgeç / Yedeksiz Kaldır / Yedekle ve Kaldır.
"""

import os
import threading

from gi.repository import Adw, Gio, GLib, Gtk

from . import prefs
from .backends.base import format_size
from .backup import create_backup
from .i18n import _
from .leftovers import find_package_leftovers, remove_leftovers
from .protected import is_protected


class RemovalWindow(Adw.Window):
    def __init__(self, main_window, items):
        super().__init__(
            transient_for=main_window,
            modal=True,
            title=_("Confirm Removal"),
            default_width=760,
            default_height=620,
        )
        self._main = main_window
        self._pkgs = [item.pkg for item in items]
        self._checks: list[tuple[Gtk.CheckButton, object]] = []
        self._busy = False
        self._protected = [pkg for pkg in self._pkgs if is_protected(pkg)]
        self._risk_acknowledged = False

        header = Adw.HeaderBar()

        self._list_box = Gtk.ListBox(
            css_classes=["boxed-list"],
            selection_mode=Gtk.SelectionMode.NONE,
            margin_top=12, margin_bottom=12, margin_start=12, margin_end=12,
            valign=Gtk.Align.START,
        )
        clamp = Adw.Clamp(maximum_size=860, child=self._list_box)
        scrolled = Gtk.ScrolledWindow(child=clamp, vexpand=True)

        # Üst bölüm: kaldırılacak paketler (her zaman görünür)
        total = sum(pkg.size for pkg in self._pkgs)
        pkg_expander = Adw.ExpanderRow(
            title=_("Packages to remove"),
            subtitle=_("{count} packages • {size}").format(
                count=len(self._pkgs), size=format_size(total) or "—"
            ),
        )
        pkg_expander.set_expanded(True)
        for pkg in self._pkgs:
            badge = f"{pkg.source} · {pkg.origin}" if pkg.origin else pkg.source
            row = Adw.ActionRow(title=pkg.name, subtitle=badge)
            if is_protected(pkg):
                row.set_subtitle(
                    badge + "  •  ⚠ " + _("critical system package")
                )
                warn = Gtk.Image(icon_name="dialog-warning-symbolic")
                warn.add_css_class("warning")
                row.add_prefix(warn)
            size_label = Gtk.Label(
                label=format_size(pkg.size),
                css_classes=["numeric", "dim-label"],
                valign=Gtk.Align.CENTER,
            )
            row.add_suffix(size_label)
            pkg_expander.add_row(row)
        self._list_box.append(pkg_expander)

        # Alt bölüm: kalıntı kategorileri (tarama bitince dolar)
        self._scan_row = Adw.ActionRow(title=_("Scanning leftovers…"))
        self._scan_row.add_prefix(Gtk.Spinner(spinning=True))
        self._list_box.append(self._scan_row)

        # Alt çubuk
        cancel_button = Gtk.Button(label=_("Cancel"))
        cancel_button.connect("clicked", lambda *_a: self.close())

        self._select_all = Gtk.CheckButton(label=_("Select all"), sensitive=False)
        self._select_all.connect("toggled", self._on_select_all)
        self._selection_label = Gtk.Label(css_classes=["dim-label"])

        self._backup_button = Gtk.Button(
            label=_("Back up and Remove"), css_classes=["suggested-action"]
        )
        self._backup_button.connect("clicked", self._on_remove, True)
        self._nobackup_button = Gtk.Button(
            label=_("Remove without Backup"), css_classes=["destructive-action"]
        )
        self._nobackup_button.connect("clicked", self._on_remove, False)
        self._spinner = Gtk.Spinner()

        action_bar = Gtk.ActionBar()
        action_bar.pack_start(cancel_button)
        action_bar.pack_start(self._select_all)
        action_bar.pack_start(self._selection_label)
        action_bar.pack_end(self._backup_button)
        action_bar.pack_end(self._nobackup_button)
        action_bar.pack_end(self._spinner)

        toolbar_view = Adw.ToolbarView(content=scrolled)
        toolbar_view.add_top_bar(header)
        toolbar_view.add_bottom_bar(action_bar)

        self._toast_overlay = Adw.ToastOverlay(child=toolbar_view)
        self.set_content(self._toast_overlay)

        self._scan()

    # ---------------- Kalıntı taraması ----------------

    def _scan(self):
        def worker():
            try:
                groups = find_package_leftovers(self._pkgs)
            except Exception:
                groups = []
            GLib.idle_add(self._on_scanned, groups)

        threading.Thread(target=worker, daemon=True).start()

    def _on_scanned(self, groups):
        self._list_box.remove(self._scan_row)

        if not groups:
            row = Adw.ActionRow(
                title=_("No leftover files found"),
                subtitle=_("These packages keep no extra files behind"),
            )
            row.add_prefix(Gtk.Image(icon_name="emblem-ok-symbolic"))
            self._list_box.append(row)
            return GLib.SOURCE_REMOVE

        for category, items in groups:
            cat_size = sum(item.size for item in items)
            expander = Adw.ExpanderRow(
                title=_(category),
                subtitle=f"{len(items)} • {format_size(cat_size) or '0'}",
            )
            expander.set_expanded(True)
            for item in items:
                check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
                check.connect("toggled", lambda *_a: self._update_selection())
                row = Adw.ActionRow(
                    title=os.path.basename(item.path),
                    subtitle=item.path,
                    activatable_widget=check,
                )
                row.add_prefix(check)
                size_label = Gtk.Label(
                    label=format_size(item.size),
                    css_classes=["numeric", "dim-label"],
                    valign=Gtk.Align.CENTER,
                )
                row.add_suffix(size_label)
                expander.add_row(row)
                self._checks.append((check, item))
            self._list_box.append(expander)

        self._select_all.set_sensitive(True)
        self._update_selection()
        return GLib.SOURCE_REMOVE

    # ---------------- Seçim ----------------

    def _selected_leftovers(self):
        return [item for check, item in self._checks if check.get_active()]

    def _on_select_all(self, check):
        active = check.get_active()
        for row_check, _item in self._checks:
            row_check.set_active(active)
        self._update_selection()

    def _update_selection(self):
        selected = self._selected_leftovers()
        if selected:
            total = sum(item.size for item in selected)
            self._selection_label.set_label(
                _("{count} selected • {size}").format(
                    count=len(selected), size=format_size(total) or "0"
                )
            )
        else:
            self._selection_label.set_label("")

    # ---------------- Kaldırma ----------------

    def _on_remove(self, _button, make_backup):
        if self._busy:
            return
        if (
            self._protected
            and prefs.get("protect_system")
            and not self._risk_acknowledged
        ):
            self._show_protection_warning(make_backup)
            return
        if make_backup:
            # Yedeğin nereye kaydedileceğini kullanıcı seçer
            dialog = Gtk.FileDialog(title=_("Choose where to save the backup"))
            dialog.set_initial_folder(
                Gio.File.new_for_path(os.path.expanduser("~"))
            )
            dialog.select_folder(self, None, self._on_backup_folder_chosen)
            return
        self._start(backup_base=None)

    def _show_protection_warning(self, make_backup):
        names = "\n".join(f"• {pkg.name}" for pkg in self._protected[:10])
        if len(self._protected) > 10:
            names += "\n…"
        dialog = Adw.AlertDialog(
            heading=_("⚠ These packages are vital to your system!"),
            body=_(
                "{names}\n\nRemoving them can leave your computer unable "
                "to start, show no display, or lose its network connection. "
                "Only continue if you know exactly what you are doing."
            ).format(names=names),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("proceed", _("Remove anyway (I accept the risk)"))
        dialog.set_response_appearance(
            "proceed", Adw.ResponseAppearance.DESTRUCTIVE
        )
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_dialog, response):
            if response == "proceed":
                self._risk_acknowledged = True
                self._on_remove(None, make_backup)

        dialog.connect("response", on_response)
        dialog.present(self)

    def _on_backup_folder_chosen(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return  # kullanıcı seçim penceresini kapattı; hiçbir şey yapma
        self._start(backup_base=folder.get_path())

    def _start(self, backup_base):
        self._set_busy(True)
        checked = self._selected_leftovers()

        groups: dict[str, list[str]] = {}
        for pkg in self._pkgs:
            groups.setdefault(pkg.source, []).append(pkg.id)

        def worker():
            backup_path = None
            if backup_base:
                try:
                    # Yedek, dosyalar henüz yerindeyken alınır
                    backup_path = create_backup(
                        self._pkgs, checked, base=backup_base
                    )
                except Exception as exc:
                    GLib.idle_add(self._on_backup_failed, str(exc))
                    return

            errors = []
            cancelled = False
            for backend in self._main._backends:
                ids = groups.get(backend.id)
                if not ids:
                    continue
                result = backend.remove(ids)
                if result.cancelled:
                    cancelled = True
                elif not result.ok:
                    tail = "\n".join(result.output.strip().splitlines()[-8:])
                    errors.append(f"{backend.display_name}:\n{tail}")

            leftover_errors = []
            if checked and not cancelled:
                leftover_errors = remove_leftovers(checked)

            GLib.idle_add(
                self._on_done,
                errors, leftover_errors, cancelled, backup_path, len(checked),
            )

        threading.Thread(target=worker, daemon=True).start()

    def _on_backup_failed(self, message):
        self._set_busy(False)
        dialog = Adw.AlertDialog(
            heading=_("Backup failed — nothing was removed"),
            body=message,
        )
        dialog.add_response("ok", _("OK"))
        dialog.present(self)
        return GLib.SOURCE_REMOVE

    def _on_done(self, errors, leftover_errors, cancelled, backup_path, cleaned):
        self._set_busy(False)

        if cancelled:
            self._toast_overlay.add_toast(
                Adw.Toast(title=_("Authorization was cancelled"))
            )
            return GLib.SOURCE_REMOVE

        if backup_path:
            self._main._toast_overlay.add_toast(
                Adw.Toast(title=_("Backup saved: {path}").format(path=backup_path))
            )
        if errors or leftover_errors:
            dialog = Adw.AlertDialog(
                heading=_("Some packages could not be removed"),
                body="\n\n".join(errors + leftover_errors),
            )
            dialog.add_response("ok", _("OK"))
            dialog.present(self._main)
        else:
            self._main._toast_overlay.add_toast(Adw.Toast(
                title=_("Uninstalled {count} packages").format(count=len(self._pkgs))
            ))
            if cleaned:
                self._main._toast_overlay.add_toast(Adw.Toast(
                    title=_("Deleted {count} leftover items").format(count=cleaned)
                ))

        self._main.refresh()
        self.close()
        return GLib.SOURCE_REMOVE

    def _set_busy(self, busy):
        self._busy = busy
        self._spinner.set_spinning(busy)
        self._backup_button.set_sensitive(not busy)
        self._nobackup_button.set_sensitive(not busy)
