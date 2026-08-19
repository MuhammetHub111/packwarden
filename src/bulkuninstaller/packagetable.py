"""Ana paket listesi için özel çizilen tablo widget'ı.

Gtk.ColumnView'in yerini alıyor çünkü GTK'nin bu widget'ı, bir sütun
büyük ölçüde genişleyince başlıkla satır hücrelerinin senkronunu
kaybediyor (bu oturumda iki farklı yöntemle — expand=True ve kod
içinden set_fixed_width() — doğrulandı, ayarla düzeltilemiyor).

Sütunların ekran konumu (x) her boyutlandırmada yeniden hesaplanır
(bkz. _reflow_column_x) — normal tablo davranışı: bir sütun
daralınca/genişleyince ondan sonrakiler (başlıkları dahil) onu takip
eder. Sütun başlıkları ayrıca sürüklenerek yeniden sıralanabilir
(bkz. _on_drag_update'teki "reorder" modu).

diskmap.py'deki DiskMapArea ile aynı deyim izleniyor: Cairo ile elle
çizim, elle hit-test, düz Python callback'leriyle (on_hover,
on_context_menu, on_selection_changed, on_columns_reordered)
window.py'ye bağlanıyor. Farklı olarak bu widget kaydırılabilir
içerik taşıdığı için ayrıca Gtk.Scrollable uyguluyor —
Gtk.ScrolledWindow bunu görünce widget'ı bir Viewport'a sarmadan
doğrudan çocuğu yapıyor, böylece 1447 satırın tamamını tek seferde
devasa bir tuvale çizmek yerine gerçek anlamda sadece görünen aralık
çiziliyor.
"""

import bisect
import os
from dataclasses import dataclass, field
from typing import Callable

from gi.repository import Gdk, GdkPixbuf, GLib, GObject, Gtk, Pango, PangoCairo

ROW_HEIGHT = 44
HEADER_HEIGHT = 40
SECTION_ROW_HEIGHT = 30
HEADER_TEXT_MIN_WIDTH = 24
HANDLE_HOT_ZONE = 8  # dar sütunlarda (Sürüm/Boyut, 90px) kenarı tutturmak
# kolay olsun diye ±4'ten büyütüldü — kod mantığı doğruydu ama fareyle
# tam o pikseli bulmak zordu, ıskalayınca sürükleme "reorder"a düşüp
# hiç genişlemiyormuş gibi görünüyordu
CLICK_SLOP = 4
REORDER_SLOP = 10  # sıralama-tıklaması ile başlık sürükleme arasındaki eşik
ICON_SIZE = 32
CELL_PADDING = 10
FONT_SIZE = 11 * Pango.SCALE


@dataclass
class Column:
    id: str
    title: str
    width: float
    min_width: float = 24
    max_width: float | None = None
    align: str = "left"  # "left" | "right"
    sort_key: Callable[[object], object] | None = None
    section_key: Callable[[object], str] | None = None
    # draw_content(table, cr, column, item, x, y, width, height, selected)
    draw_content: Callable | None = None
    x: float = field(default=0.0, init=False)


@dataclass
class RowEntry:
    kind: str  # "item" | "section"
    y: float
    height: float
    position: int | None = None
    letter: str | None = None


def _make_layout(cr, text, width_px, *, bold=False, alignment=Pango.Alignment.LEFT):
    layout = PangoCairo.create_layout(cr)
    desc = Pango.FontDescription()
    desc.set_size(FONT_SIZE)
    if bold:
        desc.set_weight(Pango.Weight.BOLD)
    layout.set_font_description(desc)
    layout.set_text(text or "", -1)
    layout.set_width(Pango.units_from_double(max(width_px, 0)))
    layout.set_ellipsize(Pango.EllipsizeMode.END)
    layout.set_alignment(alignment)
    return layout


def _rounded_rect_path(cr, x, y, w, h, r):
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -1.5708, 0)
    cr.arc(x + w - r, y + h - r, r, 0, 1.5708)
    cr.arc(x + r, y + h - r, r, 1.5708, 3.14159)
    cr.arc(x + r, y + r, r, 3.14159, 4.71239)
    cr.close_path()


class PackageTableArea(Gtk.DrawingArea, Gtk.Scrollable):
    """Sütun sırası sürüklenerek değiştirilebilen, kendi çizen tablo."""

    __gtype_name__ = "PackageTableArea"

    hadjustment = GObject.Property(type=Gtk.Adjustment, default=None)
    vadjustment = GObject.Property(type=Gtk.Adjustment, default=None)
    hscroll_policy = GObject.Property(
        type=Gtk.ScrollablePolicy, default=Gtk.ScrollablePolicy.MINIMUM
    )
    vscroll_policy = GObject.Property(
        type=Gtk.ScrollablePolicy, default=Gtk.ScrollablePolicy.MINIMUM
    )

    def __init__(self):
        super().__init__(hexpand=True, vexpand=True, focusable=True)

        self._columns: list[Column] = []
        self._raw_items: list = []
        self._flat_items: list = []
        self._rows: list[RowEntry] = []
        self._row_ys: list[float] = []
        self._position_to_row: dict[int, RowEntry] = {}
        self._total_height = 0.0
        self._total_width = 0.0

        self._selected: set[int] = set()
        self._selection_mode = False
        self._select_anchor: int | None = None
        self._hover_position: int | None = None
        self._focus_position: int | None = None

        self._sort_column_id: str | None = None
        self._sort_desc = False
        self._section_column: Column | None = None

        self._icon_cache: dict[tuple[str, int], object] = {}

        self._drag_mode: str | None = None
        self._resize_col: Column | None = None
        self._resize_start_width = 0.0
        self._drag_col: Column | None = None
        self._drag_start_x = 0.0
        self._reorder_col: Column | None = None
        self._reorder_target_index: int | None = None
        self._reorder_drag_x = 0.0  # sürüklenen başlık "çipinin" takip ettiği imleç x'i
        self._drag_row: RowEntry | None = None
        self._drag_ctrl = False
        self._drag_shift = False
        self._rubber_start: tuple[float, float] | None = None
        self._rubber_rect: tuple[float, float, float, float] | None = None
        self._rubber_base: set[int] = set()

        # Bırakınca hedef konuma "oturma" parıltısı — orta fare pan'daki
        # gibi add_tick_callback ile gerçek zamanlı, sönümlenen bir efekt
        self._settle_col: Column | None = None
        self._settle_progress = 0.0
        self._settle_tick_id: int | None = None
        self._settle_start_us: int | None = None

        self.on_hover: Callable | None = None
        self.on_context_menu: Callable | None = None
        self.on_selection_changed: Callable | None = None
        self.on_columns_reordered: Callable | None = None

        self.set_draw_func(self._on_draw)
        self.connect("notify::vadjustment", self._on_adjustment_replaced)
        self.connect("notify::hadjustment", self._on_adjustment_replaced)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        self.add_controller(motion)

        drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.add_controller(drag)

        right_click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        right_click.connect("pressed", self._on_right_click)
        self.add_controller(right_click)

        long_press = Gtk.GestureLongPress(touch_only=False)
        long_press.connect("pressed", self._on_long_press)
        self.add_controller(long_press)

        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_controller)

    # ---------------- Ortak yardımcılar ----------------

    def _fg_rgba(self):
        return self.get_color()

    def _accent_rgba(self):
        ok, color = self.get_style_context().lookup_color("accent_color")
        return color if ok else self._fg_rgba()

    def _accent_bg_rgba(self):
        ok, color = self.get_style_context().lookup_color("accent_bg_color")
        return color if ok else self._accent_rgba()

    def _grid_alpha(self, alpha, fg=None):
        """Kenar çizgisi/ayraç/hover gibi ince, fg renginde düşük opaklıklı
        katmanlar için alfa. Aynı düşük opaklık, koyu temada (beyaz fg)
        açık temadakinden (siyah fg) belirgin şekilde daha görünür oluyor
        — göz siyah-beyaz zeminde/beyaz-siyah zeminde eşit opaklığı eşit
        algılamıyor. Açık temada (fg koyuysa) telafi için alfayı artırır."""
        fg = fg or self._fg_rgba()
        luminance = 0.2126 * fg.red + 0.7152 * fg.green + 0.0722 * fg.blue
        if luminance < 0.5:
            alpha = min(alpha * 1.8, 1.0)
        return alpha

    def _hadj_value(self):
        adj = self.get_hadjustment()
        return adj.get_value() if adj else 0.0

    def _vadj_value(self):
        adj = self.get_vadjustment()
        return adj.get_value() if adj else 0.0

    def _viewport_to_content(self, x, y):
        return x + self._hadj_value(), (y - HEADER_HEIGHT) + self._vadj_value()

    # ---------------- Genel çizim yardımcıları (window.py bunları kullanır) ----------------

    def draw_text_cell(self, cr, x, y, w, h, text, *, bold=False, dim=False,
                        align="left", color=None):
        if w <= 2 or not text:
            return
        inner_w = max(w - 2 * CELL_PADDING, 0)
        alignment = Pango.Alignment.RIGHT if align == "right" else Pango.Alignment.LEFT
        layout = _make_layout(cr, text, inner_w, bold=bold, alignment=alignment)
        _tw, text_h = layout.get_pixel_size()
        fg = color or self._fg_rgba()
        alpha = fg.alpha * (0.6 if dim else 1.0)
        cr.save()
        cr.rectangle(x, y, w, h)
        cr.clip()
        cr.set_source_rgba(fg.red, fg.green, fg.blue, alpha)
        cr.move_to(x + CELL_PADDING, y + (h - text_h) / 2)
        PangoCairo.show_layout(cr, layout)
        cr.restore()

    def draw_two_line_cell(self, cr, x, y, w, h, top_text, bottom_text, *,
                            top_color=None, align="right"):
        if w <= 2:
            return
        top_color = top_color or self._accent_rgba()
        top_h = h * 0.52
        self.draw_text_cell(cr, x, y, w, top_h, top_text, align=align, color=top_color)
        if bottom_text:
            self.draw_text_cell(cr, x, y + top_h, w, h - top_h, bottom_text,
                                 align=align, dim=True)

    def draw_icon_and_text_cell(self, cr, x, y, w, h, icon_ref, text, *, selected=False):
        if w <= 2:
            return
        cr.save()
        cr.rectangle(x, y, w, h)
        cr.clip()

        cursor = x + CELL_PADDING
        # Seçim hapı (rounded capsule), boş bırakılınca yazı zıplamasın
        pill_w = 6
        if selected:
            accent = self._accent_bg_rgba()
            cr.set_source_rgba(accent.red, accent.green, accent.blue, accent.alpha)
            radius = pill_w / 2
            pill_y = y + (h - 28) / 2
            cr.arc(cursor + radius, pill_y + radius, radius, 3.14159, 0)
            cr.arc(cursor + radius, pill_y + 28 - radius, radius, 0, 3.14159)
            cr.close_path()
            cr.fill()
        cursor += pill_w + 8

        icon = self._load_icon_pixbuf(icon_ref, ICON_SIZE) if icon_ref else None
        if icon is not None:
            pixbuf, is_symbolic = icon
            icon_y = y + (h - ICON_SIZE) / 2
            if is_symbolic:
                # Sembolik ikonlar (ör. Breeze'in currentColor'a bağlı
                # cihaz/durum simgeleri) kendi dosyalarına gömülü sabit
                # bir renkle geliyor — GTK dışında ham pixbuf olarak
                # okununca o sabit renk hiç temaya uymuyor (bkz. commit
                # mesajı). Rengi görmezden gelip yalnızca şeklini
                # (alfa kanalını) o anki metin rengiyle boyuyoruz.
                icon_surface = Gdk.cairo_surface_create_from_pixbuf(
                    pixbuf, self.get_scale_factor(), None
                )
                fg = self._fg_rgba()
                cr.set_source_rgba(fg.red, fg.green, fg.blue, fg.alpha)
                cr.mask_surface(icon_surface, cursor, icon_y)
            else:
                Gdk.cairo_set_source_pixbuf(cr, pixbuf, cursor, icon_y)
                cr.paint()
        cursor += ICON_SIZE + 12

        remaining = max(x + w - cursor - CELL_PADDING, 0)
        layout = _make_layout(cr, text, remaining, bold=True)
        _tw, text_h = layout.get_pixel_size()
        fg = self._fg_rgba()
        cr.set_source_rgba(fg.red, fg.green, fg.blue, fg.alpha)
        cr.move_to(cursor, y + (h - text_h) / 2)
        PangoCairo.show_layout(cr, layout)
        cr.restore()

    # ---------------- İkon önbelleği ----------------

    def _load_icon_pixbuf(self, icon_ref: str, size: int):
        """(pixbuf, is_symbolic) döner; yüklenemezse None.

        is_symbolic True ise pixbuf'un RGB değerleri güvenilmez — GTK bu
        ikonu "sembolik" (tek renkli, currentColor'a bağlı) olarak
        işaretlemiş demektir; çağıran yalnızca alfa kanalını (şekli)
        kullanıp o anki metin rengiyle boyamalı, bkz. draw_icon_and_text_cell.
        """
        key = (icon_ref, size)
        if key in self._icon_cache:
            return self._icon_cache[key]
        result = None
        try:
            if os.path.isabs(icon_ref) and os.path.isfile(icon_ref):
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(icon_ref, size, size)
                result = (pixbuf, False)
            else:
                display = self.get_display()
                if display is not None:
                    theme = Gtk.IconTheme.get_for_display(display)
                    paintable = theme.lookup_icon(
                        icon_ref, None, size, self.get_scale_factor(),
                        Gtk.TextDirection.NONE, Gtk.IconLookupFlags.FORCE_REGULAR,
                    )
                    file = paintable.get_file() if paintable else None
                    path = file.get_path() if file else None
                    if path:
                        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(path, size, size)
                        result = (pixbuf, paintable.is_symbolic())
        except (GLib.Error, OSError):
            result = None
        self._icon_cache[key] = result
        return result

    # ---------------- Genel API ----------------

    def add_column(self, column: Column):
        self._columns.append(column)
        self._reflow_column_x()
        if self._sort_column_id is None and column.sort_key:
            self._sort_column_id = column.id

    def reorder_columns(self, id_order: list[str]):
        """Sütunları verilen kimlik sırasına göre yeniden diz — daha
        önce kaydedilmiş (örn. tercihlerden okunan) bir sırayı geri
        uygulamak için. Listede olmayan kimlikler yok sayılır, mevcut
        listede olup id_order'da geçmeyen sütunlar sona eklenir."""
        by_id = {c.id: c for c in self._columns}
        ordered = [by_id[cid] for cid in id_order if cid in by_id]
        remaining = [c for c in self._columns if c.id not in id_order]
        self._columns = ordered + remaining
        self._reflow_column_x()
        self.queue_draw()

    def get_column_order(self) -> list[str]:
        return [c.id for c in self._columns]

    def is_leading_column(self, column: Column) -> bool:
        """Sütun sürüklenerek yeniden sıralanabildiği için sağa yaslı
        bir sütun (Sürüm/Boyut gibi) en başa gelebilir — o zaman sağa
        yaslı kalması "ters" görünür, çünkü ilk sütunun doğal okuma
        noktası hep sol kenardır. Çağıranlar (window.py'deki çizim
        callback'leri) bunu kontrol edip en baştaysa sola yaslamalı."""
        return bool(self._columns) and self._columns[0] is column

    def _reflow_column_x(self):
        cursor = 0.0
        for col in self._columns:
            col.x = cursor
            cursor += col.width
        self._total_width = cursor

    def get_column(self, column_id: str) -> Column | None:
        for col in self._columns:
            if col.id == column_id:
                return col
        return None

    def set_items(self, items):
        self._raw_items = list(items)
        self._rebuild()

    def get_selected_items(self):
        return [
            self._flat_items[p] for p in sorted(self._selected)
            if 0 <= p < len(self._flat_items)
        ]

    def is_selection_mode(self) -> bool:
        return self._selection_mode

    def exit_selection_mode(self):
        if self._selection_mode or self._selected:
            self._selection_mode = False
            self._selected.clear()
            self._select_anchor = None
            self.queue_draw()
            if self.on_selection_changed:
                self.on_selection_changed()

    # ---------------- Sıralama + satır düzeni ----------------

    def _rebuild(self):
        items = sorted(self._raw_items, key=lambda it: it.pkg.name.lower())
        col = self._column_sort_col()
        if col and col.sort_key:
            items = sorted(items, key=col.sort_key, reverse=self._sort_desc)
        self._flat_items = items
        self._layout_rows(items, col)
        self._selected.clear()
        self._selection_mode = False
        self._select_anchor = None
        self._hover_position = None
        self._focus_position = None
        self._sync_adjustments()
        self.queue_draw()
        if self.on_selection_changed:
            self.on_selection_changed()

    def _column_sort_col(self) -> Column | None:
        if self._sort_column_id is None:
            return None
        return self.get_column(self._sort_column_id)

    def _layout_rows(self, items, sort_col):
        rows: list[RowEntry] = []
        row_ys: list[float] = []
        position_to_row: dict[int, RowEntry] = {}
        y = 0.0
        use_sections = bool(sort_col and sort_col.section_key)
        # Harf grubu yazısı hangi sütuna aitse (bugün "Ad") onun x/width
        # sınırlarına kırpılacak — sabit bir global konumda çizilirse o
        # sütun daraltılınca harfler onunla birlikte küçülmez/kaybolmaz.
        self._section_column = sort_col if use_sections else None
        prev_letter = None
        for position, item in enumerate(items):
            if use_sections:
                letter = sort_col.section_key(item)
                if letter != prev_letter:
                    header = RowEntry(kind="section", y=y, height=SECTION_ROW_HEIGHT,
                                       letter=letter)
                    rows.append(header)
                    row_ys.append(y)
                    y += SECTION_ROW_HEIGHT
                    prev_letter = letter
            row = RowEntry(kind="item", y=y, height=ROW_HEIGHT, position=position)
            rows.append(row)
            row_ys.append(y)
            position_to_row[position] = row
            y += ROW_HEIGHT
        self._rows = rows
        self._row_ys = row_ys
        self._position_to_row = position_to_row
        self._total_height = y

    def sort_by(self, column_id: str):
        if self._sort_column_id == column_id:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_column_id = column_id
            self._sort_desc = False
        self._rebuild()

    def get_sort_state(self):
        return self._sort_column_id, self._sort_desc

    # ---------------- Scrollable ----------------

    def _on_adjustment_replaced(self, _widget, _pspec):
        adj = self.get_vadjustment() if _pspec.name == "vadjustment" else self.get_hadjustment()
        if adj is not None:
            adj.connect("value-changed", lambda *_a: self.queue_draw())
        self._sync_adjustments()

    def _sync_adjustments(self):
        vadj = self.get_vadjustment()
        if vadj is not None:
            alloc_h = max(self.get_height() - HEADER_HEIGHT, 0)
            vadj.configure(
                min(vadj.get_value(), max(self._total_height - alloc_h, 0)),
                0, self._total_height, ROW_HEIGHT, alloc_h, alloc_h,
            )
        hadj = self.get_hadjustment()
        if hadj is not None:
            alloc_w = self.get_width()
            hadj.configure(
                min(hadj.get_value(), max(self._total_width - alloc_w, 0)),
                0, max(self._total_width, alloc_w), 40, alloc_w, alloc_w,
            )

    def do_size_allocate(self, width, height, baseline):
        Gtk.DrawingArea.do_size_allocate(self, width, height, baseline)
        self._sync_adjustments()

    def do_measure(self, _orientation, _for_size):
        return (0, 0, -1, -1)

    # ---------------- Çizim ----------------

    def _on_draw(self, _area, cr, width, height):
        hval, vval = self._hadj_value(), self._vadj_value()

        cr.save()
        cr.rectangle(0, 0, width, HEADER_HEIGHT)
        cr.clip()
        cr.translate(-hval, 0)
        self._draw_header(cr, width + hval)
        cr.restore()

        cr.save()
        cr.rectangle(0, HEADER_HEIGHT, width, max(height - HEADER_HEIGHT, 0))
        cr.clip()
        cr.translate(-hval, HEADER_HEIGHT - vval)
        self._draw_rows(cr, vval, vval + max(height - HEADER_HEIGHT, 0))
        cr.restore()

        if self._rubber_rect:
            rx, ry, rw, rh = self._rubber_rect
            cr.save()
            accent = self._accent_bg_rgba()
            cr.set_source_rgba(accent.red, accent.green, accent.blue, 0.18)
            cr.rectangle(rx, ry, rw, rh)
            cr.fill()
            cr.set_source_rgba(accent.red, accent.green, accent.blue, 0.6)
            cr.set_line_width(1)
            cr.rectangle(rx, ry, rw, rh)
            cr.stroke()
            cr.restore()

    def _draw_header(self, cr, visible_width):
        fg = self._fg_rgba()
        dragging_col = self._reorder_col if self._drag_mode == "reorder" else None
        for col in self._columns:
            cr.save()
            cr.rectangle(col.x, 0, col.width, HEADER_HEIGHT)
            cr.clip()

            if col is dragging_col:
                # Yuvası boş/soluk kalır — başlığın kendisi, çip olarak
                # sürüklenirken imleci takip edecek (bkz. altta
                # _draw_floating_header_chip). Burada yazı çizilmez.
                cr.set_source_rgba(fg.red, fg.green, fg.blue, self._grid_alpha(0.04, fg))
                cr.paint()
                cr.restore()
                edge = col.x + col.width
                cr.set_source_rgba(fg.red, fg.green, fg.blue, self._grid_alpha(0.08, fg))
                cr.set_line_width(1)
                cr.move_to(edge, 0)
                cr.line_to(edge, HEADER_HEIGHT)
                cr.stroke()
                continue

            if col.width >= HEADER_TEXT_MIN_WIDTH:
                inner_w = max(col.width - 2 * CELL_PADDING - 12, 0)
                is_right = col.align == "right" and not self.is_leading_column(col)
                alignment = Pango.Alignment.RIGHT if is_right else Pango.Alignment.LEFT
                layout = _make_layout(cr, col.title, inner_w, bold=True,
                                       alignment=alignment)
                _tw, text_h = layout.get_pixel_size()
                cr.set_source_rgba(fg.red, fg.green, fg.blue, fg.alpha * 0.75)
                cr.move_to(col.x + CELL_PADDING, (HEADER_HEIGHT - text_h) / 2)
                PangoCairo.show_layout(cr, layout)

                if col.id == self._sort_column_id:
                    ax = col.x + col.width - CELL_PADDING - 4
                    ay = HEADER_HEIGHT / 2
                    cr.set_source_rgba(fg.red, fg.green, fg.blue, fg.alpha * 0.9)
                    if self._sort_desc:
                        cr.move_to(ax - 4, ay - 2)
                        cr.line_to(ax + 4, ay - 2)
                        cr.line_to(ax, ay + 3)
                    else:
                        cr.move_to(ax - 4, ay + 2)
                        cr.line_to(ax + 4, ay + 2)
                        cr.line_to(ax, ay - 3)
                    cr.close_path()
                    cr.fill()

            if self._settle_col is col:
                # Bırakılan sütunun yeni yerine "oturma" parıltısı —
                # zamanla sönümlenen accent renginde bir dolgu.
                accent = self._accent_bg_rgba()
                alpha = accent.alpha * 0.5 * (1.0 - self._settle_progress)
                cr.set_source_rgba(accent.red, accent.green, accent.blue, alpha)
                cr.paint()
            cr.restore()

            edge = col.x + col.width
            is_dragging = self._drag_mode == "resize" and self._resize_col is col
            cr.set_line_width(2 if is_dragging else 1)
            if is_dragging:
                accent = self._accent_rgba()
                cr.set_source_rgba(accent.red, accent.green, accent.blue, 0.9)
            else:
                cr.set_source_rgba(fg.red, fg.green, fg.blue, self._grid_alpha(0.12, fg))
            cr.move_to(edge, 0)
            cr.line_to(edge, HEADER_HEIGHT)
            cr.stroke()

        cr.set_source_rgba(fg.red, fg.green, fg.blue, self._grid_alpha(0.15, fg))
        cr.set_line_width(1)
        cr.move_to(0, HEADER_HEIGHT - 0.5)
        cr.line_to(max(visible_width, self._total_width), HEADER_HEIGHT - 0.5)
        cr.stroke()

        if dragging_col is not None and self._reorder_target_index is not None:
            # Bırakınca sütunun nereye ekleneceğini gösteren dikey çizgi
            others = [c for c in self._columns if c is not dragging_col]
            if self._reorder_target_index < len(others):
                insert_x = others[self._reorder_target_index].x
            elif others:
                insert_x = others[-1].x + others[-1].width
            else:
                insert_x = 0
            accent = self._accent_rgba()
            cr.save()
            cr.set_source_rgba(accent.red, accent.green, accent.blue, 0.9)
            cr.set_line_width(3)
            cr.move_to(insert_x, 0)
            cr.line_to(insert_x, HEADER_HEIGHT)
            cr.stroke()
            cr.restore()

            self._draw_floating_header_chip(cr, dragging_col)

    def _draw_floating_header_chip(self, cr, col):
        """Sürüklenen başlığı, gölgeli/yükseltilmiş bir 'çip' olarak
        imlecin altında çizer — "basılı tutunca belli olsun, kalkıyor
        gibi" isteğinin görsel karşılığı."""
        chip_w = max(col.width, 40)
        chip_h = HEADER_HEIGHT - 8
        chip_x = self._reorder_drag_x - chip_w / 2
        chip_y = -3.0  # başlık şeridinin biraz üstünde, "kalkmış" gibi

        cr.save()
        # Gölge
        cr.set_source_rgba(0, 0, 0, 0.32)
        _rounded_rect_path(cr, chip_x + 2, chip_y + 5, chip_w, chip_h, 6)
        cr.fill()

        accent = self._accent_bg_rgba()
        cr.set_source_rgba(accent.red, accent.green, accent.blue, 0.96)
        _rounded_rect_path(cr, chip_x, chip_y, chip_w, chip_h, 6)
        cr.fill()

        luminance = 0.2126 * accent.red + 0.7152 * accent.green + 0.0722 * accent.blue
        text_rgb = (0.08, 0.08, 0.08) if luminance > 0.6 else (1.0, 1.0, 1.0)
        layout = _make_layout(cr, col.title, max(chip_w - 2 * CELL_PADDING, 0),
                               bold=True, alignment=Pango.Alignment.CENTER)
        _tw, text_h = layout.get_pixel_size()
        cr.set_source_rgb(*text_rgb)
        cr.move_to(chip_x + CELL_PADDING, chip_y + (chip_h - text_h) / 2)
        PangoCairo.show_layout(cr, layout)
        cr.restore()

    def _draw_rows(self, cr, vis_top, vis_bottom):
        if not self._rows:
            return
        start = max(bisect.bisect_right(self._row_ys, vis_top) - 1, 0)
        end = bisect.bisect_right(self._row_ys, vis_bottom)
        fg = self._fg_rgba()

        for row in self._rows[start:end]:
            if row.kind == "section":
                # Harf sabit bir global konumda değil, kendisini üreten
                # sütunun (bugün "Ad") x/width sınırlarına kırpılarak
                # çizilir — o sütun daraltılınca harf de onunla birlikte
                # küçülür/kaybolur, bağımsız sürükleme kuralıyla tutarlı.
                col = self._section_column
                if col is not None:
                    cr.save()
                    cr.rectangle(col.x, row.y, col.width, row.height)
                    cr.clip()
                    layout = _make_layout(cr, row.letter, max(col.width - 24, 0), bold=True)
                    cr.set_source_rgba(fg.red, fg.green, fg.blue, fg.alpha * 0.55)
                    cr.move_to(col.x + 12, row.y + SECTION_ROW_HEIGHT - 20)
                    PangoCairo.show_layout(cr, layout)
                    cr.restore()
                continue

            row_w = max(self._total_width, self.get_width() + self._hadj_value())
            if row.position in self._selected:
                accent = self._accent_bg_rgba()
                cr.set_source_rgba(accent.red, accent.green, accent.blue, 0.28)
                cr.rectangle(0, row.y, row_w, row.height)
                cr.fill()
            elif row.position == self._hover_position:
                cr.set_source_rgba(fg.red, fg.green, fg.blue, self._grid_alpha(0.05, fg))
                cr.rectangle(0, row.y, row_w, row.height)
                cr.fill()

            item = self._flat_items[row.position]
            for col in self._columns:
                cr.save()
                cr.rectangle(col.x, row.y, col.width, row.height)
                cr.clip()
                # Sütun kendi arkaplanını yeniden boyayıp önceki sütunun
                # taşmasını örter — bağımsız genişlik büyütmenin görsel
                # kuralı tam olarak bu iki satır.
                if row.position in self._selected:
                    accent = self._accent_bg_rgba()
                    cr.set_source_rgba(accent.red, accent.green, accent.blue, 0.28)
                    cr.paint()
                elif row.position == self._hover_position:
                    cr.set_source_rgba(fg.red, fg.green, fg.blue, self._grid_alpha(0.05, fg))
                    cr.paint()
                if col.draw_content:
                    col.draw_content(self, cr, col, item, col.x, row.y, col.width,
                                      row.height, row.position in self._selected)
                cr.restore()

                edge = col.x + col.width
                cr.set_source_rgba(fg.red, fg.green, fg.blue, self._grid_alpha(0.08, fg))
                cr.set_line_width(1)
                cr.move_to(edge, row.y)
                cr.line_to(edge, row.y + row.height)
                cr.stroke()

            if self._focus_position == row.position and self.has_focus():
                accent = self._accent_rgba()
                cr.set_source_rgba(accent.red, accent.green, accent.blue, 0.9)
                cr.set_line_width(1.5)
                cr.rectangle(1, row.y + 1, row_w - 2, row.height - 2)
                cr.stroke()

            cr.set_source_rgba(fg.red, fg.green, fg.blue, self._grid_alpha(0.06, fg))
            cr.set_line_width(1)
            cr.move_to(0, row.y + row.height - 0.5)
            cr.line_to(row_w, row.y + row.height - 0.5)
            cr.stroke()

    # ---------------- Hit-test ----------------

    def _row_at_content_y(self, y) -> RowEntry | None:
        if not self._row_ys or y < 0:
            return None
        i = bisect.bisect_right(self._row_ys, y) - 1
        if 0 <= i < len(self._rows):
            row = self._rows[i]
            if row.y <= y < row.y + row.height:
                return row
        return None

    def _column_at_content_x(self, x) -> Column | None:
        for col in self._columns:
            if col.x <= x < col.x + col.width:
                return col
        return None

    def _handle_at(self, content_x, viewport_y) -> Column | None:
        if viewport_y >= HEADER_HEIGHT:
            return None
        for col in self._columns:
            edge = col.x + col.width
            if edge - HANDLE_HOT_ZONE <= content_x <= edge + HANDLE_HOT_ZONE:
                return col
        return None

    # ---------------- Fare etkileşimi ----------------

    def _on_motion(self, _ctrl, x, y):
        content_x, content_y = self._viewport_to_content(x, y)
        handle = self._handle_at(content_x, y)
        self.set_cursor_from_name("col-resize" if handle else "default")

        if y < HEADER_HEIGHT:
            if self._hover_position is not None:
                self._hover_position = None
                self.queue_draw()
                if self.on_hover:
                    self.on_hover(None)
            return
        row = self._row_at_content_y(content_y)
        position = row.position if row and row.kind == "item" else None
        if position == self._hover_position:
            return
        self._hover_position = position
        self.queue_draw()
        if self.on_hover:
            item = self._flat_items[position] if position is not None else None
            self.on_hover(item)

    def _on_leave(self, *_args):
        if self._hover_position is not None:
            self._hover_position = None
            self.queue_draw()
            if self.on_hover:
                self.on_hover(None)

    def _on_right_click(self, gesture, _n_press, x, y):
        self.grab_focus()
        content_x, content_y = self._viewport_to_content(x, y)
        if y < HEADER_HEIGHT:
            return
        row = self._row_at_content_y(content_y)
        if not row or row.kind != "item":
            return
        if row.position not in self._selected:
            self._selected = {row.position}
            self._select_anchor = row.position
            self.queue_draw()
            if self.on_selection_changed:
                self.on_selection_changed()
        if self.on_context_menu:
            self.on_context_menu(self.get_selected_items(), x, y)

    def _on_long_press(self, gesture, x, y):
        content_x, content_y = self._viewport_to_content(x, y)
        if y < HEADER_HEIGHT:
            return
        row = self._row_at_content_y(content_y)
        if not row or row.kind != "item":
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.grab_focus()
        self._selection_mode = True
        self._selected.add(row.position)
        self._focus_position = row.position
        self.queue_draw()
        if self.on_selection_changed:
            self.on_selection_changed()

    def _on_drag_begin(self, gesture, x, y):
        self.grab_focus()
        content_x, content_y = self._viewport_to_content(x, y)
        state = gesture.get_current_event().get_modifier_state()
        if y < HEADER_HEIGHT:
            handle = self._handle_at(content_x, y)
            if handle:
                self._drag_mode = "resize"
                self._resize_col = handle
                self._resize_start_width = handle.width
            else:
                self._drag_mode = "header_click"
                self._drag_col = self._column_at_content_x(content_x)
                self._drag_start_x = content_x
        else:
            self._drag_mode = "row_or_band"
            self._drag_row = self._row_at_content_y(content_y)
            self._drag_ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
            self._drag_shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
            self._rubber_start = (content_x, content_y)
            self._rubber_base = set(self._selected) if self._drag_ctrl else set()

    def _on_drag_update(self, gesture, dx, dy):
        if self._drag_mode == "resize" and self._resize_col:
            col = self._resize_col
            w = self._resize_start_width + dx
            w = max(col.min_width, w)
            if col.max_width:
                w = min(col.max_width, w)
            if w != col.width:
                col.width = w
                # Sütunlar normal tablo gibi birbirini takip etsin — bu
                # yüzden her boyutlandırmada TÜM sütunların x'i (başlık
                # dahil, aynı col.x kullanılıyor) baştan hesaplanıyor.
                self._reflow_column_x()
                self._sync_adjustments()
                self.queue_draw()
        elif self._drag_mode == "header_click" and self._drag_col and abs(dx) > REORDER_SLOP:
            # Yeterince yatay sürüklendi — bu artık sıralama tıklaması
            # değil, başlığı sürükleyerek yeniden sıralama (reorder).
            self._drag_mode = "reorder"
            self._reorder_col = self._drag_col
            self._update_reorder_target(self._drag_start_x + dx)
        elif self._drag_mode == "reorder":
            self._update_reorder_target(self._drag_start_x + dx)
        elif self._drag_mode == "row_or_band" and (abs(dx) > CLICK_SLOP or abs(dy) > CLICK_SLOP):
            self._update_rubber_band(dx, dy)

    def _update_reorder_target(self, content_x):
        self._reorder_drag_x = content_x
        others = [c for c in self._columns if c is not self._reorder_col]
        idx = len(others)
        for i, col in enumerate(others):
            if content_x < col.x + col.width / 2:
                idx = i
                break
        if idx != self._reorder_target_index:
            self._reorder_target_index = idx
        self.queue_draw()

    def _start_settle_animation(self, col):
        # Orta fare pan'daki (_on_pan_tick, window.py) ile aynı yöntem:
        # add_tick_callback ile gerçek zamana bağlı, sönümlenen bir
        # parıltı — sütun yeni konumuna "oturuyor" hissi verir.
        self._settle_col = col
        self._settle_progress = 0.0
        if self._settle_tick_id is None:
            self._settle_tick_id = self.add_tick_callback(self._on_settle_tick)

    def _on_settle_tick(self, _widget, frame_clock):
        SETTLE_DURATION_US = 260_000
        now = frame_clock.get_frame_time()
        if self._settle_start_us is None:
            self._settle_start_us = now
        elapsed = now - self._settle_start_us
        if elapsed >= SETTLE_DURATION_US:
            self._settle_col = None
            self._settle_progress = 0.0
            self._settle_start_us = None
            self._settle_tick_id = None
            self.queue_draw()
            return GLib.SOURCE_REMOVE
        self._settle_progress = elapsed / SETTLE_DURATION_US
        self.queue_draw()
        return GLib.SOURCE_CONTINUE

    def _on_drag_end(self, gesture, dx, dy):
        is_click = abs(dx) <= CLICK_SLOP and abs(dy) <= CLICK_SLOP
        if self._drag_mode == "header_click" and is_click and self._drag_col:
            if self._drag_col.sort_key:
                self.sort_by(self._drag_col.id)
        elif self._drag_mode == "reorder" and self._reorder_col:
            others = [c for c in self._columns if c is not self._reorder_col]
            target = self._reorder_target_index if self._reorder_target_index is not None else len(others)
            others.insert(target, self._reorder_col)
            self._columns = others
            self._reflow_column_x()
            self._start_settle_animation(self._reorder_col)
            if self.on_columns_reordered:
                self.on_columns_reordered([c.id for c in self._columns])
        elif self._drag_mode == "row_or_band" and is_click:
            row = self._drag_row
            if row and row.kind == "item":
                self._apply_click_selection(row.position, self._drag_ctrl, self._drag_shift)
            elif not self._drag_ctrl and not self._drag_shift:
                self.exit_selection_mode()

        self._drag_mode = None
        self._resize_col = None
        self._drag_col = None
        self._reorder_col = None
        self._reorder_target_index = None
        self._drag_row = None
        self._rubber_start = None
        self._rubber_rect = None
        self.queue_draw()

    def _update_rubber_band(self, dx, dy):
        if not self._rubber_start:
            return
        sx, sy = self._rubber_start
        ex, ey = sx + dx, sy + dy
        rx, ry = min(sx, ex), min(sy, ey)
        rw, rh = abs(dx), abs(dy)
        # Ekranda göstermek için görünüm koordinatına çevir (satır alanı
        # zaten HEADER_HEIGHT - vval ile kaydırılıyor _on_draw içinde)
        self._rubber_rect = (rx, ry, rw, rh)

        lo = bisect.bisect_right(self._row_ys, ry) - 1
        hi = bisect.bisect_left(self._row_ys, ry + rh)
        touched = {
            row.position for row in self._rows[max(lo, 0):hi]
            if row.kind == "item"
        }
        self._selected = self._rubber_base | touched
        self.queue_draw()
        if self.on_selection_changed:
            self.on_selection_changed()

    def _apply_click_selection(self, position, ctrl, shift):
        if self._selection_mode:
            if position in self._selected:
                self._selected.discard(position)
                if not self._selected:
                    self._selection_mode = False
            else:
                self._selected.add(position)
        elif shift and self._select_anchor is not None:
            lo, hi = sorted((self._select_anchor, position))
            self._selected = {p for p in range(lo, hi + 1) if p in self._position_to_row}
        elif ctrl:
            if position in self._selected:
                self._selected.discard(position)
            else:
                self._selected.add(position)
            self._select_anchor = position
        else:
            self._selected = {position}
            self._select_anchor = position
        self._focus_position = position
        self.queue_draw()
        if self.on_selection_changed:
            self.on_selection_changed()

    # ---------------- Klavye ----------------

    def _on_key_pressed(self, _controller, keyval, _keycode, state):
        if not self._flat_items:
            return False
        n = len(self._flat_items)
        current = self._focus_position if self._focus_position is not None else -1
        page = max(int(self.get_height() / ROW_HEIGHT), 1)

        target = None
        if keyval == Gdk.KEY_Down:
            target = min(current + 1, n - 1) if current >= 0 else 0
        elif keyval == Gdk.KEY_Up:
            target = max(current - 1, 0) if current >= 0 else 0
        elif keyval == Gdk.KEY_Home:
            target = 0
        elif keyval == Gdk.KEY_End:
            target = n - 1
        elif keyval == Gdk.KEY_Page_Down:
            target = min(current + page, n - 1) if current >= 0 else 0
        elif keyval == Gdk.KEY_Page_Up:
            target = max(current - page, 0) if current >= 0 else 0
        else:
            return False

        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        self._apply_click_selection(target, ctrl=False, shift=shift and self._select_anchor is not None)
        if not shift:
            self._select_anchor = target
        self._ensure_position_visible(target)
        return True

    def _ensure_position_visible(self, position):
        row = self._position_to_row.get(position)
        vadj = self.get_vadjustment()
        if not row or not vadj:
            return
        page = vadj.get_page_size()
        value = vadj.get_value()
        if row.y < value:
            vadj.set_value(row.y)
        elif row.y + row.height > value + page:
            vadj.set_value(row.y + row.height - page)
