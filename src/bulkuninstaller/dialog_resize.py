"""Adw.Dialog'a (Ayarlar/Kullanılmayan Programlar - bkz. settings.py/
unusedwindow.py) kenardan sürükleyerek boyutlandırma ekler.

libadwaita'da Adw.Dialog için bu HİÇ yok - Adw.Window'un aksine (pencere
yöneticisinden otomatik gelirdi) burada tamamen manuel: sağ-alt köşeye
küçük bir tutamaç ekleyip sürüklemeyi content_width/content_height'e
kendimiz çeviriyoruz. Adw.Dialog kendi kendini (ana pencerenin tam
ortasına) bu iki değere göre ortaladığı için, değerleri her sürükleme
karesinde güncellemek otomatik olarak yeniden ortalamayı da getiriyor -
ayrı bir "ortala" çağrısına gerek yok.
"""

from gi.repository import Gdk, Gtk


def add_resize_grip(
    dialog, min_width=360, min_height=280,
    max_width=2400, max_height=2000, on_resize=None,
):
    """dialog.get_child()'ı bir Gtk.Overlay'e sarıp sağ-alt köşeye bir
    sürükle-tutamaç ekler. on_resize(new_width, new_height) verilirse,
    her gerçek boyut değişiminde (yalnızca gerçekten değiştiğinde)
    çağrılır - örn. UnusedAppsWindow'un kendi iç satır-sabitleme
    bütçesini de aynı anda güncellemesi için."""
    child = dialog.get_child()
    # ONCE dialog'dan ayirmadan overlay'e eklenemiyor - GTK4 bir widget'in
    # ayni anda iki ebeveyni olmasina izin vermiyor (assertion hatasi
    # veriyor, sessizce yok sayiyor).
    dialog.set_child(None)
    overlay = Gtk.Overlay()
    overlay.set_child(child)

    grip = Gtk.DrawingArea(
        content_width=16, content_height=16,
        halign=Gtk.Align.END, valign=Gtk.Align.END,
        margin_end=3, margin_bottom=3,
        css_classes=["pw-resize-grip"],
    )
    grip.set_cursor(Gdk.Cursor.new_from_name("nwse-resize"))

    def _draw(_area, cr, w, h):
        cr.set_line_width(1.4)
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.55)
        for i in range(3):
            off = i * 4 + 3
            cr.move_to(w - 1, h - off)
            cr.line_to(w - off, h - 1)
            cr.stroke()
    grip.set_draw_func(_draw)

    state = {"w": 0, "h": 0}
    drag = Gtk.GestureDrag()

    def _on_begin(_g, _x, _y):
        state["w"] = dialog.get_content_width()
        state["h"] = dialog.get_content_height()

    def _on_update(_g, dx, dy):
        new_w = max(min_width, min(max_width, state["w"] + int(dx)))
        new_h = max(min_height, min(max_height, state["h"] + int(dy)))
        changed = (
            new_w != dialog.get_content_width()
            or new_h != dialog.get_content_height()
        )
        dialog.set_content_width(new_w)
        dialog.set_content_height(new_h)
        if changed and on_resize is not None:
            on_resize(new_w, new_h)

    drag.connect("drag-begin", _on_begin)
    drag.connect("drag-update", _on_update)
    grip.add_controller(drag)

    overlay.add_overlay(grip)
    dialog.set_child(overlay)
