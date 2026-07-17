import os
import threading

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango

from . import host
from .appicons import build_maps
from .backends import available_backends, format_size
from . import prefs
from .i18n import _
from .leftovers import find_package_leftovers, remove_leftovers
from .removal import RemovalWindow

SORT_NAME, SORT_SIZE = 0, 1


class PackageItem(GObject.Object):
    """GObject wrapper so Package rows can live in a Gio.ListStore."""

    __gtype_name__ = "PackageItem"

    def __init__(self, pkg):
        super().__init__()
        self.pkg = pkg


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("PackWarden")
        self.set_default_size(920, 640)

        self._backends = []
        self._items: list[PackageItem] = []
        self._icon_map: dict[str, str] = {}
        self._launcher_map: dict[str, str] = {}
        self._apps_only = bool(prefs.get("apps_only"))
        self._context_item = None  # sağ tık yapılan satırın paketi
        # Basılı tutunca açılan seçim modu: açıkken normal tıklamalar
        # seçime ekler/çıkarır (telefondaki gibi)
        self._selection_mode = False
        self._setup_context_actions()
        self._search_text = ""
        # (backend_id | None, origin | None): (None, None) = tümü
        self._source_filter = (None, None)
        self._source_choices = [(None, None)]
        self._sort_mode = SORT_NAME
        self._busy = False

        self._store = Gio.ListStore(item_type=PackageItem)
        self._filter = Gtk.CustomFilter.new(self._filter_func)
        self._filter_model = Gtk.FilterListModel(model=self._store, filter=self._filter)
        self._sorter = Gtk.CustomSorter.new(self._sort_func)
        self._sort_model = Gtk.SortListModel(model=self._filter_model, sorter=self._sorter)

        self._setup_icon_theme()
        self._build_ui()
        self.refresh()

    # ---------------- UI construction ----------------

    def _setup_icon_theme(self):
        """Uygulama simgelerini bulmak için Flatpak dışa aktarma
        dizinlerini de simge temasının arama yoluna ekle."""
        self._icon_theme = Gtk.IconTheme.get_for_display(self.get_display())
        for path in (
            "/var/lib/flatpak/exports/share/icons",
            os.path.expanduser("~/.local/share/flatpak/exports/share/icons"),
        ):
            if os.path.isdir(path):
                self._icon_theme.add_search_path(path)

    def _icon_name_for(self, pkg) -> str:
        """Paketin uygulama simgesini bul; yoksa genel paket simgesi."""
        mapped = self._icon_map.get(pkg.id.lower()) or self._icon_map.get(
            pkg.name.lower()
        )
        if mapped:
            if os.path.isabs(mapped) and os.path.exists(mapped):
                return mapped
            if self._icon_theme.has_icon(mapped):
                return mapped
        for candidate in (pkg.id, pkg.name.lower(), pkg.name):
            if candidate and self._icon_theme.has_icon(candidate):
                return candidate
        return "package-x-generic"

    def _build_ui(self):
        header = Adw.HeaderBar()

        self._search_entry = Gtk.SearchEntry(placeholder_text=_("Search packages…"))
        self._search_entry.set_hexpand(True)
        self._search_entry.connect("search-changed", self._on_search_changed)
        clamp = Adw.Clamp(maximum_size=420, child=self._search_entry, hexpand=True)
        header.set_title_widget(clamp)

        search_action = Gio.SimpleAction.new("search", None)
        search_action.connect("activate", lambda *_: self._search_entry.grab_focus())
        self.add_action(search_action)

        self._refresh_button = Gtk.Button(
            icon_name="view-refresh-symbolic", tooltip_text=_("Refresh package list")
        )
        self._refresh_button.connect("clicked", lambda *_: self.refresh())
        header.pack_start(self._refresh_button)

        menu = Gio.Menu()
        menu.append(_("Settings"), "app.settings")
        menu.append(_("About PackWarden"), "app.about")
        menu.append(_("Quit"), "app.quit")
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        header.pack_end(menu_button)

        # Filter row under the header: source + sort dropdowns
        self._source_dropdown = Gtk.DropDown.new_from_strings([_("All sources")])
        self._source_dropdown.connect("notify::selected", self._on_source_changed)

        self._sort_dropdown = Gtk.DropDown.new_from_strings(
            [_("Sort by name"), _("Sort by size")]
        )
        self._sort_dropdown.connect("notify::selected", self._on_sort_changed)

        self._count_label = Gtk.Label(css_classes=["dim-label"], hexpand=True, xalign=1)

        filter_bar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            margin_top=8, margin_bottom=8, margin_start=12, margin_end=12,
        )
        filter_bar.append(self._source_dropdown)
        filter_bar.append(self._sort_dropdown)
        filter_bar.append(self._count_label)

        # Package list
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_row_setup)
        factory.connect("bind", self._on_row_bind)

        # Çoklu seçim: tık = seç, Ctrl+tık = ekle, Shift+tık = aralık,
        # sürükleme = kutuyla toplu seçim (rubber band)
        self._selection = Gtk.MultiSelection(model=self._sort_model)
        self._selection.connect(
            "selection-changed", lambda *_a: self._update_count_label()
        )
        self._list_view = Gtk.ListView(
            model=self._selection,
            enable_rubberband=True,
            factory=factory,
            css_classes=["rich-list"],
        )
        self._scrolled = Gtk.ScrolledWindow(child=self._list_view, vexpand=True)
        scrolled = self._scrolled

        # Orta fare tuşuyla otomatik kaydırma (Windows usulü): basılı
        # tutup işaretçiyi itince liste sürekli akar; uzaklık = hız.
        # İşaretçi ekran kenarına dayansa bile kaydırma devam eder.
        self._pan_tick_id = None
        self._pan_dy = 0.0
        self._pan_last_time = None
        pan = Gtk.GestureDrag(button=Gdk.BUTTON_MIDDLE)
        pan.connect("drag-begin", self._on_pan_begin)
        pan.connect("drag-update", self._on_pan_update)
        pan.connect("drag-end", self._on_pan_end)
        pan.connect("cancel", self._on_pan_end)
        scrolled.add_controller(pan)

        self._loading_page = Adw.StatusPage(
            title=_("Loading packages…"),
            description=_("Reading installed packages from every source"),
        )
        spinner = Gtk.Spinner(spinning=True, width_request=32, height_request=32)
        self._loading_page.set_child(spinner)

        self._stack = Gtk.Stack()
        self._stack.add_named(self._loading_page, "loading")

        list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        list_box.append(filter_bar)
        list_box.append(Gtk.Separator())
        list_box.append(scrolled)
        self._stack.add_named(list_box, "list")

        self._busy_spinner = Gtk.Spinner()
        header.pack_end(self._busy_spinner)

        toolbar_view = Adw.ToolbarView(content=self._stack)
        toolbar_view.add_top_bar(header)

        self._toast_overlay = Adw.ToastOverlay(child=toolbar_view)
        self.set_content(self._toast_overlay)

        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_controller)

    def _on_key_pressed(self, _controller, keyval, _keycode, _state):
        if keyval == Gdk.KEY_Escape and self._selection_mode:
            self._exit_selection_mode()
            return True
        return False

    def _on_pan_begin(self, _gesture, _x, _y):
        self._pan_dy = 0.0
        self._pan_last_time = None
        if self._pan_tick_id is None:
            self._pan_tick_id = self._scrolled.add_tick_callback(self._on_pan_tick)

    def _on_pan_update(self, _gesture, _dx, dy):
        self._pan_dy = dy  # bağlantı noktasından uzaklık = hız

    def _on_pan_end(self, *_args):
        if self._pan_tick_id is not None:
            self._scrolled.remove_tick_callback(self._pan_tick_id)
            self._pan_tick_id = None

    def _on_pan_tick(self, _widget, frame_clock):
        now = frame_clock.get_frame_time()  # mikrosaniye
        if self._pan_last_time is None:
            self._pan_last_time = now
            return GLib.SOURCE_CONTINUE
        elapsed = (now - self._pan_last_time) / 1_000_000
        self._pan_last_time = now

        # Küçük ölü bölge: tuşa basarken elin titremesi kaydırma sayılmasın
        if abs(self._pan_dy) > 8:
            adjustment = self._scrolled.get_vadjustment()
            adjustment.set_value(
                adjustment.get_value() + self._pan_dy * 8 * elapsed
            )
        return GLib.SOURCE_CONTINUE

    # ---------------- List rows ----------------

    def _on_row_setup(self, _factory, list_item):
        icon = Gtk.Image(pixel_size=32, valign=Gtk.Align.CENTER)

        name = Gtk.Label(xalign=0, css_classes=["heading"],
                         ellipsize=Pango.EllipsizeMode.END)
        desc = Gtk.Label(xalign=0, css_classes=["dim-label", "caption"],
                         ellipsize=Pango.EllipsizeMode.END)
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        text_box.append(name)
        text_box.append(desc)

        source = Gtk.Label(css_classes=["caption", "accent"], xalign=1)
        publisher = Gtk.Label(css_classes=["caption", "dim-label"], xalign=1,
                              ellipsize=Pango.EllipsizeMode.END, max_width_chars=24)
        badge_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                            valign=Gtk.Align.CENTER)
        badge_box.append(source)
        badge_box.append(publisher)

        size = Gtk.Label(css_classes=["numeric", "dim-label"],
                         valign=Gtk.Align.CENTER, width_chars=9, xalign=1)

        row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
            margin_top=6, margin_bottom=6, margin_start=12, margin_end=12,
        )
        row.append(icon)
        row.append(text_box)
        row.append(badge_box)
        row.append(size)

        gesture = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        gesture.connect("pressed", self._on_row_right_click, list_item)
        row.add_controller(gesture)

        # Basılı tutma: seçim modunu başlatır (fare ve dokunmatik)
        long_press = Gtk.GestureLongPress(touch_only=False)
        long_press.connect("pressed", self._on_row_long_press, list_item)
        row.add_controller(long_press)

        # Seçim modu açıkken sol tık seçime ekler/çıkarır; kapalıyken
        # bu denetleyici olaya karışmaz ve normal davranış sürer
        toggle_click = Gtk.GestureClick(
            button=Gdk.BUTTON_PRIMARY,
            propagation_phase=Gtk.PropagationPhase.CAPTURE,
        )
        toggle_click.connect("pressed", self._on_row_toggle_click, list_item)
        row.add_controller(toggle_click)

        list_item.set_child(row)
        list_item.icon = icon
        list_item.name_label = name
        list_item.desc_label = desc
        list_item.source_label = source
        list_item.publisher_label = publisher
        list_item.size_label = size

    def _on_row_bind(self, _factory, list_item):
        item = list_item.get_item()
        pkg = item.pkg

        version = f"  {pkg.version}" if pkg.version else ""
        icon = self._icon_name_for(pkg)
        if os.path.isabs(icon):
            list_item.icon.set_from_file(icon)
        else:
            list_item.icon.set_from_icon_name(icon)
        list_item.name_label.set_label(pkg.name + version)
        list_item.desc_label.set_label(pkg.description or pkg.id)
        badge = f"{pkg.source} · {pkg.origin}" if pkg.origin else pkg.source
        list_item.source_label.set_label(badge)
        list_item.publisher_label.set_label(pkg.publisher)
        list_item.publisher_label.set_visible(bool(pkg.publisher))
        list_item.size_label.set_label(format_size(pkg.size))

    # ---------------- Sağ tık menüsü ----------------

    def _setup_context_actions(self):
        actions = {
            "ctx-uninstall": self._ctx_uninstall,
            "ctx-launch": self._ctx_launch,
            "ctx-copy": self._ctx_copy,
            "ctx-leftovers": self._ctx_leftovers,
            "ctx-properties": self._ctx_properties,
        }
        for name, handler in actions.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)

    def _on_row_long_press(self, gesture, _x, _y, list_item):
        item = list_item.get_item()
        if item is None or self._busy:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._selection_mode = True
        self._selection.select_item(list_item.get_position(), False)
        self._update_count_label()

    def _on_row_toggle_click(self, gesture, _n_press, _x, _y, list_item):
        if not self._selection_mode:
            return  # normal düzen: olaya karışma
        item = list_item.get_item()
        if item is None:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        position = list_item.get_position()
        if self._selection.is_selected(position):
            self._selection.unselect_item(position)
            if not self._selected_items():
                self._selection_mode = False  # seçim bitti, mod kapanır
        else:
            self._selection.select_item(position, False)
        self._update_count_label()

    def _exit_selection_mode(self):
        if self._selection_mode:
            self._selection_mode = False
            self._selection.unselect_all()
            self._update_count_label()

    def _selected_items(self):
        """Vurgulanarak seçilmiş satırların paketleri."""
        bitset = self._selection.get_selection()
        items = []
        for n in range(bitset.get_size()):
            item = self._selection.get_item(bitset.get_nth(n))
            if item is not None:
                items.append(item)
        return items

    def _on_row_right_click(self, gesture, _n_press, x, y, list_item):
        item = list_item.get_item()
        if item is None or self._busy:
            return
        self._context_item = item

        # Sağ tıklanan satır mevcut seçimin dışındaysa seçimi ona daralt
        position = list_item.get_position()
        if not self._selection.is_selected(position):
            self._selection.select_item(position, True)
        selected_count = len(self._selected_items())

        menu = Gio.Menu()
        if selected_count > 1:
            menu.append(
                _("Uninstall {count} selected…").format(count=selected_count),
                "win.ctx-uninstall",
            )
        else:
            menu.append(_("Uninstall…"), "win.ctx-uninstall")
        menu.append(_("Launch"), "win.ctx-launch")
        menu.append(_("Copy package ID"), "win.ctx-copy")
        menu.append(_("Delete leftover files…"), "win.ctx-leftovers")
        menu.append(_("Properties"), "win.ctx-properties")

        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(gesture.get_widget())
        popover.set_has_arrow(False)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        # Kapanınca satırdan ayrılmalı; yoksa geri dönüştürülen satırlar
        # görünmez popover'lar biriktirir
        popover.connect("closed", lambda p: GLib.idle_add(p.unparent))
        popover.popup()

    def _ctx_uninstall(self, *_args):
        items = self._selected_items()
        if not items and self._context_item:
            items = [self._context_item]
        if items:
            RemovalWindow(self, items).present()

    def _ctx_launch(self, *_args):
        item = self._context_item
        if not item:
            return
        pkg = item.pkg
        if pkg.source == "flatpak":
            argv = ["flatpak", "run", pkg.id]
        elif pkg.source == "snap":
            argv = ["snap", "run", pkg.id]
        elif pkg.source == "appimage":
            argv = [pkg.id]  # kimlik = dosya yolu
        else:
            desktop_id = self._launcher_map.get(
                pkg.id.lower()
            ) or self._launcher_map.get(pkg.name.lower())
            if not desktop_id:
                self._toast_overlay.add_toast(Adw.Toast(
                    title=_("{name} has no launchable window").format(
                        name=pkg.name
                    )
                ))
                return
            argv = ["gtk-launch", desktop_id]
        try:
            host.spawn(argv)
            self._toast_overlay.add_toast(
                Adw.Toast(title=_("Launching {name}…").format(name=pkg.name))
            )
        except Exception:
            self._toast_overlay.add_toast(
                Adw.Toast(title=_("Could not launch {name}").format(name=pkg.name))
            )

    def _ctx_copy(self, *_args):
        item = self._context_item
        if not item:
            return
        self.get_clipboard().set(item.pkg.id)
        self._toast_overlay.add_toast(
            Adw.Toast(title=_("Copied: {text}").format(text=item.pkg.id))
        )

    def _ctx_leftovers(self, *_args):
        item = self._context_item
        if not item:
            return
        pkg = item.pkg

        def worker():
            groups = find_package_leftovers([pkg])
            flat = [lo for _c, items in groups for lo in items]
            GLib.idle_add(done, flat)

        def done(flat):
            if not flat:
                self._toast_overlay.add_toast(
                    Adw.Toast(title=_("No leftover files found"))
                )
                return GLib.SOURCE_REMOVE
            total = sum(lo.size for lo in flat)
            listing = "\n".join(f"• {lo.path}" for lo in flat[:10])
            dialog = Adw.AlertDialog(
                heading=_("Delete leftovers of {name}?").format(name=pkg.name),
                body=f"{listing}\n\n"
                + _("Total: {size}").format(size=format_size(total) or "~0"),
            )
            dialog.add_response("cancel", _("Cancel"))
            dialog.add_response("delete", _("Delete"))
            dialog.set_response_appearance(
                "delete", Adw.ResponseAppearance.DESTRUCTIVE
            )
            dialog.set_default_response("cancel")
            dialog.connect("response", confirm, flat)
            dialog.present(self)
            return GLib.SOURCE_REMOVE

        def confirm(_dialog, response, flat):
            if response != "delete":
                return
            errors = remove_leftovers(flat)
            if errors:
                self._toast_overlay.add_toast(Adw.Toast(title=errors[0]))
            else:
                self._toast_overlay.add_toast(Adw.Toast(
                    title=_("Deleted {count} leftover items").format(
                        count=len(flat)
                    )
                ))

        threading.Thread(target=worker, daemon=True).start()

    def _ctx_properties(self, *_args):
        item = self._context_item
        if not item:
            return
        pkg = item.pkg
        rows = [
            (_("Package ID"), pkg.id),
            (_("Version"), pkg.version),
            (_("Size"), format_size(pkg.size)),
            (_("Source"), pkg.source),
            (_("Repository"), pkg.origin),
            (_("Publisher"), pkg.publisher),
            (_("Description"), pkg.description),
        ]
        body = "\n".join(f"{label}:  {value}" for label, value in rows if value)
        dialog = Adw.AlertDialog(heading=pkg.name, body=body)
        dialog.add_response("ok", _("OK"))
        dialog.present(self)

    # ---------------- Filtering & sorting ----------------

    def _is_app(self, pkg) -> bool:
        """Paket bir son-kullanıcı uygulaması mı (kütüphane/sistem değil)?"""
        if pkg.source in ("flatpak", "snap", "appimage"):
            return True  # bu kaynaklar zaten yalnızca uygulama barındırır
        return (
            pkg.id.lower() in self._launcher_map
            or pkg.name.lower() in self._launcher_map
        )

    def set_apps_only(self, value: bool) -> None:
        self._apps_only = value
        prefs.set("apps_only", value)
        self._filter.changed(Gtk.FilterChange.DIFFERENT)
        self._update_count_label()

    def _filter_func(self, item):
        pkg = item.pkg
        if self._apps_only and not self._is_app(pkg):
            return False
        backend_id, origin = self._source_filter
        if backend_id and pkg.source != backend_id:
            return False
        if origin and pkg.origin != origin:
            return False
        if self._search_text:
            haystack = (
                f"{pkg.name} {pkg.id} {pkg.description} "
                f"{pkg.publisher} {pkg.origin}".lower()
            )
            return self._search_text in haystack
        return True

    def _sort_func(self, a, b, _data=None):
        if self._sort_mode == SORT_SIZE:
            diff = b.pkg.size - a.pkg.size  # largest first
            if diff:
                return 1 if diff > 0 else -1
        an, bn = a.pkg.name.lower(), b.pkg.name.lower()
        return (an > bn) - (an < bn)

    def _on_search_changed(self, entry):
        self._search_text = entry.get_text().strip().lower()
        self._filter.changed(Gtk.FilterChange.DIFFERENT)
        self._update_count_label()

    def _on_source_changed(self, dropdown, _pspec):
        index = dropdown.get_selected()
        if 0 <= index < len(self._source_choices):
            self._source_filter = self._source_choices[index]
        else:
            self._source_filter = (None, None)
        self._filter.changed(Gtk.FilterChange.DIFFERENT)
        self._update_count_label()

    def _on_sort_changed(self, dropdown, _pspec):
        self._sort_mode = dropdown.get_selected()
        self._sorter.changed(Gtk.SorterChange.DIFFERENT)

    # ---------------- Loading ----------------

    def refresh(self):
        if self._busy:
            return
        self._set_busy(True)
        self._stack.set_visible_child_name("loading")

        def worker():
            backends = available_backends()
            packages = []
            for backend in backends:
                try:
                    packages.extend(backend.list_packages())
                except Exception:
                    pass  # a broken backend must not take the app down
            try:
                icon_map, launcher_map = build_maps()
            except Exception:
                icon_map, launcher_map = {}, {}
            GLib.idle_add(
                self._on_loaded, backends, packages, icon_map, launcher_map
            )

        threading.Thread(target=worker, daemon=True).start()

    def _on_loaded(self, backends, packages, icon_map, launcher_map):
        self._backends = backends
        self._icon_map = icon_map
        self._launcher_map = launcher_map
        self._items = [PackageItem(pkg) for pkg in packages]

        self._store.remove_all()
        # splice is far faster than thousands of append() calls
        self._store.splice(0, 0, self._items)

        # Filtre listesi: her arka uç + altındaki depolar, paket sayılarıyla.
        # Boş kaynaklar (sistemde olup hiç paketi olmayanlar) gösterilmez.
        choices = [(None, None)]
        names = [f'{_("All sources")} ({len(packages)})']
        for backend in backends:
            in_backend = [p for p in packages if p.source == backend.id]
            if not in_backend:
                continue
            choices.append((backend.id, None))
            label = _("{name} — all").format(name=backend.display_name)
            names.append(f"{label} ({len(in_backend)})")
            origin_counts: dict[str, int] = {}
            for pkg in in_backend:
                if pkg.origin:
                    origin_counts[pkg.origin] = origin_counts.get(pkg.origin, 0) + 1
            if len(origin_counts) > 1 or (
                origin_counts and sum(origin_counts.values()) < len(in_backend)
            ):
                for origin in sorted(origin_counts):
                    choices.append((backend.id, origin))
                    names.append(
                        f"{backend.display_name} · {origin} ({origin_counts[origin]})"
                    )
        self._source_choices = choices
        self._source_dropdown.set_model(Gtk.StringList.new(names))
        self._source_filter = (None, None)

        self._stack.set_visible_child_name("list")
        self._set_busy(False)
        self._update_count_label()
        return GLib.SOURCE_REMOVE

    # ---------------- Helpers ----------------

    def _set_busy(self, busy):
        self._busy = busy
        self._busy_spinner.set_spinning(busy)
        self._refresh_button.set_sensitive(not busy)

    def _update_count_label(self):
        shown = self._filter_model.get_n_items()
        text = _("{shown} of {total} packages").format(
            shown=shown, total=len(self._items)
        )
        selected = self._selected_items()
        if selected:
            total_size = sum(item.pkg.size for item in selected)
            text += "  •  " + _("{count} selected").format(count=len(selected))
            if total_size:
                text += f" ({format_size(total_size)})"
        if self._selection_mode:
            text = _("Selection mode (Esc to finish)") + "  •  " + text
        self._count_label.set_label(text)
