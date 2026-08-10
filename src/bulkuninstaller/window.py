import os
import shutil
import threading

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from . import DEV_BUILD, host, prefs
from .appicons import APP_DIRS, build_maps
from .backends import available_backends, format_install_date, format_size
from .diskmap import DiskMapArea, build_legend, format_percent
from .i18n import _
from .leftovers import find_package_leftovers, remove_leftovers
from .packagetable import Column, PackageTableArea
from .removal import RemovalWindow
from .unusedwindow import UnusedAppsWindow


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
        self.maximize()

        self._backends = []
        self._items: list[PackageItem] = []
        self._icon_map: dict[str, str] = {}
        self._launcher_map: dict[str, str] = {}
        self._category_map: dict[str, str] = {}
        self._apps_only = bool(prefs.get("apps_only"))
        self._context_item = None  # sağ tık yapılan satırın paketi
        self._setup_context_actions()
        self._search_text = ""
        # (backend_id | None, origin | None): (None, None) = tümü
        self._source_filter = (None, None)
        self._source_choices = [(None, None)]
        self._busy = False
        self._total_size = 1

        self._store = Gio.ListStore(item_type=PackageItem)
        self._filter = Gtk.CustomFilter.new(self._filter_func)
        self._filter_model = Gtk.FilterListModel(model=self._store, filter=self._filter)

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
        if pkg.icon_path and os.path.isfile(pkg.icon_path):
            return pkg.icon_path
        if pkg.source.startswith(("steam", "lutris", "heroic-")):
            return "applications-games"
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

    def _category_for(self, pkg) -> str:
        # Oyun kaynaklarının .desktop dosyası olmayabilir (Steam/Lutris/
        # Heroic'te kurulan oyunlar genelde menüye kayıtlı değildir) —
        # kaynağın kendisi zaten kategoriyi kesin olarak veriyor.
        if pkg.source.startswith(("steam", "lutris", "heroic-")):
            return _("Game")
        raw = self._category_map.get(pkg.id.lower()) or self._category_map.get(
            pkg.name.lower()
        )
        return _(raw) if raw else ""

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

        # Yenile düğmesinin yanında küçük uygulama logosu
        logo_path = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "..", "..",
            "data", "icons", "io.github.muhammethub111.PackWarden.svg",
        ))
        if os.path.exists(logo_path):
            logo = Gtk.Image(pixel_size=24, valign=Gtk.Align.CENTER)
            logo.set_from_file(logo_path)
            header.pack_start(logo)

        menu = Gio.Menu()
        menu.append(_("Unused apps"), "win.unused-apps")
        menu.append(_("Settings"), "app.settings")
        menu.append(_("About PackWarden"), "app.about")
        menu.append(_("Quit"), "app.quit")
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        header.pack_end(menu_button)

        # Filter row under the header: source dropdown
        self._source_dropdown = Gtk.DropDown.new_from_strings([_("All sources")])
        self._source_dropdown.connect("notify::selected", self._on_source_changed)

        self._count_label = Gtk.Label(css_classes=["dim-label"], hexpand=True, xalign=1)

        toggle_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        toggle_content.append(Gtk.Image(icon_name="drive-harddisk-symbolic"))
        toggle_content.append(Gtk.Label(label=_("Disk map")))
        self._diskmap_toggle = Gtk.ToggleButton(
            child=toggle_content, tooltip_text=_("Disk map"),
        )
        self._diskmap_toggle.connect("toggled", self._on_diskmap_toggled)
        # Kutuların üzerine gelince/tıklayınca ad+boyut+oranı burada
        # göster — GTK'nin yerleşik tooltip'i kutular arası hızlı
        # geçişte kararsız davranıyor, kalıcı bir etiket daha güvenilir
        self._diskmap_hover_label = Gtk.Label(css_classes=["dim-label"])

        filter_bar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            margin_top=8, margin_bottom=8, margin_start=12, margin_end=12,
        )
        filter_bar.append(self._source_dropdown)
        filter_bar.append(self._diskmap_toggle)
        filter_bar.append(self._count_label)
        filter_bar.append(self._diskmap_hover_label)

        # Paket listesi: tamamen özel çizilen bir tablo (PackageTableArea,
        # bkz. packagetable.py) — Gtk.ColumnView'in yerini alıyor çünkü
        # GTK'nin bu widget'ı bir sütun büyük ölçüde genişleyince
        # başlıkla satır hücrelerinin senkronunu kaybediyor — kullanıcının
        # istediği "her sütun tamamen bağımsız sürüklensin" davranışı
        # zaten bu widget'la mümkün değildi. Yeni tablo, diskmap.py'deki
        # DiskMapArea ile aynı deyimi izliyor: Cairo ile elle çizim, elle
        # hit-test, düz Python callback'leri.
        self._table = PackageTableArea()
        self._table.on_hover = self._on_table_hover
        self._table.on_context_menu = self._on_table_context_menu
        self._table.on_selection_changed = self._update_count_label
        self._table.on_columns_reordered = self._on_table_columns_reordered

        self._table.add_column(Column(
            id="name", title=_("Name"), width=320, min_width=4,
            sort_key=lambda it: it.pkg.name.lower(),
            section_key=lambda it: self._section_letter(it.pkg),
            draw_content=self._draw_name_cell,
        ))
        self._table.add_column(Column(
            id="version", title=_("Version"), width=130, min_width=4,
            align="left", sort_key=lambda it: (it.pkg.version.lower(), it.pkg.name.lower()),
            draw_content=self._draw_version_cell,
        ))
        self._table.add_column(Column(
            id="size", title=_("Size"), width=100, min_width=4,
            align="left", sort_key=lambda it: (it.pkg.size, it.pkg.name.lower()),
            draw_content=self._draw_size_cell,
        ))
        self._table.add_column(Column(
            id="percent", title=_("Percent"), width=90, min_width=4,
            # Yüzde, boyutun sabit bir çarpanı olduğu için (pay/toplam)
            # aynı sırayı verir — Boyut ile aynı sort_key'i paylaşıyor.
            align="left", sort_key=lambda it: (it.pkg.size, it.pkg.name.lower()),
            draw_content=self._draw_percent_cell,
        ))
        self._table.add_column(Column(
            id="publisher", title=_("Publisher"), width=200, min_width=4,
            align="left",
            sort_key=lambda it: (it.pkg.publisher.lower(), it.pkg.name.lower()),
            draw_content=self._draw_publisher_cell,
        ))
        self._table.add_column(Column(
            id="install_date", title=_("Installed"), width=120, min_width=4,
            align="left",
            sort_key=lambda it: (it.pkg.install_date or 0, it.pkg.name.lower()),
            draw_content=self._draw_install_date_cell,
        ))
        self._table.add_column(Column(
            id="install_path", title=_("Location"), width=260, min_width=4,
            align="left",
            sort_key=lambda it: (it.pkg.install_path.lower(), it.pkg.name.lower()),
            draw_content=self._draw_install_path_cell,
        ))
        self._table.add_column(Column(
            id="install_reason", title=_("Reason"), width=130, min_width=4,
            align="left",
            sort_key=lambda it: (it.pkg.install_reason, it.pkg.name.lower()),
            draw_content=self._draw_install_reason_cell,
        ))
        self._table.add_column(Column(
            id="required_by", title=_("Required by"), width=110, min_width=4,
            align="left",
            sort_key=lambda it: (it.pkg.required_by if it.pkg.required_by is not None
                                  else -1, it.pkg.name.lower()),
            draw_content=self._draw_required_by_cell,
        ))
        self._table.add_column(Column(
            id="license", title=_("License"), width=160, min_width=4,
            align="left",
            sort_key=lambda it: (it.pkg.license.lower(), it.pkg.name.lower()),
            draw_content=self._draw_license_cell,
        ))
        self._table.add_column(Column(
            id="category", title=_("Category"), width=130, min_width=4,
            align="left",
            sort_key=lambda it: (self._category_for(it.pkg).lower(), it.pkg.name.lower()),
            draw_content=self._draw_category_cell,
        ))

        # Kullanıcı başlıkları sürükleyerek yeniden sıraladıysa (bkz.
        # _on_table_columns_reordered) o sıra kalıcıdır — her açılışta
        # sıfırlanması can sıkıcı olurdu, tıpkı bir dosya yöneticisinde
        # sütun sırasının hatırlanması gibi.
        saved_order = prefs.get("column_order")
        if saved_order:
            self._table.reorder_columns(saved_order)

        self._scrolled = Gtk.ScrolledWindow(child=self._table, vexpand=True)
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

        self._list_empty_status = Adw.StatusPage(
            icon_name="edit-find-symbolic",
            title=_("No packages match"),
            description=_("Try a different search or source filter"),
            vexpand=True,
            visible=False,
        )
        list_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        list_page.append(scrolled)
        list_page.append(self._list_empty_status)

        self._stack = Gtk.Stack()
        self._stack.add_named(self._loading_page, "loading")
        self._stack.add_named(list_page, "list")

        self._diskmap_area = DiskMapArea()
        self._diskmap_area.on_select = self._on_diskmap_select
        self._diskmap_area.on_context_menu = self._on_diskmap_context_menu
        self._diskmap_area.on_hover = self._on_diskmap_hover
        self._diskmap_legend = build_legend([])
        self._diskmap_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._diskmap_content.append(self._diskmap_area)
        self._diskmap_content.append(Gtk.Separator())
        self._diskmap_content.append(self._diskmap_legend)

        self._diskmap_empty_status = Adw.StatusPage(
            icon_name="edit-find-symbolic",
            title=_("No packages match"),
            description=_("Try a different search or source filter"),
            vexpand=True,
            visible=False,
        )
        self._diskmap_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._diskmap_page.append(self._diskmap_content)
        self._diskmap_page.append(self._diskmap_empty_status)
        self._stack.add_named(self._diskmap_page, "diskmap")

        self._busy_spinner = Gtk.Spinner()
        header.pack_end(self._busy_spinner)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content_box.append(filter_bar)
        content_box.append(Gtk.Separator())
        content_box.append(self._stack)

        toolbar_view = Adw.ToolbarView(content=content_box)
        toolbar_view.add_top_bar(header)

        self._toast_overlay = Adw.ToastOverlay(child=toolbar_view)
        self.set_content(self._toast_overlay)

        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_controller)

    def _on_key_pressed(self, _controller, keyval, _keycode, _state):
        if keyval == Gdk.KEY_Escape and self._table.is_selection_mode():
            self._table.exit_selection_mode()
            return True
        if keyval == Gdk.KEY_F5:
            self.refresh()
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

    # ---------------- Tablo hücre çizimleri (PackageTableArea) ----------------
    #
    # Her metot Column.draw_content imzasıyla çağrılır:
    # (table, cr, column, item, x, y, width, height, selected). Genel
    # çizim yardımcıları (draw_text_cell, draw_icon_and_text_cell,
    # draw_two_line_cell) packagetable.py'de tanımlı — burada sadece
    # hangi paket alanının hangi yardımcıyla çizileceğine karar veriliyor.

    def _draw_name_cell(self, table, cr, _col, item, x, y, w, h, selected):
        pkg = item.pkg
        table.draw_icon_and_text_cell(
            cr, x, y, w, h, self._icon_name_for(pkg), pkg.name, selected=selected
        )

    def _draw_version_cell(self, table, cr, col, item, x, y, w, h, _selected):
        # Oyun arka uçları (Steam/Lutris/Heroic) sürüm bilgisi
        # sunmuyor, version="" geliyor — boş bırakınca hücre kırık/boş
        # görünüyordu, yer tutucu tire ile dolduruyoruz.
        table.draw_text_cell(cr, x, y, w, h, item.pkg.version or "—",
                              align=self._cell_align(table, col), dim=True)

    def _draw_size_cell(self, table, cr, col, item, x, y, w, h, _selected):
        table.draw_text_cell(cr, x, y, w, h, format_size(item.pkg.size),
                              align=self._cell_align(table, col), dim=True)

    def _draw_source_cell(self, table, cr, col, item, x, y, w, h, _selected):
        pkg = item.pkg
        badge = f"{pkg.source} · {pkg.origin}" if pkg.origin else pkg.source
        table.draw_two_line_cell(cr, x, y, w, h, badge, pkg.publisher,
                                  align=self._cell_align(table, col))

    def _draw_percent_cell(self, table, cr, col, item, x, y, w, h, _selected):
        percent = item.pkg.size / self._total_size * 100
        table.draw_text_cell(cr, x, y, w, h, f"{format_percent(percent)}%",
                              align=self._cell_align(table, col), dim=True)

    def _draw_publisher_cell(self, table, cr, col, item, x, y, w, h, _selected):
        table.draw_text_cell(cr, x, y, w, h, item.pkg.publisher or "—",
                              align=self._cell_align(table, col), dim=True)

    def _draw_install_date_cell(self, table, cr, col, item, x, y, w, h, _selected):
        table.draw_text_cell(
            cr, x, y, w, h, format_install_date(item.pkg.install_date) or "—",
            align=self._cell_align(table, col), dim=True,
        )

    def _draw_install_path_cell(self, table, cr, col, item, x, y, w, h, _selected):
        table.draw_text_cell(cr, x, y, w, h, item.pkg.install_path or "—",
                              align=self._cell_align(table, col), dim=True)

    _INSTALL_REASON_LABELS = {"explicit": _("Explicit"), "dependency": _("Dependency")}

    def _draw_install_reason_cell(self, table, cr, col, item, x, y, w, h, _selected):
        text = self._INSTALL_REASON_LABELS.get(item.pkg.install_reason, "—")
        table.draw_text_cell(cr, x, y, w, h, text,
                              align=self._cell_align(table, col), dim=True)

    def _draw_required_by_cell(self, table, cr, col, item, x, y, w, h, _selected):
        count = item.pkg.required_by
        text = str(count) if count is not None else "—"
        table.draw_text_cell(cr, x, y, w, h, text,
                              align=self._cell_align(table, col), dim=True)

    def _draw_license_cell(self, table, cr, col, item, x, y, w, h, _selected):
        table.draw_text_cell(cr, x, y, w, h, item.pkg.license or "—",
                              align=self._cell_align(table, col), dim=True)

    def _draw_category_cell(self, table, cr, col, item, x, y, w, h, _selected):
        table.draw_text_cell(cr, x, y, w, h, self._category_for(item.pkg) or "—",
                              align=self._cell_align(table, col), dim=True)

    def _cell_align(self, table, col) -> str:
        # Sağa yaslı bir sütun (Sürüm/Boyut/Yüzde) sürüklenerek en başa
        # taşınırsa sağa yaslı kalması "ters" görünür — ilk sütun her
        # zaman sol kenardan okunmaya başlanır. En baştaysa sola yasla.
        if table.is_leading_column(col):
            return "left"
        return col.align

    def _on_table_hover(self, item):
        if self._stack.get_visible_child_name() == "diskmap":
            return
        self._diskmap_hover_label.set_label(
            self._single_selection_info([item]) if item is not None else ""
        )

    def _on_table_context_menu(self, items, x, y):
        if not items or self._busy:
            return
        self._context_item = items[0]
        self._popup_context_menu(self._table, x, y, len(items))

    def _on_table_columns_reordered(self, id_order):
        prefs.set("column_order", id_order)

    # ---------------- Sağ tık menüsü ----------------

    def _setup_context_actions(self):
        actions = {
            "ctx-uninstall": self._ctx_uninstall,
            "ctx-launch": self._ctx_launch,
            "ctx-copy": self._ctx_copy,
            "ctx-leftovers": self._ctx_leftovers,
            "ctx-properties": self._ctx_properties,
            "unused-apps": self._open_unused_apps,
        }
        if DEV_BUILD:
            actions["ctx-desktop-shortcut"] = self._ctx_desktop_shortcut
        for name, handler in actions.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)

    def _build_context_menu(self, selected_count: int) -> Gio.Menu:
        menu = Gio.Menu()
        if selected_count > 1:
            menu.append(
                _("Uninstall {count} selected…").format(count=selected_count),
                "win.ctx-uninstall",
            )
        else:
            menu.append(_("Uninstall…"), "win.ctx-uninstall")
        menu.append(_("Launch"), "win.ctx-launch")
        if DEV_BUILD:
            menu.append(_("Create desktop shortcut"), "win.ctx-desktop-shortcut")
        menu.append(_("Copy package ID"), "win.ctx-copy")
        menu.append(_("Delete leftover files…"), "win.ctx-leftovers")
        menu.append(_("Properties"), "win.ctx-properties")
        return menu

    def _popup_context_menu(self, parent_widget, x, y, selected_count: int):
        popover = Gtk.PopoverMenu.new_from_model(
            self._build_context_menu(selected_count)
        )
        popover.set_parent(parent_widget)
        popover.set_has_arrow(False)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        # Kapanınca ebeveynden ayrılmalı; yoksa geri dönüştürülen satırlar
        # görünmez popover'lar biriktirir
        popover.connect("closed", lambda p: GLib.idle_add(p.unparent))
        popover.popup()

    def _ctx_uninstall(self, *_args):
        items = self._table.get_selected_items()
        if not items and self._context_item:
            items = [self._context_item]
        if items:
            RemovalWindow(self, items).present()

    def _launch_argv(self, pkg):
        """Bir paketi başlatmak için host komutu; başlatılamıyorsa None."""
        if pkg.source == "flatpak":
            return ["flatpak", "run", pkg.id]
        if pkg.source == "snap":
            return ["snap", "run", pkg.id]
        if pkg.source == "appimage":
            return [pkg.id]  # kimlik = dosya yolu
        desktop_id = self._launcher_map.get(
            pkg.id.lower()
        ) or self._launcher_map.get(pkg.name.lower())
        if not desktop_id:
            return None
        return ["gtk-launch", desktop_id]

    def _ctx_launch(self, *_args):
        items = self._table.get_selected_items()
        if not items and self._context_item:
            items = [self._context_item]
        if not items:
            return

        launched = []
        no_window = []
        failed = []
        for item in items:
            pkg = item.pkg
            argv = self._launch_argv(pkg)
            if argv is None:
                no_window.append(pkg.name)
                continue
            try:
                host.spawn(argv)
                launched.append(pkg.name)
            except Exception:
                failed.append(pkg.name)

        if launched:
            title = (
                _("Launching {name}…").format(name=launched[0])
                if len(launched) == 1
                else _("Launching {count} apps…").format(count=len(launched))
            )
            self._toast_overlay.add_toast(Adw.Toast(title=title))
        elif no_window and not failed:
            title = (
                _("{name} has no launchable window").format(name=no_window[0])
                if len(no_window) == 1
                else _("Selected apps have no launchable window")
            )
            self._toast_overlay.add_toast(Adw.Toast(title=title))
        elif failed:
            title = (
                _("Could not launch {name}").format(name=failed[0])
                if len(failed) == 1
                else _("Could not launch selected apps")
            )
            self._toast_overlay.add_toast(Adw.Toast(title=title))

    # Flatpak/Snap kendi .desktop dosyalarını APP_DIRS'ın taradığı sıradan
    # sistem klasörlerine değil, kendi dışa aktarma klasörlerine koyar —
    # bu yüzden kısayol için ayrıca aranmaları gerekir.
    _EXTRA_DESKTOP_DIRS = (
        "/var/lib/flatpak/exports/share/applications",
        "~/.local/share/flatpak/exports/share/applications",
        "/var/lib/snapd/desktop/applications",
    )

    def _desktop_file_path(self, desktop_id: str) -> str | None:
        for base in APP_DIRS + self._EXTRA_DESKTOP_DIRS:
            path = os.path.join(os.path.expanduser(base), f"{desktop_id}.desktop")
            if os.path.isfile(path):
                return path
        return None

    def _shortcut_desktop_id(self, pkg) -> str | None:
        if pkg.source == "flatpak":
            # Flatpak .desktop dosyaları her zaman <app-id>.desktop olarak
            # adlandırılır — pkg.id zaten tam olarak bu.
            return pkg.id
        return self._launcher_map.get(
            pkg.id.lower()
        ) or self._launcher_map.get(pkg.name.lower())

    def _desktop_dir(self) -> str:
        try:
            proc = host.run(["xdg-user-dir", "DESKTOP"], timeout=5)
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
        except Exception:
            pass
        return os.path.expanduser("~/Desktop")

    def _ctx_desktop_shortcut(self, *_args):
        items = self._table.get_selected_items()
        if not items and self._context_item:
            items = [self._context_item]
        if not items:
            return

        desktop_dir = self._desktop_dir()
        created = []
        unsupported = []
        failed = []
        for item in items:
            pkg = item.pkg
            desktop_id = self._shortcut_desktop_id(pkg)
            source_path = self._desktop_file_path(desktop_id) if desktop_id else None
            if not source_path:
                unsupported.append(pkg.name)
                continue
            try:
                os.makedirs(desktop_dir, exist_ok=True)
                # Kaynak dosya adı yerine gerçek uygulama adını kullan —
                # Flatpak/kendi kurulumumuz gibi kaynaklar dosyayı ters-DNS
                # kimliğiyle adlandırır (io.github.foo.Bar.desktop), bu da
                # dosya yöneticisinde çirkin görünür.
                safe_name = pkg.name.replace("/", "_").strip() or desktop_id
                dest_path = os.path.join(desktop_dir, f"{safe_name}.desktop")
                shutil.copyfile(source_path, dest_path)
                os.chmod(dest_path, 0o755)
                created.append(pkg.name)
            except OSError:
                failed.append(pkg.name)

        if created:
            title = (
                _("Desktop shortcut created for {name}").format(name=created[0])
                if len(created) == 1
                else _("Created {count} desktop shortcuts").format(count=len(created))
            )
            self._toast_overlay.add_toast(Adw.Toast(title=title))
        elif unsupported and not failed:
            title = (
                _("{name} has no desktop entry to copy").format(name=unsupported[0])
                if len(unsupported) == 1
                else _("Selected apps have no desktop entry to copy")
            )
            self._toast_overlay.add_toast(Adw.Toast(title=title))
        elif failed:
            title = (
                _("Could not create shortcut for {name}").format(name=failed[0])
                if len(failed) == 1
                else _("Could not create shortcuts for selected apps")
            )
            self._toast_overlay.add_toast(Adw.Toast(title=title))

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
                    title=_("Moved {count} leftover items to Trash").format(
                        count=len(flat)
                    )
                ))

        threading.Thread(target=worker, daemon=True).start()

    def _open_unused_apps(self, *_args):
        UnusedAppsWindow(self).present()

    # ---------------- Disk haritası ----------------

    def _filtered_items(self):
        return [
            self._filter_model.get_item(i)
            for i in range(self._filter_model.get_n_items())
        ]

    def _refresh_diskmap(self):
        items = self._filtered_items()
        empty = not items
        self._diskmap_content.set_visible(not empty)
        self._diskmap_empty_status.set_visible(empty)
        if empty:
            return
        self._diskmap_area.set_items(items, total_size=self._total_size)

        self._diskmap_content.remove(self._diskmap_legend)
        self._diskmap_legend = build_legend(items)
        self._diskmap_content.append(self._diskmap_legend)

    def _on_diskmap_toggled(self, button):
        if self._stack.get_visible_child_name() == "loading":
            button.set_active(False)
            return
        if button.get_active():
            self._refresh_diskmap()
            self._stack.set_visible_child_name("diskmap")
        else:
            self._stack.set_visible_child_name("list")
            self._on_diskmap_hover(None)

    def _on_diskmap_select(self, item):
        self._context_item = item

    def _on_diskmap_hover(self, text):
        self._diskmap_hover_label.set_label(text or "")

    def _on_diskmap_context_menu(self, item, x, y):
        self._context_item = item
        self._popup_context_menu(self._diskmap_area, x, y, 1)

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
            (_("Installed"), format_install_date(pkg.install_date)),
            (_("Location"), pkg.install_path),
            (_("Reason"), self._INSTALL_REASON_LABELS.get(pkg.install_reason, "")),
            (_("Required by"), str(pkg.required_by) if pkg.required_by else ""),
            (_("License"), pkg.license),
            (_("Category"), self._category_for(pkg)),
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
        if pkg.source.startswith(("steam", "lutris", "heroic-")):
            return True  # oyunlar her zaman uygulama sayılır
        return (
            pkg.id.lower() in self._launcher_map
            or pkg.name.lower() in self._launcher_map
        )

    def _maybe_refresh_diskmap(self):
        """Harita görünümü açıksa filtre değişince içeriğini güncel tutar."""
        if self._diskmap_toggle.get_active():
            self._refresh_diskmap()

    def set_apps_only(self, value: bool) -> None:
        self._apps_only = value
        prefs.set("apps_only", value)
        self._filter.changed(Gtk.FilterChange.DIFFERENT)
        self._rebuild_source_dropdown()
        self._refresh_table()
        self._update_count_label()
        self._maybe_refresh_diskmap()

    def _rebuild_source_dropdown(self, reset: bool = False):
        """Kaynak açılır listesini ve yanındaki paket sayılarını yeniden
        kurar. "Sadece uygulamalar" ayarı kapsam dışı bıraktığı paketler
        de sayıma katılmasın diye görünür kümeye göre hesaplanır."""
        visible = [
            item.pkg for item in self._items
            if not self._apps_only or self._is_app(item.pkg)
        ]

        # Boş kaynaklar (sistemde olup görünür hiç paketi olmayanlar) gösterilmez.
        choices = [(None, None)]
        names = [f'{_("All sources")} ({len(visible)})']
        for backend in self._backends:
            in_backend = [p for p in visible if p.source == backend.id]
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

        target = (None, None) if reset else self._source_filter
        index = choices.index(target) if target in choices else 0
        self._source_filter = choices[index]
        self._source_choices = choices

        # Model/seçim değişikliği notify::selected'ı tetikleyip
        # _on_source_changed'i ikinci kez çalıştırmasın diye engellenir;
        # yukarıda filtre zaten doğru şekilde belirlendi.
        self._source_dropdown.handler_block_by_func(self._on_source_changed)
        self._source_dropdown.set_model(Gtk.StringList.new(names))
        self._source_dropdown.set_selected(index)
        self._source_dropdown.handler_unblock_by_func(self._on_source_changed)

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

    def _section_letter(self, pkg) -> str:
        name = pkg.name.strip()
        letter = name[:1].upper() if name else "#"
        return letter if letter.isalpha() else "#"

    def _refresh_table(self):
        self._table.set_items(self._filtered_items())

    def _on_search_changed(self, entry):
        self._search_text = entry.get_text().strip().lower()
        self._filter.changed(Gtk.FilterChange.DIFFERENT)
        self._refresh_table()
        self._update_count_label()
        self._maybe_refresh_diskmap()

    def _on_source_changed(self, dropdown, _pspec):
        index = dropdown.get_selected()
        if 0 <= index < len(self._source_choices):
            self._source_filter = self._source_choices[index]
        else:
            self._source_filter = (None, None)
        self._filter.changed(Gtk.FilterChange.DIFFERENT)
        self._refresh_table()
        self._update_count_label()
        self._maybe_refresh_diskmap()

    # ---------------- Loading ----------------

    def refresh(self):
        if self._busy:
            return
        self._set_busy(True)
        self._stack.set_visible_child_name("loading")

        def worker():
            from .games import available_game_backends
            # Oyunlar önce gelir: kaynak dropdown'ında Pacman'ın
            # onlarca repo alt kırılımının arkasında kaybolmasınlar,
            # "Tüm kaynaklar"ın hemen altında görünsünler.
            backends = available_game_backends() + available_backends()
            packages = []
            for backend in backends:
                try:
                    packages.extend(backend.list_packages())
                except Exception:
                    pass  # a broken backend must not take the app down
            try:
                icon_map, launcher_map, category_map = build_maps()
            except Exception:
                icon_map, launcher_map, category_map = {}, {}, {}
            GLib.idle_add(
                self._on_loaded, backends, packages,
                icon_map, launcher_map, category_map,
            )

        threading.Thread(target=worker, daemon=True).start()

    def _on_loaded(self, backends, packages, icon_map, launcher_map, category_map):
        self._backends = backends
        self._icon_map = icon_map
        self._launcher_map = launcher_map
        self._category_map = category_map
        self._items = [PackageItem(pkg) for pkg in packages]
        # Filtreden bağımsız, sabit bir referans: bir uygulamanın toplam
        # disk kullanımındaki payı, hangi kaynak filtresine bakıyor
        # olursanız olun aynı kalmalı — filtrelenmiş listeye göre
        # hesaplanırsa aynı uygulama filtre değişince farklı yüzde
        # gösterir, bu kafa karıştırıcıdır.
        self._total_size = sum(item.pkg.size for item in self._items) or 1

        self._store.remove_all()
        # splice is far faster than thousands of append() calls
        self._store.splice(0, 0, self._items)

        self._rebuild_source_dropdown(reset=True)
        self._refresh_table()

        if self._diskmap_toggle.get_active():
            self._refresh_diskmap()
            self._stack.set_visible_child_name("diskmap")
        else:
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
        empty = shown == 0 and len(self._items) > 0
        self._scrolled.set_visible(not empty)
        self._list_empty_status.set_visible(empty)
        text = _("{shown} of {total} packages").format(
            shown=shown, total=len(self._items)
        )
        selected = self._table.get_selected_items()
        if selected:
            total_size = sum(item.pkg.size for item in selected)
            text += "  •  " + _("{count} selected").format(count=len(selected))
            if total_size:
                text += f" ({format_size(total_size)})"
        if self._table.is_selection_mode():
            text = _("Selection mode (Esc to finish)") + "  •  " + text
        self._count_label.set_label(text)

        # Liste görünümünde de tek seçili uygulama için ad/boyut/oran —
        # disk haritasındaki aynı etiket alanı, o görünüm açıkken onun
        # kendi gezinme geri çağrısı tarafından yönetiliyor
        if self._stack.get_visible_child_name() != "diskmap":
            self._diskmap_hover_label.set_label(self._single_selection_info(selected))

    def _single_selection_info(self, selected) -> str:
        if len(selected) != 1:
            return ""
        pkg = selected[0].pkg
        percent = pkg.size / self._total_size * 100
        return _("{name} — {size} ({percent}%)").format(
            name=pkg.name, size=format_size(pkg.size), percent=format_percent(percent),
        )
