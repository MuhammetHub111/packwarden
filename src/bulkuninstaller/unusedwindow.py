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

from gi.repository import Adw, GLib, Gtk, Pango

from . import prefs, usage
from .backends.base import format_size
from .settings import SettingsDialog
from .i18n import _
from .removal import RemovalWindow
from .unused import last_used

PRESETS_DAYS = (90, 180, 365)


class _Item:
    """RemovalWindow'un beklediği .pkg arayüzü için ince sarmalayıcı."""

    def __init__(self, pkg):
        self.pkg = pkg


def _pkg_key(pkg) -> str:
    return f"{pkg.source}:{pkg.id}"


def _format_last_used(ts):
    if ts is None:
        return _("Unknown")
    days = max(0, int((time.time() - ts) // 86400))
    if days < 30:
        key = "{days} day ago" if days == 1 else "{days} days ago"
        return _(key).format(days=days)
    # 12 ay (12*30=360 gün) tam bir yıla (365 gün) denk gelmiyor — bu
    # boşlukta (360-364 gün) ay hesabına göre 12, yıl hesabına göre hâlâ
    # 0 çıkıp "0 yıl önce" gibi anlamsız bir şey gösteriyordu. Sınırı ay
    # sayısı yerine doğrudan gün sayısına (365) göre çizmek bunu önlüyor.
    if days < 365:
        months = days // 30
        key = "{months} month ago" if months == 1 else "{months} months ago"
        return _(key).format(months=months)
    years = days // 365
    key = "{years} year ago" if years == 1 else "{years} years ago"
    return _(key).format(years=years)


class UnusedAppsWindow(Adw.Window):
    def __init__(self, main_window):
        super().__init__(
            transient_for=main_window,
            modal=True,
            title=_("Unused programs"),
            default_width=780,
            default_height=920,
        )
        self._main = main_window
        self._data: list[tuple[object, float | None]] = []
        self._checks: list[tuple[Gtk.CheckButton, object]] = []
        # Anahtar -> satır widget'ı: bir uygulamayı yoksayınca tüm listeyi
        # yeniden kurmak yerine sadece o satırı çıkarabilmek için — aksi
        # halde her "X" tıklamasında kaydırma konumu sıfırlanıyor, kullanıcı
        # her seferinde listede yeniden aşağı inmek zorunda kalıyordu.
        self._rows: dict[str, Adw.ActionRow] = {}
        # Taramalar arasında hayatta kalan işaretleme durumu — periyodik
        # yeniden tarama listeyi yeniden kurduğunda kullanıcının az önce
        # işaretlediği kutucuklar sıfırlanmasın diye.
        self._selected_keys: set[str] = set()
        # Son kurulan listenin paket anahtarları — bu değişmediği sürece
        # _rebuild_list() tekrar çağrılmaz (gereksiz yeniden kurma =
        # üzerine gelince yanıp sönme, kaydırmanın sıfırlanması).
        self._last_stale_keys: frozenset[str] | None = None
        # Bir tarama hâlâ çalışırken 2sn'lik tık yenisini başlatmasın —
        # aksi halde çok paketli sistemde thread'ler üst üste birikebilir
        # ve sonuçlar sırasız uygulanabilirdi.
        self._scan_in_progress = False

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
        self._threshold_row.add_css_class("pw-threshold-row")

        self._custom_row = Adw.SpinRow.new_with_range(1, 3650, 1)
        self._custom_row.set_title(_("Custom (days)"))
        self._custom_row.set_value(saved_custom_days)
        self._custom_row.set_visible(saved_preset == 3)
        self._custom_row.connect("notify::value", self._on_threshold_changed)

        # Kullanıcının "bir daha gösterme" dediği paketleri yönetmek için —
        # hiç yoksayılan yoksa tamamen gizli, karışıklık çıkarmasın. Ayrı
        # bir düğme + perde yerine, diğer ayar satırlarıyla AYNI kartın
        # içinde, kendi aç/kapa okunu taşıyan doğal bir Adw.ExpanderRow —
        # sağda boşlukta duran garip bir düğme yerine tam entegre görünüm.
        self._ignored_row = Adw.ExpanderRow(visible=False, expanded=False)
        # css_classes CONSTRUCTOR'da verilirse (yukarıdaki gibi kwarg
        # olarak) ok göstergesinin aç/kapa animasyonu donup kalıyor —
        # eşleşen bir CSS kuralı olmasa BİLE. Canlı bir testle
        # doğrulandı: aynı satır add_css_class() ile SONRADAN
        # etiketlenirse ok normal çalışıyor. Sebep muhtemelen
        # ExpanderRow'un iç kurulumunun (ok animasyon bağlantısı)
        # css_classes constructor kwarg'ından SONRA çalışması.
        self._ignored_row.add_css_class("pw-ignored-expander")
        # Anahtar -> satır widget'ı: bir öğe eklenip çıkarılırken sadece
        # o satırı ekleyip/kaldırmak için — tüm listeyi silip yeniden
        # kurmak, panel açıkken satır sayısı bir an için sıfıra düşünce
        # ExpanderRow'un kendini otomatik kapatmasına (ok animasyonuyla
        # fark edilir şekilde) yol açıyordu.
        self._ignored_rows: dict[str, Adw.ActionRow] = {}
        # Bir satıra ikon eklendiyse bir daha eklenmesin diye (taramanın
        # ilk bitişinde tüm satırlar için tek seferlik ikon tamamlama).
        self._ignored_row_icons: set[str] = set()
        # "−" düğmesinin kendisi — ikon/boyut sonradan (tarama bitince)
        # eklendiğinde düğmeyi çıkarıp boyuttan SONRA yeniden eklemek
        # için (add_suffix hep sona ekler, boyut hep düğmenin solunda
        # kalsın istiyoruz).
        self._ignored_row_buttons: dict[str, Gtk.Button] = {}

        settings_group = self._settings_group = Adw.PreferencesGroup()
        settings_group.add(self._threshold_row)
        settings_group.add(self._custom_row)
        settings_group.add(self._ignored_row)

        self._list_box = Gtk.ListBox(
            css_classes=["boxed-list"],
            selection_mode=Gtk.SelectionMode.NONE,
            valign=Gtk.Align.START,
        )
        self._scan_row = Adw.ActionRow(title=_("Scanning programs…"))
        self._scan_row.add_prefix(Gtk.Spinner(spinning=True))
        self._list_box.append(self._scan_row)

        self._empty_status = Adw.StatusPage(
            icon_name="emblem-ok-symbolic",
            title=_("No unused programs found"),
            description=_("Every scanned program was used within the threshold"),
            vexpand=True,
            visible=False,
            css_classes=["pw-row-bg-fill"],
        )

        content = self._content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12,
            margin_top=12, margin_bottom=12, margin_start=12, margin_end=12,
        )

        # Arka plan algılama kapalıyken sonuçlar eksik kalabilir — bunu
        # engellemek yerine bilgilendiriyoruz, özelliği elinden almıyoruz.
        # Adw.Banner'ın iç düğümleri kendi CSS'imizi geçersiz kılıyordu
        # (sarı hiç görünmüyordu), bu yüzden tam kontrol için düz bir
        # Gtk.Box kullanıyoruz. Görünürlüğü periyodik tarama tıkında da
        # kontrol ediyoruz (bkz. _on_rescan_tick) — kullanıcı Ayarlar'dan
        # açınca kendiliğinden kaybolsun diye.
        # Yan boşluk margin olarak değil CSS padding olarak veriliyor —
        # margin sarı arkaplanın DIŞINDA kalıyor ve alttaki satırlarla
        # (content'in kendi 12px marginiyle hizalı) eşit görünmüyordu.
        self._hint_banner = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=10,
            margin_top=10, margin_bottom=10,
            css_classes=["pw-warning-banner"],
        )
        hint_icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
        hint_icon.set_pixel_size(20)
        hint_icon.set_valign(Gtk.Align.CENTER)
        self._hint_banner.append(hint_icon)
        self._hint_banner.append(Gtk.Label(
            label=_("Enable background detection for more accurate results"),
            wrap=True, xalign=0, hexpand=True, valign=Gtk.Align.CENTER,
        ))
        # Gtk.Button burada da kendi arkaplanını CSS'e rağmen gösterip
        # duruyordu; tıklanabilir düz bir Label + tıklama hareketiyle
        # arkaplan sorunu kökten ortadan kalkıyor.
        hint_settings_label = Gtk.Label(
            label=_("Open Settings"), css_classes=["pw-warning-btn"],
            valign=Gtk.Align.CENTER,
        )
        hint_settings_click = Gtk.GestureClick()
        hint_settings_click.connect(
            "released", lambda *_a: self._on_open_settings()
        )
        hint_settings_label.add_controller(hint_settings_click)
        self._hint_banner.append(hint_settings_label)
        self._hint_banner.set_visible(
            not prefs.get("background_usage_detection")
        )
        content.append(self._hint_banner)

        content.append(settings_group)
        content.append(self._list_box)
        content.append(self._empty_status)
        # maximum_size onceden pencere genisliginden (720) daha buyuktu,
        # o yuzden hicbir sey kisitlamiyordu. Pencereyi 780'e cikarinca
        # (bkz. git log) bu sinir aktif hale geldi ve icerigin iki
        # yanında bosluk birakti (kullanici ekran goruntusuyle gosterdi).
        # Pencereden acikca daha genis bir deger veriyoruz ki tekrar
        # aktif hale gelmesin.
        clamp = Adw.Clamp(maximum_size=900, child=content)
        # pw-row-bg-fill: _snap_scrolled_bottom yarim satiri gizlemek icin
        # bu widget'a margin_bottom veriyor - bu margin alani stilsizdi
        # (duz pencere rengi), bildirimler acikken listenin son satiri ile
        # bildirim alani arasinda ayri, koyu bir "bosluk" gibi goze
        # batiyordu (kullanici ekran goruntusuyle gosterdi). Satirlarla
        # AYNI rengi vererek (bkz. _toast_spacer/_empty_status/_toast_box
        # icin ayni cozum) bu bosluk goze batmiyor.
        self._scrolled = Gtk.ScrolledWindow(
            child=clamp, vexpand=True, css_classes=["pw-row-bg-fill"],
        )
        scrolled = self._scrolled
        # Satir sayisi (tarama/yoksayma ile) veya pencere boyutu degistiginde
        # gorunur listenin alt kenari bir satirin ORTASINDAN gecebiliyordu -
        # "wine"/"Okular" gibi hangi satir en sonda kalirsa o yarim kesilmis
        # gorunuyordu (kullanici birden fazla ekran goruntusuyle gosterdi).
        # Sabit bir pencere yuksekligi bunu KALICI olarak cozemez - ust
        # kisimdaki ayar satirlari (ozellikle Yoksayilanlar basligi) sayiya
        # gore gorunur/gizli oldugundan sinir her an kayabiliyor. Bunun
        # yerine DINAMIK olarak, her seferinde GERCEK satir yuksekligine
        # gore alt kenari en yakin satir sinirina yuvarliyoruz (bkz.
        # _snap_scrolled_bottom) - hem tarama/yoksayma sonrasi (upper
        # degisince) hem pencere yeniden boyutlaninca (page_size degisince)
        # tetiklenir, boylece yarim satir hic gorunmez: ya tam satir ya hic.
        # Bildirimin kendi acilma/kapanma gecisi (200ms kayma) art arda,
        # hizli "page-size" degisimi ureten bir kaynak - _snap_debounce_id
        # gelen tetikleri tek bir GECIKMELI cagriya indirger, yalnizca son
        # degisimden SONRA sessizlik olunca (gecis tamamen bitince) gercekten
        # calisir. Canli testte GORULDU: debounce olmadan (veya hesap her
        # seferinde margin'i once 0'a cekip yeniden olcerse) kendi
        # duzeltmemiz kendi page-size bildirimini tetikleyip sonsuz bir
        # dongu icinde %60+ CPU ile donuyordu - bkz. _snap_scrolled_bottom.
        self._snap_debounce_id = None
        # Kullanici (ya da programatik olarak biz) listeyi en alta kadar
        # kaydirmis mi - bkz. _on_scroll_value_changed/_snap_scrolled_bottom.
        self._pinned_to_bottom = False
        vadjustment = self._scrolled.get_vadjustment()
        vadjustment.connect("notify::value", self._on_scroll_value_changed)
        vadjustment.connect("notify::upper", self._on_scroll_area_changed)
        vadjustment.connect("notify::page-size", self._on_scroll_area_changed)

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

        self._build_toast_widget()

        # Bildirimler artik bir Gtk.Overlay ile listenin UZERINE binen
        # yuzen kutular DEGIL - ToolbarView'in kendi "bottom bar" yigi-
        # nina, icerik (scrolled) ile alt eylem cubugu (action_bar)
        # ARASINA eklenen GERCEK, akis-ici bir satir (bkz. _build_toast_
        # widget). Bu sayede varligi/yuksekligi GTK'nin kendi box
        # duzeni tarafindan hesaplaniyor - "+" tikladiginda alttaki
        # satirlarin +/- dugmelerini kapatan/erisilemez yapan bir yuzen-
        # kutu-uzerinde-liste tasarimi tamamen ortadan kalkiyor, cunku
        # bildirim artik listenin BİR PARCASI degil, ayri, kendi
        # yerini alan bir alan. Bos oldugunda (hic bildirim yokken)
        # dogal yuksekligi 0'a doner (bkz. Gtk.Revealer kullanimi,
        # _show_toast/_remove_toast), yani bos alan da kalmiyor.
        toolbar_view = Adw.ToolbarView(content=scrolled)
        toolbar_view.add_top_bar(header)
        toolbar_view.add_bottom_bar(self._toast_box)
        toolbar_view.add_bottom_bar(action_bar)
        self.set_content(toolbar_view)

        self._update_ignored_button()
        self._scan()
        # Pencere açık kaldığı sürece periyodik olarak yeniden tara —
        # kullanıcı burayı açık bırakıp başka bir uygulamaya geçerse, o
        # kullanım kapatıp yeniden açmaya gerek kalmadan yakalanır.
        self._rescan_source = GLib.timeout_add_seconds(2, self._on_rescan_tick)
        self.connect("close-request", self._on_close_request)

    def _on_rescan_tick(self):
        self._scan()
        self._hint_banner.set_visible(
            not prefs.get("background_usage_detection")
        )
        return GLib.SOURCE_CONTINUE

    # Ayni anda en fazla bu kadar bildirim ust uste yigilabilir — 4.'su
    # gelince en eskisi (kullanicinin "Geri al" sansi olmadan) dusurulur.
    _MAX_TOASTS = 3

    def _build_toast_widget(self):
        # Bildirimler artik Gtk.Overlay uzerinde listenin USTUNE binen
        # yuzen kutular DEGIL - ToolbarView'in bottom-bar yiginina
        # (bkz. __init__) icerik (scrolled) ile alt eylem cubugu
        # (action_bar) ARASINA eklenen GERCEK, akis-ici bir Gtk.Box
        # (self._toast_box). Her bildirim kendi Gtk.Revealer'i icinde bu
        # kutuya append/remove edilir - GTK'nin kendi box duzeni bildirim
        # sayisina gore dogal olarak buyur/kucculur (hic bildirim yokken
        # self._toast_box'un 0 cocugu var, dogal yuksekligi 0 - kalici
        # bos alan yok), elle rank/translateY hesabina hic gerek yok.
        #
        # Eski tasarim (bkz. git log) Gtk.Overlay + rank tabanli CSS
        # transform kullaniyordu: bildirim listenin UZERINE biniyor,
        # +/- dugmelerini hem gorsel hem tiklama olarak erisilemez
        # yapiyordu. Bunu once "rezerv" (listenin altina kalici bos
        # alan ayirma) ile, sonra "kaydirma telafisi" (listeyi bildirim
        # yuksekligi kadar yukari kaydirma) ile cozmeye calisildi - ikisi
        # de bir sekilde calisti ama ikisi de temelde YANLIS katmanda
        # (liste/scroll) bir "listenin UZERINE binen ayri bir katman"
        # sorununu duzeltmeye calisiyordu. Kullanici defalarca ekran
        # goruntusuyle "hala ust uste biniyor" bildirdi (uzun listelerde,
        # art arda/coklu bildirimde, farkli senaryolarda) - kok neden HER
        # ZAMAN ayniydi: bildirim, PENCERE DUZENININ bir parcasi degil,
        # ondan bagimsiz yuzen bir katmandi. Simdi bildirim de dogrudan
        # duzenin (ToolbarView) bir parcasi - liste onun icin GERCEKTEN
        # yer aciyor, cakisma yapisal olarak imkansiz (bkz. GTK'nin kendi
        # box/toolbarview yerlesim mantigi).
        #
        # Adw.Toast/ToastOverlay KULLANILMIYOR — libadwaita'nin kendi
        # kayma animasyonu private/ic bir efekt, disaridan sadece GTK'nin
        # genel "animasyonlari etkinlestir" ayariyla (uygulamadaki HER
        # animasyonu etkiler, ör. Yoksayilanlar perdesi) kapatilabiliyor.
        # Kendi basit bildirim widget'imizi yazmak, konum/davranis
        # uzerinde tam kontrol sagliyor.
        self._toasts = []  # en eski once: [{"revealer","timeout_id","callback"}, ...]
        # pw-row-bg-fill: bu kutunun kendi arkaplani stilsizdi (duz
        # pencere rengi/varsayilan), pill'lerin kendi CSS margin'i
        # (bkz. .pw-toast, "margin: 4px 12px") disinda kalan kucuk
        # seritlerde bu duz renk gorunuyordu - tam da listenin son
        # satirinin ayirici cizgisinin bitip bildirim alaninin basladigi
        # sinirda, ekran goruntusu + piksel olcumuyle dogrulandi (satir
        # rengi rgb(52,52,55) -> bu kutunun stilsiz rengi rgb(32,32,36)
        # -> pill rgb(46,46,50): ayirici cizginin "ic" tarafi kayboluyor,
        # dogrudan bildirimin kendi renginmis gibi goruniyordu). Satirlarla
        # AYNI hesaplanmis rengi vererek (bkz. _toast_spacer/_empty_status
        # icin ayni cozum) bu sinirdaki ayirici cizgi normal/tam gorunuyor.
        self._toast_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, css_classes=["pw-row-bg-fill"],
        )

        css = b"""
        .pw-toast {
            background-color: rgba(46, 46, 50, 0.92);
            color: white;
            padding: 6px 6px 6px 16px;
            border-radius: 9999px;
            box-shadow: 0 3px 10px rgba(0, 0, 0, 0.35);
            /* Ust/alt bosluk (komsu bildirimler + icerik/eylem cubugu
               arasi) VE yan bosluk (icerigin kendi 12px kenar payiyla
               hizali) burada, PILL'in kendi CSS'inde - self._toast_box'un
               UZERINDE degil. Boylece 0 bildirim varken kutunun 0 cocugu
               = 0 gorunur yukseklik + 0 margin (bos alan kalmiyor); her
               bildirim VARKEN kendi boslugunu kendi tasiyor.
            */
            margin: 4px 12px;
        }
        /* libadwaita bu satira farkli bir kart rengi, yuvarlak kose ve
           ince bir kenarlik veriyor (acilan bir alt-kart gibi ayirt
           etsin diye). Once @card_bg_color'i (yarim seffaf beyaz bir
           katman, duz bir renk DEGIL) dogrudan background-color olarak
           denedik: ekran goruntusu + piksel olcumuyle dogrulandi ki
           normal satirlarin GERCEK rengi window_bg_color + %8 beyaz
           karisimiymis (olcum: rgb(52,52,55)); @card_bg_color'i hem
           bu satira hem ic satirlarina ayri ayri uygulamak bu karisimi
           IKI KEZ ust uste bindiriyordu (olcum: rgb(64,64,66) -
           belirgin sekilde daha acik/farkli). mix() ile TEK, DUZ/OPAK
           bir sonuc renk hesaplayip HER katmana (kutu, liste, revealer,
           satir) ayni sekilde uyguluyoruz - opak renk ust uste
           bindirmede karismiyor, kenarlardaki ince farkli renkli
           seritler de boylece kapaniyor.
        */
        .pw-ignored-expander,
        .pw-ignored-expander box,
        .pw-ignored-expander list,
        .pw-ignored-expander revealer,
        .pw-ignored-expander row {
            background-color: mix(@window_bg_color, white, 0.08);
            border-radius: 0;
            box-shadow: none;
            border: none;
            outline: none;
            margin: 0;
        }
        /* Bos-durum sayfasinin (_empty_status, hepsi yoksayilinca
           "Kullanilmayan yok" karti) duz/stilsiz DUZ arkaplani
           (pencerenin kendi rengi) gosterip satirlarin (boxed-list, tek
           katman @card_bg_color) arkaplanindan farkli durdugu, "ayri bir
           kutu" gibi goze batiyordu. @card_bg_color'in tek katmani,
           olculen window_bg_color+%8 beyaz karisimiyla matematiksel
           olarak esdeger (bkz. yukaridaki uzun aciklama) - o yuzden AYNI
           mix() formulunu kullanmak yeterli.
        */
        .pw-row-bg-fill {
            background-color: mix(@window_bg_color, white, 0.08);
        }
        /* Ok gostergesi kendi ayri "hit area" boslugu yuzunden +/-
           dugmelerinin hizasindan saga kayiyor. margin-end negatif
           degerle denendi ama bir noktadan sonra (~-33px) kilitleniyor
           (kucuk bir tiklama alani alt siniri var gibi); transform ise
           boyle bir sinira carpmiyor.
           AYRICA: "nested" liste (ExpanderRow'un ic satirlari) ile ana
           listenin ("boxed-list") kendi sag bosluk payi farkli;
           piksel olcumuyle dogrulandi: eksi dugmesi 670, arti dugmesi
           658 (12px fark). Hem eksi dugmesini hem oku, arti dugmesiyle
           AYNI x konumuna (658) gelecek sekilde kaydiriyoruz.
        */
        .pw-ignored-expander .nested button.destructive-action {
            transform: translateX(-12px);
        }
        .pw-ignored-expander .expander-row-arrow {
            transform: translateX(-21px);
        }
        /* Panel ilk kez gizliden gorunure gectiginde (yok sayilan sayisi
           0'dan 1'e ciktiginda) ok ikonu, hic tiklanmamis olsa bile bir
           kere donuyordu. Sebebi: libadwaita'nin oku -gtk-icon-transform
           ile 200ms'de donduren CSS transition'i, widget ilk realize
           olurken bile (gercek bir :checked degisimi olmadan) bir kere
           tetikleniyor. Bu sinif set_visible(True)'dan once eklenip
           hemen sonra kaldirilarak SADECE o ilk kareyi (transition'siz)
           gecistiriyor; sonraki gercek ac/kapa tiklamalarinda animasyon
           normal kaliyor.
        */
        .pw-ignored-expander.pw-suppress-arrow-anim .expander-row-arrow {
            transition: none;
        }
        /* Gorunmez onay kutusu ikon ile baslik arasina gercek onay
           kutusuyla AYNI genislikte bosluk koyacakti, ama "nested"
           liste bunu gercek listeden daha genis uyguluyor (olculdu:
           14px fazla). Kapatiyoruz.
        */
        .pw-ignore-title-spacer {
            margin-right: 8px;
        }
        /* ExpanderRow'un basligi ("Yoksayilanlar"), ust satirlarin
           ("Ozel (gun)" vb.) basligindan ~11px sola kayik duruyordu
           (gercek pencerede ekran goruntusu + piksel olcumuyle
           dogrulandi: 85 vs 95-96). Ayni noktaya getiriyoruz.
        */
        .pw-ignored-expander .header .title {
            margin-left: 5px;
        }
        /* Yoksayilanlar panelindeki satirlarin ikonu (alacritty vb.)
           biraz saga kaydirilsin istendi. Bu ikon add_prefix() ile
           elle eklendigi icin ActionRow'un kendi ".icon" sinifini
           TASIMIYOR (o sinif sadece set_icon_name() kullanildiginda
           devreye giren, bizim kullanmadigimiz baska bir ic Image'a
           ait) - kendi ozel sinifimizi (pw-ignore-row-icon) ekleyip
           onunla hedefliyoruz.
        */
        /* YANLIS TESHIS DUZELTMESI: "yapisal kayma yok" dedik ve sifira
           aldik, ama kullanici bunun "en dibe kaymis" gibi kotu
           gorundugunu bildirdi - dogruydu. Kesin kanit icin AYNI pkg'nin
           AYNI ikonunu hem gercek nested (yoksayilan) satirda hem de
           gecici bir boxed-list satirinda olusturup compute_bounds ile
           (burada transform yok, guvenilir) karsilastirdik: nested
           x=57, boxed-list x=69 - TAM 12px'lik gercek bir yapisal fark
           var (basliktaki 4px, eksi dugmesindeki 12px ile ayni aile).
           Dogru deger buydu; kullanicinin nudge'larinin toplami da
           tesadufen buna yakinmis.
        */
        /* DUZELTME: "transform: translateX()" gorseli kaydiriyor ama
           widget'in KENDI ayrilmis kutusunu (ve kirpma sinirini)
           BUYUTMUYOR - onceki bir commit bunu "bu eleman translateX()'e
           dogrusal olmayan tepki veriyor, kirpma/allocation kisiti"
           diye zaten tespit etmisti ama sorunu (kirpilma) cozmeden,
           sadece az fark edilecek kucuk bir deger secerek gecistirmisti.
           margin-left GERCEKTEN yerlesime katilir - kutuyu genisletir,
           kirpma sinirini de birlikte tasir, tasma/kirpilma olmaz. */
        .pw-ignore-row-icon {
            margin-left: 12px;
        }
        /* Yoksayilanlar bolumundeki boyut yazisi biraz sola kaydirilsin
           istendi.
        */
        .pw-ignore-row-size {
            transform: translateX(-12px);
        }
        /* "Su suredir kullanilmayanlar" (S harfi), "Ozel (gun)" ve
           "Yoksayilanlar" (O/Y harfleri) ile ayni x'te olcumustu ama
           1px fark vardi - harf sekli. Kullanici gozle fark ediyor,
           kapatiyoruz.
        */
        .pw-threshold-row .title {
            transform: translateX(-0.5px);
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _show_toast(self, title, button_label=None, on_action=None, timeout=5):
        # Zaten 3 bildirim yigilmisken 4.'su gelirse, en eskisini
        # (listenin basindaki, kullanici onu okuma/geri alma sansi
        # olmadan) sessizce dusuruyoruz — sinirsiz yigilmasin.
        if len(self._toasts) >= self._MAX_TOASTS:
            self._discard_toast(self._toasts[0])

        label = Gtk.Label(
            label=title, xalign=0, hexpand=True,
            ellipsize=Pango.EllipsizeMode.END,
        )
        action_button = Gtk.Button(css_classes=["flat"])
        close_button = Gtk.Button(
            icon_name="window-close-symbolic",
            css_classes=["flat", "circular"],
            tooltip_text=_("Close"),
        )
        # Genislik: SABIT bir piksel degeri (640) once denendi ama pencere
        # o kadar genis olmayabiliyor - bildirim diyalogun kendi sinirini
        # tasip arkadaki ana pencereye biniyordu (kullanici ekran
        # goruntusuyle gosterdi). hexpand=True kullaniyoruz: bildirim HER
        # ZAMAN o an hangi pencerede/genislikte gosteriliyorsa onun
        # genisligine gore olculur - asla tasamaz, ama yine de her
        # bildirim ayni (o pencereye gore standart) genislikte olur,
        # metnin uzunlugu genisligi degistirmez (bkz. ellipsize).
        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
            css_classes=["pw-toast"], hexpand=True,
        )
        box.append(label)
        box.append(action_button)
        box.append(close_button)

        # Her bildirim kendi Gtk.Revealer'inde - bkz. _build_toast_widget
        # icin yukaridaki genel aciklama. reveal_child=False ile baslar,
        # bir kare sonra True'ya cekilir ki gercek "acilma" gecisi oynasin
        # (aninda True olsaydi hic gecis olmadan dogrudan acik belirirdi).
        revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
            transition_duration=200,
            child=box,
        )

        entry = {
            "revealer": revealer, "timeout_id": None, "callback": on_action,
        }
        if button_label:
            action_button.set_label(button_label)
            action_button.set_visible(True)
        else:
            action_button.set_visible(False)
        action_button.connect(
            "clicked", lambda _b, e=entry: self._on_toast_action_clicked(e)
        )
        close_button.connect(
            "clicked", lambda _b, e=entry: self._remove_toast(e)
        )

        # append(): yeni bildirim, dikey kutunun EN SONUNA (gorsel olarak
        # en ALTTA, eylem cubuguna en yakin) eklenir - onceki bildirimler
        # oldugu yerde kalir, elle bir konum/rank hesabina gerek yok.
        self._toast_box.append(revealer)
        self._toasts.append(entry)

        def _reveal(r=revealer):
            r.set_reveal_child(True)
            return GLib.SOURCE_REMOVE
        GLib.idle_add(_reveal)

        if timeout:
            entry["timeout_id"] = GLib.timeout_add_seconds(
                timeout, lambda e=entry: self._on_toast_timeout(e)
            )

    def _on_toast_timeout(self, entry):
        entry["timeout_id"] = None
        self._remove_toast(entry)
        return GLib.SOURCE_REMOVE

    def _discard_toast(self, entry):
        # _remove_toast'un ANIMASYONSUZ hali - sadece kaydi ve widget'i
        # siler. SADECE _show_toast'un 4. bildirim gelince en eskiyi
        # dusurdugu yerde kullanilir (kullaniciya gecis/kapanma
        # animasyonu gostermeye deger bir eylem degil, arka planda
        # sessizce kapaniyor).
        if entry["timeout_id"] is not None:
            GLib.source_remove(entry["timeout_id"])
            entry["timeout_id"] = None
        if entry in self._toasts:
            self._toasts.remove(entry)
        revealer = entry["revealer"]
        if revealer.get_parent() is self._toast_box:
            self._toast_box.remove(revealer)

    def _remove_toast(self, entry, animate=True):
        if entry not in self._toasts:
            return
        if entry["timeout_id"] is not None:
            GLib.source_remove(entry["timeout_id"])
            entry["timeout_id"] = None
        self._toasts.remove(entry)
        revealer = entry["revealer"]
        if not animate:
            self._toast_box.remove(revealer)
            return
        # Gtk.Revealer kapanma gecisini kendi yonetiyor (SLIDE_DOWN'i
        # tersine oynatir, kutunun toplam yuksekligi de bununla birlikte
        # dogal olarak kucculur - elle bir "digerlerini kaydir" hesabina
        # hic gerek yok). Gecis bitince (child-revealed False olunca)
        # widget'i GERCEKTEN kutudan cikariyoruz.
        def _on_revealed(r, _pspec):
            if not r.get_child_revealed():
                if r.get_parent() is self._toast_box:
                    self._toast_box.remove(r)
        revealer.connect("notify::child-revealed", _on_revealed)
        revealer.set_reveal_child(False)

    def _on_toast_action_clicked(self, entry):
        callback = entry["callback"]
        self._remove_toast(entry)
        if callback is not None:
            callback()

    # Bildirimin kendi kayma gecisinden (200ms) daha uzun - debounce
    # penceresi, gecis TAMAMEN bitip sessizlik olana kadar gercek
    # hesabi surekli erteler.
    _SNAP_DEBOUNCE_MS = 250

    def _on_scroll_area_changed(self, *_args):
        # "upper" (icerik degisti - tarama/yoksayma), "page_size"
        # (pencere yeniden boyutlandi) VEYA bildirim alani buyuyup/
        # kucultugunde tetiklenir. Kendi margin_bottom degisikligimiz de
        # page_size'i degistirir (bkz. _snap_scrolled_bottom) ve bu
        # sinyali YENIDEN tetikler - ama asagidaki hesap idempotent
        # oldugundan (degisiklik gerekmiyorsa margin'e hic dokunmaz) bu
        # kendiliginden 1-2 turda sonlanir. Debounce ise bildirimin
        # animasyonu gibi art arda hizli ates eden AYRI/HARICI bir
        # kaynagin her karesinde tekrar hesap yapmak yerine, gecis
        # tamamen durulunca TEK SEFER hesap yapmasini sagliyor.
        if self._snap_debounce_id is not None:
            GLib.source_remove(self._snap_debounce_id)
        self._snap_debounce_id = GLib.timeout_add(
            self._SNAP_DEBOUNCE_MS, self._on_snap_debounce_fired
        )

    def _on_snap_debounce_fired(self):
        self._snap_debounce_id = None
        self._snap_scrolled_bottom()
        return GLib.SOURCE_REMOVE

    def _on_scroll_value_changed(self, vadjustment, _pspec):
        # Kullanicinin (fare tekeri/kaydirma cubugu ile) ya da bizim
        # (_snap_scrolled_bottom'daki yeniden-sabitleme) az once nereye
        # kaydirdigimizi ANLIK olarak izliyor - "su an listenin gorunen en
        # altina kadar kaydirilmis mi" bilgisi, bir SONRAKI upper/page-size
        # degisiminde (ör. bir bildirim daha eklenip page_size kuculunce)
        # KORUNMASI gereken davranisi (en altta kalmaya devam et) belirlemek
        # icin lazim - o degisim aninda deger ZATEN yeni haline gecmis
        # olacagi icin "degismeden ONCE en altta miydi" bilgisini simdiden
        # saklamamiz gerekiyor.
        page_size = vadjustment.get_page_size()
        upper = vadjustment.get_upper()
        value = vadjustment.get_value()
        self._pinned_to_bottom = value >= upper - page_size - 2
        # ARALIKLI (intermittent) bug'in gercek kaynagi (canli stres
        # testiyle bulundu): kullanici kaydirma cubugunu/fare tekerini
        # kullanip listeyi elle en alta kaydirdiginda SADECE bu sinyal
        # ateşlenir - "upper"/"page-size" DEGISMEZ (icerik/bildirim sayisi
        # ayni kalir), o yuzden _on_scroll_area_changed hic tetiklenmez ve
        # _snap_scrolled_bottom hic CAGRILMAZ. Kullanici en alta kaydirmadan
        # ONCE (ör. listeyi ustten goruntulerken) hesaplanmis eski bir
        # margin varsa, bu deger duzeltilmeden KALICI olarak asili kaliyor
        # - ta ki BASKA bir sebeple (ör. yeni bir yoksayma) upper/page-size
        # tekrar degisene kadar. Iste bu yuzden sorun "aralikli" gorunuyordu:
        # kullanici en alta kaydirip HEMEN ekran goruntusu alirsa (araya
        # baska bir islem girmeden) bosluk hala oradaydi; birkac yoksayma
        # daha yaparsa o islemler "yan etki" olarak duzeltiyordu. Duzeltme:
        # simdi en alta sabitlenmis oldugumuzu ANLADIGIMIZ (ya da hala
        # oyle oldugunu dogruladigimiz) HER an, asili kalan margin'i hemen
        # sifirla - baska bir tetikleyici beklemeye gerek yok.
        if self._pinned_to_bottom and self._scrolled.get_margin_bottom() != 0:
            self._scrolled.set_margin_bottom(0)

    def _snap_scrolled_bottom(self):
        vadjustment = self._scrolled.get_vadjustment()
        # Icerik (ayar satirlari + liste) kaydirma gerektirmeden viewport'a
        # ZATEN sigiyorsa (upper, su anki margin'i geri eklenmis "dogal"
        # page_size'i asmiyorsa) kirpilma riski hic yok - gecmise/kaydirma
        # gecmisine bakmaya bile gerek yok, HER ZAMAN margin=0 dogru
        # sonuctur. Bu kontrol asagidaki "en alta sabitleme" mantigindan
        # ONCE ve ONDAN BAGIMSIZ calisir - cunku _pinned_to_bottom bayragi
        # sadece notify::value fiilen tetiklenince guncellenir, deger hic
        # degismeden (value=0'da) kalirsa (ör. pencere yeni acildi, kullanici
        # henuz kaydirmadi) bayrak henuz doğru kurulmamis olabilir.
        current_margin = self._scrolled.get_margin_bottom()
        natural_page_size = vadjustment.get_page_size() + current_margin
        if vadjustment.get_upper() <= natural_page_size:
            if current_margin != 0:
                self._scrolled.set_margin_bottom(0)
            return
        # Liste EN ALTA kadar kaydirilmis durumdaysa (bildirimlerin hemen
        # ustunde goze batan "gereksiz bosluk" raporu HER ZAMAN bu durumda
        # ortaya cikiyordu - kullanici ekran goruntusuyle dogruladi):
        # icerik orada zaten HER ZAMAN tam bir satirin sonunda biter -
        # satirlar bosluksuz art arda dizildigi icin listenin dogal sonu
        # (upper) matematiksel olarak HER ZAMAN bir satir sinirina denk
        # gelir, margin'e hic gerek yok. Bildirim sayisi degistiginde
        # (page_size kuculup/buyudukce) tek yapilmasi gereken, kaydirma
        # degerini YENI "en alt" konumuna guncellemek - boylece bildirimler
        # eklenip/kaldirilirken liste otomatik olarak onlara "yapisik"
        # kaliyor, ne bosluk ne yarim satir.
        #
        # ONCEKI TASARIM (canli testle bulunan, kaldirilan bug): bu durumda
        # da satir-yuksekligine gore modulo alinarak bir margin
        # hesaplaniyordu (deger SADECE "value=0'dan/pencerenin USTUNDEN
        # bakilsaydi" senaryosu icin dogruydu, gercek kaydirma degerini hic
        # hesaba katmiyordu) - kullanicinin EN ALTA kaydirdigi, tipik
        # senaryoda bu, listenin son satiriyla bildirimler arasinda 34-48px
        # arasi, goze batan, tamamen gereksiz bir bosluk birakiyordu (canli
        # testle dogrulandi, ekran goruntusuyle raporlandi).
        if self._pinned_to_bottom:
            if current_margin != 0:
                self._scrolled.set_margin_bottom(0)
            new_value = vadjustment.get_upper() - vadjustment.get_page_size()
            if new_value < 0:
                new_value = 0
            if abs(vadjustment.get_value() - new_value) > 0.5:
                vadjustment.set_value(new_value)
            return
        # Kullanici en altta DEGILSE, gorunur alanin alt kenarini GERCEK
        # satir yuksekligine yuvarlayarak iki satir arasindaki dogal
        # sinira getiriyoruz - hangi satir "en sonda" kalirsa kalsin YARIM
        # gorunmesin.
        row = self._list_box.get_row_at_index(0)
        row_height = row.get_allocated_height() if row is not None else 0
        if row_height <= 0:
            return
        # page_size, scrolled goruntulenen TUM icerigi (hint_banner +
        # settings_group + list_box + empty_status) kapsar - satirlar
        # SADECE bunun bir kismini kaplar, oncesinde ayar satirlari (bkz.
        # settings_group) var. O yuzden dogrudan page_size'a modulo almak
        # YANLIS cikti (canli testle bulundu: 15 tam satirlik yer varmis
        # gibi hesaplandi ama gercekte ~3 satir zaten ayar bolumune
        # gidiyordu). Once listenin BASLADIGI noktaya kadar olan "ust
        # kisim" yuksekligini (spacing/margin dahil) cikarip GERI KALANI
        # satir sayisina yuvarliyoruz.
        spacing = self._content.get_spacing()
        chrome = self._content.get_margin_top() + spacing
        chrome += self._settings_group.get_allocated_height()
        if self._hint_banner.get_visible():
            chrome += self._hint_banner.get_allocated_height() + spacing
        usable_for_rows = natural_page_size - chrome
        leftover = int(usable_for_rows) % row_height if usable_for_rows > 0 else 0
        value = vadjustment.get_value()
        # EN USTE YAKINSA (value < row_height - pencere yeni acilmis,
        # kullanici henuz hic kaydirmamis, YA DA az once BURADAN bir
        # kaydirma-duzeltmesi uyguladik; ekran goruntuleriyle DOGRULANDI:
        # "Yok Sayilanlar"a bir program eklendiginde bildirimin USTUNDE
        # goze batan, uzun bir bos dikdortgen kendini tekrar tekrar
        # gosteriyordu, cunku pencere HER acildiginda deger 0'da
        # basliyor) margin YERINE kaydirma DEGERINI leftover'a esitliyoruz:
        # viewport kendi DOGAL (kucultulmemis) boyutunda kaliyor, hicbir
        # yerde bos bir "margin seridi" olusmuyor - sadece en ustteki
        # ayar satirlarinin (chrome) bir kac pikselik UST kismi ekran
        # disina kayiyor, bu da bildirimin hemen yaninda beliren buyuk bos
        # alandan kat kat az goze batiyor. Kirpilma korumasi aynen
        # korunuyor: alt kenar hala tam bir satir sinirinda bitiyor -
        # sadece bunu "viewport'u kucultmek" yerine "biraz daha asagi
        # kaydirmak" ile sagliyoruz. Esik deger sabit kucuk bir epsilon
        # DEGIL, row_height - kendi onceki duzeltmemiz value'yu en fazla
        # row_height-1 kadar ileri itmis olabilir, bir SONRAKI cagrida da
        # bunu hala "ustte" olarak tanimamiz gerekiyor (yoksa deger
        # degisince asagidaki "orta konum" koluna dusup margin'e geri
        # donerdik).
        if value < row_height:
            if current_margin != 0:
                self._scrolled.set_margin_bottom(0)
            if value != leftover:
                vadjustment.set_value(leftover)
            return
        # Gercekten ORTA bir konuma kaydirilmissa (kullanici o an aktif
        # olarak listeyi geziyor) kaydirma degerini degistirmek onun
        # konumunu "kaçirmak" olurdu - bu nadir durumda eski margin
        # mantigi degismeden kaliyor.
        new_margin = leftover
        if new_margin != current_margin:
            self._scrolled.set_margin_bottom(new_margin)

    def _on_open_settings(self, *_args):
        # Bu pencereyi (ve altındaki ana pencereyi) aynı anda görünür
        # tutmak iç içe/karışık istiflemeye yol açıyordu. Bunun yerine bu
        # pencereyi geçici olarak gizleyip Ayarlar'ı tek başına, ana
        # pencerenin üzerinde açıyoruz; Ayarlar kapanınca geri geliyor.
        # set_visible(False) kullanıyoruz (close() değil) — pencere ve
        # içindeki durum (işaretli kutular, kaydırma konumu, periyodik
        # tarama) korunuyor, sadece görünürlük değişiyor.
        self.set_visible(False)
        dialog = SettingsDialog(self._main.get_application())
        dialog.connect("closed", lambda *_a: self.set_visible(True))
        dialog.present(self._main)

    def _on_close_request(self, *_args):
        if self._rescan_source is not None:
            GLib.source_remove(self._rescan_source)
            self._rescan_source = None
        return False

    # ---------------- Tarama ----------------

    def _scan(self):
        # Önceki tarama hâlâ sürüyorsa yenisini başlatma — bir sonraki
        # 2sn'lik tık zaten tekrar deneyecek. Bu olmadan çok paketli bir
        # sistemde art arda thread'ler birikip sonuçlar sırasız
        # uygulanabiliyordu.
        if self._scan_in_progress:
            return
        self._scan_in_progress = True

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
            #
            # try/finally: bu blokta beklenmeyen bir istisna oluşursa bile
            # _on_scanned yine de (GLib.idle_add ile, ana thread'de)
            # çağrılmalı — aksi halde _scan_in_progress hiç sıfırlanmaz ve
            # sonraki tüm periyodik taramalar sessizce hiçbir şey yapmadan
            # döner. data=None, taramanın başarısız olduğunu ve mevcut
            # verinin korunması gerektiğini _on_scanned'e bildirir.
            data = None
            try:
                usage.scan_and_record(packages, launcher_map)
                # usage.json'ı paket başına değil, tarama başına bir kez
                # yükle — N paket için N kez dosya okuyup ayrıştırmak yerine.
                seen_map = usage.get_seen_map()
                data = [(pkg, last_used(pkg, seen_map)) for pkg in packages]
            except Exception:
                pass  # pencereyi çökertme; mevcut veri korunur
            finally:
                GLib.idle_add(self._on_scanned, data)

        threading.Thread(target=worker, daemon=True).start()

    def _on_scanned(self, data):
        self._scan_in_progress = False
        if data is None:
            return  # tarama başarısız oldu; mevcut veri/liste korunur
        # İlk gerçek tarama sonucu geldiğinde Yoksayılanlar panelini de
        # tazele — __init__'te _update_ignored_button() ilk kez tarama
        # bitmeden (self._data hâlâ boşken) çağrılıyor, o yüzden önceki
        # oturumdan kalma yoksayılan öğeler ikonsuz kalıyordu ve bir daha
        # hiç düzelmiyordu (bu metod başka hiçbir yerden tetiklenmiyordu).
        had_data = bool(self._data)
        self._data = data
        if not had_data and data:
            self._update_ignored_button()
        # Liste üyeliği (hangi paketler eşiğin üzerinde) değişmediyse
        # yeniden kurma — sadece metin/zaman damgası birkaç saniye
        # ilerlemiş olabilir, bu görsel olarak önemli değil ve sürekli
        # yeniden kurmak seçimleri/kaydırmayı bozuyordu.
        new_stale_keys = frozenset(pkg_key for pkg_key, _pkg, _ts in self._stale_packages())
        if new_stale_keys != self._last_stale_keys:
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

    def _stale_packages(self):
        """(anahtar, pkg, ts) üçlüleri — eşiği aşmış VE kullanıcının
        "bir daha gösterme" demediği paketler, en eskiden yeniye sıralı.

        "Bilinmiyor" (ts is None) burada asla listelenmez: yeni kurulmuş
        ya da az önce güncellenmiş bir uygulamanın henüz hiç kalıntı
        klasörü olmayabilir, bu da onu gerçekten eski/kullanılmayan bir
        uygulamadan ayırt edilemez kılar. Sadece GERÇEKTEN eski olduğu
        bilinen (kesin zaman damgalı) uygulamalar gösterilir."""
        ignored = prefs.get("ignored_unused_programs") or {}
        cutoff = time.time() - self._threshold_days * 86400
        stale = [
            (_pkg_key(pkg), pkg, ts) for pkg, ts in self._data
            if ts is not None and ts <= cutoff and _pkg_key(pkg) not in ignored
        ]
        stale.sort(key=lambda triple: triple[2])
        return stale

    def _rebuild_list(self):
        child = self._list_box.get_first_child()
        while child is not None:
            self._list_box.remove(child)
            child = self._list_box.get_first_child()
        self._checks.clear()
        self._rows.clear()

        stale = self._stale_packages()

        self._last_stale_keys = frozenset(key for key, _pkg, _ts in stale)
        # Artık listede olmayan paketlerin işaretlerini biriktirmeye
        # gerek yok — set büyümesin diye mevcut listeyle kesişimine indir.
        self._selected_keys &= self._last_stale_keys

        if not stale:
            self._list_box.set_visible(False)
            self._empty_status.set_visible(True)
            self._select_all.set_sensitive(False)
            self._update_selection()
            return

        self._list_box.set_visible(True)
        self._empty_status.set_visible(False)
        for key, pkg, ts in stale:
            check = Gtk.CheckButton(
                valign=Gtk.Align.CENTER, active=key in self._selected_keys,
            )
            check.connect("toggled", self._on_row_toggled, key)
            badge = f"{pkg.source} · {_format_last_used(ts)}"
            if pkg.source.startswith("heroic-"):
                badge += " (" + _("approximate") + ")"
            row = Adw.ActionRow(
                title=pkg.name, subtitle=badge, activatable_widget=check,
            )
            row.add_prefix(check)
            row.add_prefix(self._icon_for(pkg))
            size_label = Gtk.Label(
                # format_size(0) boş dize döner (bilinmeyen boyut) - boş
                # bırakınca satır bozuk görünüyordu.
                label=format_size(pkg.size) or "—",
                css_classes=["numeric", "dim-label"],
                valign=Gtk.Align.CENTER,
            )
            row.add_suffix(size_label)
            # İlk bakışta fark edilsin diye sağ-tık'a gizlenmek yerine her
            # satırda görünür bir düğme — kullanıcı ara sıra açtığı ama
            # yine de kalmasını istediği bir uygulamayı (ör. nadiren
            # kullandığı bir oyun) süresiz olarak listeden çıkarabilsin.
            # Yoksayma listesine EKLEMEK bir "artı" eylemi (yeşil), oradan
            # ÇIKARMAK ise bir "eksi" eylemi (kırmızı, bkz. _update_ignored_
            # button) — yaygın filtre listesi kalıbı, bkz. kullanıcıyla
            # konuşulan artı-yeşil/eksi-kırmızı sistemi.
            ignore_btn = Gtk.Button(
                icon_name="list-add-symbolic", valign=Gtk.Align.CENTER,
                css_classes=["flat", "circular", "success"],
                tooltip_text=_("Don't show again"),
            )
            ignore_btn.connect("clicked", self._on_ignore_button_clicked, pkg)
            row.add_suffix(ignore_btn)
            self._list_box.append(row)
            self._checks.append((check, pkg))
            self._rows[key] = row

        self._select_all.set_sensitive(True)
        self._update_selection()

    def _remove_row(self, key):
        """Tüm listeyi yeniden kurmadan tek bir satırı çıkarır — bkz.
        _on_ignore_button_clicked. Tam yeniden kurma kaydırma konumunu
        sıfırlıyordu, kullanıcı her "X" tıklamasında listede yeniden
        aşağı inmek zorunda kalıyordu."""
        row = self._rows.pop(key, None)
        if row is not None:
            self._list_box.remove(row)
        self._checks = [(c, pkg) for c, pkg in self._checks if _pkg_key(pkg) != key]
        self._last_stale_keys = self._last_stale_keys - {key} if self._last_stale_keys else None

        if not self._rows:
            self._list_box.set_visible(False)
            self._empty_status.set_visible(True)
            self._select_all.set_sensitive(False)
        self._update_selection()

    def _on_row_toggled(self, check, key):
        if check.get_active():
            self._selected_keys.add(key)
        else:
            self._selected_keys.discard(key)
        self._update_selection()

    # ---------------- Yoksayma ----------------

    def _on_ignore_button_clicked(self, _button, pkg):
        key = _pkg_key(pkg)
        ignored = dict(prefs.get("ignored_unused_programs") or {})
        ignored[key] = pkg.name
        prefs.set("ignored_unused_programs", ignored)
        self._selected_keys.discard(key)
        self._update_ignored_button()
        self._remove_row(key)
        # Görünür, tek tıkla çalışan bir düğme yanlışlıkla da basılabilir —
        # geri alma bildirimi bunu ucuzlaştırıyor, ayrıca yönetim panelini
        # hiç bilmeyen biri için de "bunu geri getirebilirim" ipucu veriyor.
        # _show_toast her çağrıda YENİ bir bildirim ekler, öncekini
        # değiştirmez (bkz. _build_toast_widget) — art arda birkaç
        # uygulamayı hızlıca işaretlerse her biri kendi "Geri al"ıyla
        # üst üste yığılır, ilki kaybolmaz.
        self._show_toast(
            _("{name} won't be shown in the Unused programs list again").format(name=pkg.name),
            button_label=_("Undo"),
            on_action=lambda k=key: self._unignore(k),
            timeout=7,
        )

    def _update_ignored_button(self):
        ignored = prefs.get("ignored_unused_programs") or {}
        # Panel boşalıp gizlenirken KAPALI duruma alınıyor (kullanıcı
        # elle açmış olsa bile) — "expanded" görünürlükten bağımsız bir
        # özellik, panel gizliyken bile eski (açık) değerini taşıyordu.
        # Sırayla: önce gizliyken kapat, SONRA görünür yap — tersi
        # sıralamada (önce görünür, sonra kapat) panel bir kare için eski
        # "açık" haliyle çizilip aynı anda kapanma animasyonuna giriyordu;
        # kullanıcı buna "artıya basınca sekme açılıp kapanıyor gibi" dedi.
        # Kapanırken zaten kapatıldığı için yeniden açıldığında da hep
        # kapalı başlıyor, elle açmak isteyip istemediğine kullanıcı karar
        # veriyor.
        if not ignored:
            self._ignored_row.set_expanded(False)
        now_visible = bool(ignored)
        first_reveal = now_visible and not self._ignored_row.get_visible()
        if first_reveal:
            self._ignored_row.add_css_class("pw-suppress-arrow-anim")
        self._ignored_row.set_visible(now_visible)
        if first_reveal:
            def _restore_arrow_anim():
                self._ignored_row.remove_css_class("pw-suppress-arrow-anim")
                return GLib.SOURCE_REMOVE
            GLib.timeout_add(100, _restore_arrow_anim)
        self._ignored_row.set_title(_("Ignored ({count})").format(count=len(ignored)))

        # ÖNEMLİ: tüm satırları silip yeniden kurmuyoruz. Bunu denedik —
        # ExpanderRow'un satır sayısı silme ile yeniden ekleme arasında
        # bir an için sıfıra düşüyor, ve panel AÇIKKEN bu, ExpanderRow'un
        # kendini otomatik kapatmasına yol açıyordu (ok, kapanma
        # animasyonuyla dönüyordu — kullanıcının "artıya basınca ok
        # dönüyor" dediği tam olarak buydu). Sadece değişen satırı
        # ekleyip/çıkararak diğer satırlara hiç dokunmuyoruz.
        #
        # Yoksayılanlar sadece {anahtar: ad} olarak saklanıyor, Package
        # nesnesi değil — ikon için son taramadaki (self._data, TÜM
        # yüklü uygulamalar, sadece eski olanlar değil) eşleşen paketi
        # arıyoruz. Uygulama o zamandan beri kaldırıldıysa bulunamaz,
        # o durumda ikonsuz kalır (ad hâlâ doğru gösterilir).
        pkg_by_key = {_pkg_key(pkg): pkg for pkg, _ts in self._data}
        # "Kullanım zamanı" alt satırı için (ör. "1 ay önce") - asıl
        # listedeki satırların gösterdiği aynı bilgi, buraya hiç
        # eklenmemişti (canlı bildirildi: yoksayılan bir programın
        # kullanım zamanı hiç görünmüyordu).
        ts_by_key = {_pkg_key(pkg): ts for pkg, ts in self._data}

        for key in list(self._ignored_rows):
            if key not in ignored:
                row = self._ignored_rows.pop(key)
                self._ignored_row.remove(row)
                self._ignored_row_icons.discard(key)
                self._ignored_row_buttons.pop(key, None)

        for key, name in ignored.items():
            if key in self._ignored_rows:
                # Satır zaten var — sadece ikon/boyut eksikse tamamla
                # (tarama bu satır eklendikten SONRA bitmiş olabilir).
                if key not in self._ignored_row_icons:
                    pkg = pkg_by_key.get(key)
                    if pkg is not None:
                        row = self._ignored_rows[key]
                        row.set_subtitle(
                            f"{pkg.source} · {_format_last_used(ts_by_key.get(key))}"
                        )
                        icon = self._icon_for(pkg)
                        icon.add_css_class("pw-ignore-row-icon")
                        row.add_prefix(icon)
                        # Boyut, "−" düğmesinden SOLDA görünsün (asıl
                        # listedeki gibi) — düğme zaten eklenmiş
                        # olduğundan, önce çıkarıp boyuttan sonra
                        # yeniden ekliyoruz (add_suffix hep sona ekler).
                        remove_btn = self._ignored_row_buttons.get(key)
                        if remove_btn is not None:
                            row.remove(remove_btn)
                        size_label = Gtk.Label(
                            label=format_size(pkg.size) or "—",
                            css_classes=["numeric", "dim-label", "pw-ignore-row-size"],
                            valign=Gtk.Align.CENTER,
                        )
                        row.add_suffix(size_label)
                        if remove_btn is not None:
                            row.add_suffix(remove_btn)
                        self._ignored_row_icons.add(key)
                continue

            row = Adw.ActionRow(title=name)
            # Asıl listedeki satırlarda ikondan sonra bir onay kutusu var,
            # burada yok — bu yüzden başlık metni ("Counter-Strike 2" vb.)
            # asıl listedeki başlıklardan ("Frostpunk" vb.) daha soldan
            # başlıyordu. Aynı boyutta ama görünmez bir onay kutusu
            # ekleyerek başlıkları aynı x konumuna hizalıyoruz.
            spacer = Gtk.CheckButton(sensitive=False, can_target=False, opacity=0)
            spacer.add_css_class("pw-ignore-title-spacer")
            row.add_prefix(spacer)
            pkg = pkg_by_key.get(key)
            if pkg is not None:
                row.set_subtitle(
                    f"{pkg.source} · {_format_last_used(ts_by_key.get(key))}"
                )
                icon = self._icon_for(pkg)
                icon.add_css_class("pw-ignore-row-icon")
                row.add_prefix(icon)
                size_label = Gtk.Label(
                    label=format_size(pkg.size) or "—",
                    css_classes=["numeric", "dim-label", "pw-ignore-row-size"],
                    valign=Gtk.Align.CENTER,
                )
                row.add_suffix(size_label)
                self._ignored_row_icons.add(key)
            # "list-remove-symbolic" bu temada (Breeze-dark) düz bir tire
            # değil, X şeklinde çiziliyor — doğrulandı, kafa karıştırıyordu.
            # Temadan bağımsız garanti bir görünüm için gerçek bir "−"
            # (eksi) karakteri kullanıyoruz, ikon değil.
            remove_btn = Gtk.Button(
                label="−", valign=Gtk.Align.CENTER,
                css_classes=["flat", "circular", "destructive-action"],
                tooltip_text=_("Show again"),
            )
            remove_btn.connect("clicked", self._on_unignore_clicked, key)
            row.add_suffix(remove_btn)
            self._ignored_row.add_row(row)
            self._ignored_rows[key] = row
            self._ignored_row_buttons[key] = remove_btn

    def _on_unignore_clicked(self, _button, key):
        ignored = prefs.get("ignored_unused_programs") or {}
        name = ignored.get(key, "")
        self._unignore(key)
        # "Ekle" tarafındaki (yoksay düğmesi) bildirimde Geri al var —
        # kullanıcı burada da aynı simetriyi istedi.
        self._show_toast(
            _("{name} will be shown in the Unused programs list again").format(name=name),
            button_label=_("Undo"),
            on_action=lambda k=key, n=name: self._reignore(k, n),
            timeout=7,
        )

    def _unignore(self, key):
        ignored = dict(prefs.get("ignored_unused_programs") or {})
        ignored.pop(key, None)
        prefs.set("ignored_unused_programs", ignored)
        self._update_ignored_button()
        self._rebuild_list()

    def _reignore(self, key, name):
        """_on_unignore_clicked'in toast'ındaki Geri al eylemi."""
        ignored = dict(prefs.get("ignored_unused_programs") or {})
        ignored[key] = name
        prefs.set("ignored_unused_programs", ignored)
        self._selected_keys.discard(key)
        self._update_ignored_button()
        self._remove_row(key)

    def _icon_for(self, pkg) -> Gtk.Image:
        # 32 -> 40: satirlar biraz daha buyuk/ferah gorunsun istendi -
        # bu hem ana listede hem Yoksayilanlar panelinde kullanilan
        # PAYLASILAN ikon fonksiyonu, tek yerden ikisini de buyutuyor.
        name = self._main._icon_name_for(pkg)
        icon = Gtk.Image(pixel_size=40, valign=Gtk.Align.CENTER)
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
