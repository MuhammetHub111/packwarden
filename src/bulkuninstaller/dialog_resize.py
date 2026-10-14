"""Adw.Dialog'a (Ayarlar/Kullanılmayan Programlar - bkz. settings.py/
unusedwindow.py) normal bir pencere gibi kenarlardan sürükleyerek
boyutlandırma ekler.

libadwaita'da Adw.Dialog için bu HİÇ yok - Adw.Window'un aksine (pencere
yöneticisinden otomatik gelirdi) burada tamamen manuel: dört kenara ve
dört köşeye ince, görünmez sürükleme şeritleri/kareleri ekleyip imleci
o yöne göre değiştiriyor, sürüklemeyi content_width/content_height'e
kendimiz çeviriyoruz. Adw.Dialog kendi kendini (ana pencerenin tam
ortasına) bu iki değere göre ortaladığı için, değerleri her sürükleme
karesinde güncellemek otomatik olarak yeniden ortalamayı da getiriyor -
ayrı bir "ortala" çağrısına gerek yok. Kullanıcı ilk başta sadece
sağ-alt köşeye küçük, görünür bir tutamaç istedi ama gerçekte kastettiği
- ana pencerede olduğu gibi, kenarın HER noktasına gelince imlecin ok
olup sürüklenebilmesiydi (canlı iki ekran görüntüsüyle gösterdi);
gerçek pencerelerde de görünür bir kenar çizgisi yok, sadece imleç
değişimi var - o yüzden şeritler/köşeler görünmez, sadece imleç ve
sürükleme davranışı var.
"""

from gi.repository import Gdk, Gtk

_EDGE = 6       # kenar seritlerinin kalinligi (px)
_CORNER = 14    # kose karelerinin kenar uzunlugu (px)


def add_resize_grip(
    dialog, min_width=360, min_height=280,
    max_width=2400, max_height=2000, on_resize=None,
):
    child = dialog.get_child()
    # ONCE dialog'dan ayirmadan overlay'e eklenemiyor - GTK4 bir widget'in
    # ayni anda iki ebeveyni olmasina izin vermiyor (assertion hatasi
    # veriyor, sessizce yok sayiyor).
    dialog.set_child(None)
    overlay = Gtk.Overlay()
    overlay.set_child(child)

    state = {"w": 0, "h": 0}

    def _apply(new_w, new_h):
        new_w = max(min_width, min(max_width, new_w))
        new_h = max(min_height, min(max_height, new_h))
        changed = (
            new_w != dialog.get_content_width()
            or new_h != dialog.get_content_height()
        )
        if not changed:
            return
        dialog.set_content_width(new_w)
        dialog.set_content_height(new_h)
        if on_resize is not None:
            on_resize(new_w, new_h)

    def _make_zone(halign, valign, cursor_name, dx_sign, dy_sign, is_edge):
        zone = Gtk.Box(halign=halign, valign=valign)
        if is_edge:
            if dx_sign == 0:
                # yatay kenar (ust/alt) - tam genislik, ince yukseklik
                zone.set_hexpand(True)
                zone.set_size_request(-1, _EDGE)
            else:
                # dikey kenar (sol/sag) - tam yukseklik, ince genislik
                zone.set_vexpand(True)
                zone.set_size_request(_EDGE, -1)
        else:
            zone.set_size_request(_CORNER, _CORNER)
        zone.set_cursor(Gdk.Cursor.new_from_name(cursor_name))
        # opacity AYARLANMIYOR: bos bir Gtk.Box zaten hicbir sey
        # cizmiyor, gorunmezligi icin opacity'e hic gerek yok. Onceki
        # surumde "tam 0 hit-testi de kapatiyor" varsayimiyla 0.0001
        # gibi sira disi bir deger kullanilmisti - dogrudan test edip
        # bunun YANLIS oldugunu dogruladim (opacity=0 ile pick() hala
        # dogru widget'i buluyor); sira disi, neredeyse-sifir-ama-sifir-
        # degil bir opacity degeri, GTK'nin kompozisyon/onbellekleme
        # yollarinda pencere yeniden boyutlanirken garip, eskimis
        # goruntu kalintilarina yol acabilecek turden bir sey - kullanici
        # canli olarak boyut degistirince bulanik/karismis gorsel
        # bozulma bildirdi. Gereksiz oldugu icin kaldirildi.

        drag = Gtk.GestureDrag()

        def _begin(_g, _x, _y):
            state["w"] = dialog.get_content_width()
            state["h"] = dialog.get_content_height()

        def _update(_g, dx, dy):
            new_w = state["w"] + int(dx) * dx_sign if dx_sign else state["w"]
            new_h = state["h"] + int(dy) * dy_sign if dy_sign else state["h"]
            _apply(new_w, new_h)

        drag.connect("drag-begin", _begin)
        drag.connect("drag-update", _update)
        zone.add_controller(drag)
        return zone

    A = Gtk.Align
    # Kenarlar - ONCE eklenir (z-sirasinda altta kalir, koseler ustte).
    overlay.add_overlay(_make_zone(A.FILL, A.START, "n-resize", 0, -1, True))   # ust
    overlay.add_overlay(_make_zone(A.FILL, A.END, "s-resize", 0, 1, True))     # alt
    overlay.add_overlay(_make_zone(A.START, A.FILL, "w-resize", -1, 0, True))  # sol
    overlay.add_overlay(_make_zone(A.END, A.FILL, "e-resize", 1, 0, True))     # sag
    # Koseler - SONRA eklenir, kenarlarla ustuste bindikleri kosede
    # girdi onceligi onlarda kalsin diye (capraz surukleme guvenilir
    # calissin).
    overlay.add_overlay(_make_zone(A.START, A.START, "nw-resize", -1, -1, False))
    overlay.add_overlay(_make_zone(A.END, A.START, "ne-resize", 1, -1, False))
    overlay.add_overlay(_make_zone(A.START, A.END, "sw-resize", -1, 1, False))
    overlay.add_overlay(_make_zone(A.END, A.END, "se-resize", 1, 1, False))

    dialog.set_child(overlay)
