"""Ana ekranda listenin yerini alabilen disk haritası görünümü
(yalnızca test/dev sürümü).

Kurulu her uygulama, kurulu boyutuyla orantılı bir kutu olarak çizilir
(BCUninstaller'daki disk haritası gibi) — tek bir uygulamanın dosya
kırılımı değil, TÜM listedeki uygulamaların birbirine göre büyüklüğü.
Kutuya sol tık o uygulamayı seçer (win._context_item), sağ tık liste
satırlarıyla aynı bağlam menüsünü açar; asıl etkileşim window.py'de.
"""

from gi.repository import Adw, Gdk, Gtk

from .i18n import _

MAX_BOXES = 400

# Sabit kategorik sıra (dataviz referans paleti, 8 yuva — CVD-güvenli
# bitişik-çift sırası). Kaynak kimliği bu sıraya göre renk alır; 8'i aşan
# kaynaklar "Diğer" rengine düşer, asla yeni bir ton üretilmez.
_SOURCE_COLORS: dict[str, tuple[str, str]] = {
    "pacman": ("#2a78d6", "#3987e5"),
    "apt": ("#eb6834", "#d95926"),
    "flatpak": ("#1baf7a", "#199e70"),
    "snap": ("#eda100", "#c98500"),
    "dnf": ("#e87ba4", "#d55181"),
    "appimage": ("#008300", "#008300"),
    "zypper": ("#4a3aa7", "#9085e9"),
    "apk": ("#e34948", "#e66767"),
}
_OTHER_COLOR = ("#898781", "#898781")
_SOURCE_DISPLAY = {
    "pacman": "Pacman", "apt": "APT", "flatpak": "Flatpak", "snap": "Snap",
    "dnf": "DNF", "appimage": "AppImage", "zypper": "Zypper", "apk": "APK",
    "xbps": "XBPS", "portage": "Portage", "nix": "Nix",
}


def format_percent(percent: float) -> str:
    """Küçük paylar sabit tek ondalıkla yuvarlanınca "0.0" gibi anlamsız
    görünüyor; oranın büyüklüğüne göre basamak sayısını artırır."""
    if percent <= 0:
        return "0"
    if percent >= 10:
        return f"{percent:.0f}"
    if percent >= 1:
        return f"{percent:.1f}"
    if percent >= 0.01:
        return f"{percent:.2f}"
    return f"{percent:.4f}"


def _hex_to_rgb(value: str) -> tuple[float, float, float]:
    value = value.lstrip("#")
    return (
        int(value[0:2], 16) / 255,
        int(value[2:4], 16) / 255,
        int(value[4:6], 16) / 255,
    )


def _box_color(source: str, dark: bool) -> tuple[float, float, float]:
    light_hex, dark_hex = _SOURCE_COLORS.get(source, _OTHER_COLOR)
    return _hex_to_rgb(dark_hex if dark else light_hex)


def _label_color(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    r, g, b = rgb
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return (0.05, 0.05, 0.05) if luminance > 0.6 else (1.0, 1.0, 1.0)


# ---------------- Squarified treemap yerleşimi (saf Python) ----------------
#
# Bruls, Huizing, van Wijk'in "worst aspect ratio" algoritması: her satır,
# mevcut dikdörtgenin kısa kenarı boyunca en kare kutuları üretecek şekilde
# doldurulur. Girdi: pozitif boyutlar; çıktı: aynı sırada (x, y, w, h).

def _worst_ratio(row: list[float], side: float) -> float:
    total = sum(row)
    if total <= 0 or side <= 0:
        return float("inf")
    row_max, row_min = max(row), min(row)
    a = (side * side * row_max) / (total * total)
    b = (total * total) / (side * side * row_min)
    return max(a, b)


def _layout_row(row, x, y, w, h):
    total = sum(row)
    if total <= 0:
        return [], (x, y, w, h)
    if w >= h:
        thickness = total / h if h else 0
        rects, cy = [], y
        for s in row:
            rh = (s / total) * h
            rects.append((x, cy, thickness, rh))
            cy += rh
        return rects, (x + thickness, y, max(w - thickness, 0), h)
    thickness = total / w if w else 0
    rects, cx = [], x
    for s in row:
        rw = (s / total) * w
        rects.append((cx, y, rw, thickness))
        cx += rw
    return rects, (x, y + thickness, w, max(h - thickness, 0))


def squarify(sizes: list[float], x: float, y: float, w: float, h: float):
    """sizes ile aynı sırada (x, y, w, h) dikdörtgenleri döner.

    Orijinal index her adımda boyutla birlikte taşınır, böylece
    algoritmanın kendi içindeki (büyükten küçüğe) sıralaması sonuçları
    çağıranın sırasıyla karıştırmaz.
    """
    indexed = sorted(
        ((i, s) for i, s in enumerate(sizes) if s > 0),
        key=lambda pair: -pair[1],
    )
    if not indexed or w <= 0 or h <= 0:
        return [(0, 0, 0, 0) for _ in sizes]

    total = sum(s for _i, s in indexed)
    scale = (w * h) / total
    remaining = [(i, s * scale) for i, s in indexed]

    result: list[tuple[float, float, float, float]] = [(0, 0, 0, 0)] * len(sizes)
    row: list[tuple[int, float]] = []
    rx, ry, rw, rh = x, y, w, h

    def flush_row():
        nonlocal row, rx, ry, rw, rh
        laid, (rx, ry, rw, rh) = _layout_row([s for _i, s in row], rx, ry, rw, rh)
        for (idx, _s), rect in zip(row, laid):
            result[idx] = rect
        row = []

    while remaining:
        side = min(rw, rh)
        row_sizes = [s for _i, s in row]
        candidate_sizes = row_sizes + [remaining[0][1]]
        if not row or _worst_ratio(row_sizes, side) >= _worst_ratio(candidate_sizes, side):
            row.append(remaining.pop(0))
        else:
            flush_row()
    if row:
        flush_row()
    return result


class DiskMapArea(Gtk.DrawingArea):
    """Kurulu uygulamaları boyutlarına göre kutulara döken harita widget'ı."""

    def __init__(self):
        super().__init__(hexpand=True, vexpand=True)
        self._items: list = []  # PackageItem listesi (item.pkg ile erişilir)
        self._rects: list[tuple[object, tuple[float, float, float, float]]] = []
        self._total_size = 1
        self._selected_item = None
        self._hover_item = None
        self.on_select = None          # callback(item)
        self.on_context_menu = None    # callback(item, x, y)
        self.on_hover = None           # callback(text | None)

        self.set_draw_func(self._on_draw)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        self.add_controller(motion)

        left_click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        left_click.connect("released", self._on_left_click)
        self.add_controller(left_click)

        right_click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        right_click.connect("released", self._on_right_click)
        self.add_controller(right_click)

    def set_items(self, items):
        """items: .pkg özniteliği olan nesneler (PackageItem)."""
        items = sorted(items, key=lambda it: -it.pkg.size)[:MAX_BOXES]
        self._items = items
        self._total_size = sum(item.pkg.size for item in items) or 1
        self._selected_item = None
        self._hover_item = None
        self.queue_draw()

    def _is_dark(self) -> bool:
        return Adw.StyleManager.get_default().get_dark()

    def _on_draw(self, _area, cr, width, height, *_a):
        self._rects = []
        if not self._items:
            return
        dark = self._is_dark()
        sizes = [item.pkg.size for item in self._items]
        rects = squarify(sizes, 0, 0, width, height)
        self._rects = list(zip(self._items, rects))

        for item, (x, y, w, h) in self._rects:
            if w <= 1 or h <= 1:
                continue
            color = _box_color(item.pkg.source, dark)
            bx, by = x + 1, y + 1
            bw, bh = max(w - 2, 0), max(h - 2, 0)
            cr.set_source_rgb(*color)
            cr.rectangle(bx, by, bw, bh)
            cr.fill()

            if item is self._selected_item:
                # Herhangi bir kutu rengine karşı görünsün diye siyah+beyaz
                # çift kontur (tek renk seçili kutunun tonuna karışabilir)
                ring_x, ring_y = bx + 1.5, by + 1.5
                ring_w, ring_h = max(bw - 3, 0), max(bh - 3, 0)
                cr.set_line_width(3)
                cr.set_source_rgb(0, 0, 0)
                cr.rectangle(ring_x, ring_y, ring_w, ring_h)
                cr.stroke()
                cr.set_line_width(1.2)
                cr.set_source_rgb(1, 1, 1)
                cr.rectangle(ring_x, ring_y, ring_w, ring_h)
                cr.stroke()
            elif item is self._hover_item:
                # Üzerinden geçilen kutuyu da hafifçe belli et — seçiliden
                # daha ince, tek beyaz çerçeve
                ring_x, ring_y = bx + 1, by + 1
                ring_w, ring_h = max(bw - 2, 0), max(bh - 2, 0)
                cr.set_line_width(2)
                cr.set_source_rgba(1, 1, 1, 0.85)
                cr.rectangle(ring_x, ring_y, ring_w, ring_h)
                cr.stroke()

            if bw > 46 and bh > 18:
                cr.set_source_rgb(*_label_color(color))
                cr.move_to(bx + 4, by + 14)
                cr.set_font_size(11)
                max_chars = max(int(bw // 7), 1)
                cr.show_text(item.pkg.name[:max_chars])

    def _item_at(self, x, y):
        for item, (rx, ry, rw, rh) in self._rects:
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                return item
        return None

    def _describe(self, item) -> str:
        from .backends.base import format_size
        percent = item.pkg.size / self._total_size * 100
        return _("{name} — {size} ({percent}%)").format(
            name=item.pkg.name,
            size=format_size(item.pkg.size),
            percent=format_percent(percent),
        )

    def _on_motion(self, _ctrl, x, y):
        item = self._item_at(x, y)
        # Kutular arasındaki ince boşluklarda item None gelebilir; harita
        # tamamen terk edilmediği sürece son gösterilen bilgiyi koru —
        # yoksa gezinirken bilgi anlık olarak boşalıp titriyor
        if item is None or item is self._hover_item:
            return
        self._hover_item = item
        self.queue_draw()
        if self.on_hover:
            self.on_hover(self._describe(item))

    def _on_leave(self, *_args):
        if self._hover_item is None:
            return
        self._hover_item = None
        self.queue_draw()
        if self.on_hover:
            self.on_hover(None)

    def _on_left_click(self, _gesture, _n, x, y):
        item = self._item_at(x, y)
        if not item:
            return
        self._selected_item = item
        self.queue_draw()
        if self.on_hover:
            self.on_hover(self._describe(item))
        if self.on_select:
            self.on_select(item)

    def _on_right_click(self, _gesture, _n, x, y):
        item = self._item_at(x, y)
        if not item:
            return
        self._selected_item = item
        self.queue_draw()
        if self.on_hover:
            self.on_hover(self._describe(item))
        if self.on_select:
            self.on_select(item)
        if self.on_context_menu:
            self.on_context_menu(item, x, y)


def build_legend(items) -> Gtk.Widget:
    """Haritada görünen kaynaklar için renk + isim lejantı."""
    sources = []
    seen = set()
    for item in items:
        source = item.pkg.source
        if source not in seen:
            seen.add(source)
            sources.append(source)
    # sabit yuva sırası; 8'i aşanlar tek bir "Diğer" girdisine katlanır
    ordered = [s for s in _SOURCE_COLORS if s in seen]
    other = [s for s in sources if s not in _SOURCE_COLORS]

    box = Gtk.Box(
        orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
        margin_top=4, margin_bottom=4, margin_start=12, margin_end=12,
    )
    dark = Adw.StyleManager.get_default().get_dark()
    for source in ordered:
        box.append(_legend_entry(
            _SOURCE_DISPLAY.get(source, source), _box_color(source, dark)
        ))
    if other:
        other_hex = _OTHER_COLOR[1] if dark else _OTHER_COLOR[0]
        box.append(_legend_entry(_("Other"), _hex_to_rgb(other_hex)))
    return box


def _legend_entry(label: str, color: tuple[float, float, float]) -> Gtk.Widget:
    r, g, b = (int(c * 255) for c in color)
    swatch = Gtk.Box(width_request=10, height_request=10, valign=Gtk.Align.CENTER)
    swatch.add_css_class("diskmap-swatch")
    css = Gtk.CssProvider()
    css.load_from_data(
        f"box.diskmap-swatch {{ background-color: rgb({r},{g},{b}); "
        f"border-radius: 2px; }}".encode()
    )
    swatch.get_style_context().add_provider(
        css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    entry = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    entry.append(swatch)
    entry.append(Gtk.Label(label=label, css_classes=["caption", "dim-label"]))
    return entry
