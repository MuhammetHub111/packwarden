"""Kullanılmayan uygulamalar penceresi (yalnızca test/dev sürümü).

Seçilen eşiğin (3 ay / 6 ay / 1 yıl / özel) üzerinde kullanılmadığı
KESİN olarak bilinen uygulamaları listeler. "Bilinmiyor" (last_used
None) hiç listelenmez — yeni kurulmuş/güncellenmiş bir uygulamayla
gerçekten eski bir uygulama bu şekilde ayırt edilemeyeceği için, emin
olunmadıkça hiçbir şey "kullanılmıyor" diye işaretlenmez. "last_used"
tahmini de kesin değildir, bkz. unused.py. Kaldırma işlemi mevcut
RemovalWindow akışını yeniden kullanır.
"""

import os
import threading
import time

from gi.repository import Adw, GLib, Gtk

from . import prefs, usage
from .backends.base import format_size
from .i18n import _
from .removal import RemovalWindow
from .unused import last_used

PRESETS_DAYS = (90, 180, 365)


class _Item:
    """RemovalWindow'un beklediği .pkg arayüzü için ince sarmalayıcı."""

    def __init__(self, pkg):
        self.pkg = pkg


def _format_last_used(ts):
    if ts is None:
        return _("Unknown")
    days = max(0, int((time.time() - ts) // 86400))
    if days < 30:
        return _("{days} days ago").format(days=days)
    months = days // 30
    if months < 12:
        return _("{months} months ago").format(months=months)
    years = days // 365
    return _("{years} years ago").format(years=years)


class UnusedAppsWindow(Adw.Window):
    def __init__(self, main_window):
        super().__init__(
            transient_for=main_window,
            modal=True,
            title=_("Unused apps"),
            default_width=640,
            default_height=560,
        )
        self._main = main_window
        self._data: list[tuple[object, float | None]] = []
        self._checks: list[tuple[Gtk.CheckButton, object]] = []

        # Kullanıcının en son seçtiği eşik hatırlanır — pencere her
        # açıldığında 6 aya sıfırlanması can sıkıcıydı.
        saved_preset = prefs.get("unused_threshold_preset")
        if not isinstance(saved_preset, int) or not 0 <= saved_preset <= 3:
            saved_preset = 1
        saved_custom_days = prefs.get("unused_threshold_custom_days")
        if not isinstance(saved_custom_days, int) or not 1 <= saved_custom_days <= 3650:
            saved_custom_days = 180
        self._threshold_days = (
            saved_custom_days if saved_preset == 3 else PRESETS_DAYS[saved_preset]
        )

        header = Adw.HeaderBar()

        self._threshold_row = Adw.ComboRow(
            title=_("Not used for"),
            model=Gtk.StringList.new([
                _("3 months"), _("6 months"), _("1 year"), _("Custom"),
            ]),
            selected=saved_preset,
        )
        self._threshold_row.connect("notify::selected", self._on_threshold_changed)

        self._custom_row = Adw.SpinRow.new_with_range(1, 3650, 1)
        self._custom_row.set_title(_("Custom (days)"))
        self._custom_row.set_value(saved_custom_days)
        self._custom_row.set_visible(saved_preset == 3)
        self._custom_row.connect("notify::value", self._on_threshold_changed)

        settings_group = Adw.PreferencesGroup()
        settings_group.add(self._threshold_row)
        settings_group.add(self._custom_row)

        self._list_box = Gtk.ListBox(
            css_classes=["boxed-list"],
            selection_mode=Gtk.SelectionMode.NONE,
            valign=Gtk.Align.START,
        )
        self._scan_row = Adw.ActionRow(title=_("Scanning applications…"))
        self._scan_row.add_prefix(Gtk.Spinner(spinning=True))
        self._list_box.append(self._scan_row)

        self._empty_status = Adw.StatusPage(
            icon_name="emblem-ok-symbolic",
            title=_("No unused applications found"),
            description=_("Every scanned app was used within the threshold"),
            vexpand=True,
            visible=False,
        )

        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12,
            margin_top=12, margin_bottom=12, margin_start=12, margin_end=12,
        )
        content.append(settings_group)
        content.append(self._list_box)
        content.append(self._empty_status)
        clamp = Adw.Clamp(maximum_size=760, child=content)
        scrolled = Gtk.ScrolledWindow(child=clamp, vexpand=True)

        cancel_button = Gtk.Button(label=_("Cancel"))
        cancel_button.connect("clicked", lambda *_a: self.close())

        self._select_all = Gtk.CheckButton(label=_("Select all"), sensitive=False)
        self._select_all.connect("toggled", self._on_select_all)
        self._selection_label = Gtk.Label(css_classes=["dim-label"])

        self._remove_button = Gtk.Button(
            label=_("Uninstall selected…"),
            css_classes=["destructive-action"],
            sensitive=False,
        )
        self._remove_button.connect("clicked", self._on_remove_clicked)

        action_bar = Gtk.ActionBar()
        action_bar.pack_start(cancel_button)
        action_bar.pack_start(self._select_all)
        action_bar.pack_start(self._selection_label)
        action_bar.pack_end(self._remove_button)

        toolbar_view = Adw.ToolbarView(content=scrolled)
        toolbar_view.add_top_bar(header)
        toolbar_view.add_bottom_bar(action_bar)
        self.set_content(toolbar_view)

        self._scan()
        # Pencere açık kaldığı sürece periyodik olarak yeniden tara —
        # kullanıcı burayı açık bırakıp başka bir uygulamaya geçerse, o
        # kullanım kapatıp yeniden açmaya gerek kalmadan yakalanır.
        self._rescan_source = GLib.timeout_add_seconds(30, self._on_rescan_tick)
        self.connect("close-request", self._on_close_request)

    def _on_rescan_tick(self):
        self._scan()
        return GLib.SOURCE_CONTINUE

    def _on_close_request(self, *_args):
        if self._rescan_source is not None:
            GLib.source_remove(self._rescan_source)
            self._rescan_source = None
        return False

    # ---------------- Tarama ----------------

    def _scan(self):
        # DEV_BUILD açıkken oyunlar zaten ana listeye (self._main._items)
        # dahil ediliyor (bkz. window.py:refresh) — burada ayrıca
        # taranmıyor, yoksa iki kez sayılır.
        packages = [
            item.pkg for item in self._main._items
            if self._main._is_app(item.pkg)
        ]
        launcher_map = self._main._launcher_map

        def worker():
            # Pencere her açıldığında/tazelendiğinde şu an fiilen çalışan
            # paketleri tespit et ve yerel kayda işle — kapanış/açılışta
            # ayar klasörüne hiç yazmayan uygulamalar bile bu sayede
            # "az önce kullanıldı" olarak doğru yakalanır (bkz. usage.py).
            usage.scan_and_record(packages, launcher_map)
            data = [(pkg, last_used(pkg)) for pkg in packages]
            GLib.idle_add(self._on_scanned, data)

        threading.Thread(target=worker, daemon=True).start()

    def _on_scanned(self, data):
        self._data = data
        self._rebuild_list()
        return GLib.SOURCE_REMOVE

    # ---------------- Eşik ----------------

    def _on_threshold_changed(self, *_args):
        preset = self._threshold_row.get_selected()
        is_custom = preset == 3
        self._custom_row.set_visible(is_custom)
        custom_days = int(self._custom_row.get_value())
        self._threshold_days = custom_days if is_custom else PRESETS_DAYS[preset]

        prefs.set("unused_threshold_preset", preset)
        prefs.set("unused_threshold_custom_days", custom_days)

        if self._data:
            self._rebuild_list()

    # ---------------- Liste ----------------

    def _rebuild_list(self):
        child = self._list_box.get_first_child()
        while child is not None:
            self._list_box.remove(child)
            child = self._list_box.get_first_child()
        self._checks.clear()

        # "Bilinmiyor" (ts is None) burada asla listelenmez: yeni kurulmuş
        # ya da az önce güncellenmiş bir uygulamanın henüz hiç kalıntı
        # klasörü olmayabilir, bu da onu gerçekten eski/kullanılmayan bir
        # uygulamadan ayırt edilemez kılar. Sadece GERÇEKTEN eski olduğu
        # bilinen (kesin zaman damgalı) uygulamalar gösterilir.
        cutoff = time.time() - self._threshold_days * 86400
        stale = [
            (pkg, ts) for pkg, ts in self._data
            if ts is not None and ts <= cutoff
        ]
        stale.sort(key=lambda pair: pair[1])

        if not stale:
            self._list_box.set_visible(False)
            self._empty_status.set_visible(True)
            self._select_all.set_sensitive(False)
            self._update_selection()
            return

        self._list_box.set_visible(True)
        self._empty_status.set_visible(False)
        for pkg, ts in stale:
            check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
            check.connect("toggled", lambda *_a: self._update_selection())
            badge = f"{pkg.source} · {_format_last_used(ts)}"
            if pkg.source.startswith("heroic-"):
                badge += " (" + _("approximate") + ")"
            row = Adw.ActionRow(
                title=pkg.name, subtitle=badge, activatable_widget=check,
            )
            row.add_prefix(check)
            row.add_prefix(self._icon_for(pkg))
            size_label = Gtk.Label(
                label=format_size(pkg.size),
                css_classes=["numeric", "dim-label"],
                valign=Gtk.Align.CENTER,
            )
            row.add_suffix(size_label)
            self._list_box.append(row)
            self._checks.append((check, pkg))

        self._select_all.set_sensitive(True)
        self._update_selection()

    def _icon_for(self, pkg) -> Gtk.Image:
        name = self._main._icon_name_for(pkg)
        icon = Gtk.Image(pixel_size=32, valign=Gtk.Align.CENTER)
        if os.path.isabs(name):
            icon.set_from_file(name)
        else:
            icon.set_from_icon_name(name)
        return icon

    def _selected_packages(self):
        return [pkg for check, pkg in self._checks if check.get_active()]

    def _on_select_all(self, check):
        active = check.get_active()
        for row_check, _pkg in self._checks:
            row_check.set_active(active)
        self._update_selection()

    def _update_selection(self):
        selected = self._selected_packages()
        self._remove_button.set_sensitive(bool(selected))
        if selected:
            total = sum(pkg.size for pkg in selected)
            self._selection_label.set_label(
                _("{count} selected • {size}").format(
                    count=len(selected), size=format_size(total) or "0"
                )
            )
        else:
            self._selection_label.set_label("")

    def _on_remove_clicked(self, *_args):
        selected = self._selected_packages()
        if not selected:
            return
        items = [_Item(pkg) for pkg in selected]
        RemovalWindow(self._main, items).present()
        self.close()
