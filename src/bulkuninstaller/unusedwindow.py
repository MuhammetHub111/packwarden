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
            default_width=720,
            default_height=760,
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

        settings_group = Adw.PreferencesGroup()
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
        )

        content = Gtk.Box(
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

        # "Bir daha gösterme" bildirimi için Adw.Toast/ToastOverlay
        # KULLANILMIYOR — libadwaita'nın kendi kayma animasyonu private/iç
        # bir efekt, dışarıdan sadece GTK'nin genel "animasyonları
        # etkinleştir" ayarıyla (uygulamadaki HER animasyonu etkiler,
        # ör. Yoksayılanlar perdesi) kapatılabiliyor. Kullanıcı sadece bu
        # bildirimin kaymadan, anında belirmesini istedi; bunu garanti
        # etmenin tek yolu kendi basit bildirim widget'ımızı yazmaktı.
        self._overlay = Gtk.Overlay(child=toolbar_view)
        self.set_content(self._overlay)
        self._build_toast_widget()

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

    def _build_toast_widget(self):
        self._toast_label = Gtk.Label(
            xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END,
        )
        self._toast_action_button = Gtk.Button(css_classes=["flat"])
        self._toast_action_button.connect("clicked", self._on_toast_action_clicked)
        self._toast_close_button = Gtk.Button(
            icon_name="window-close-symbolic",
            css_classes=["flat", "circular"],
            tooltip_text=_("Close"),
        )
        self._toast_close_button.connect("clicked", lambda *_a: self._hide_toast())

        self._toast = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
            css_classes=["pw-toast"],
            halign=Gtk.Align.CENTER, valign=Gtk.Align.END,
            # Alt eylem çubuğu (Vazgeç/Kaldır) kendi başına ~50-56px
            # yükseklikte — bildirim onun üstünde net bir boşlukla dursun.
            margin_bottom=72,
            # Kayma efekti YOK — kaymadan, olduğu yerde büyüyerek/sararak
            # beliren bir "baloncuk" efekti isteniyor. GTK'nin CSS geçiş
            # desteğiyle (transform + opacity) bunu kendimiz yönetiyoruz;
            # widget hep "visible" kalır, görünürlük "pw-toast-shown"
            # sınıfının eklenip çıkarılmasıyla animasyonlu değişir — bu
            # yüzden set_visible(False) yerine can_target ile tıklamaları
            # gizliyken devre dışı bırakıyoruz.
            visible=True,
            can_target=False,
        )
        self._toast.append(self._toast_label)
        self._toast.append(self._toast_action_button)
        self._toast.append(self._toast_close_button)
        self._overlay.add_overlay(self._toast)

        self._toast_timeout_id = None
        self._toast_action_callback = None

        css = b"""
        .pw-toast {
            background-color: rgba(46, 46, 50, 0.92);
            color: white;
            padding: 6px 6px 6px 16px;
            border-radius: 9999px;
            box-shadow: 0 3px 10px rgba(0, 0, 0, 0.35);
            opacity: 0;
            transform: scale(0.6);
            transition: opacity 180ms ease-out,
                        transform 220ms cubic-bezier(0.34, 1.56, 0.64, 1);
        }
        .pw-toast.pw-toast-shown {
            opacity: 1;
            transform: scale(1);
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
        /* Ok gostergesi kendi ayri "hit area" boslugu yuzunden +/-
           dugmelerinin hizasindan saga kayiyor. margin-end negatif
           degerle denendi ama bir noktadan sonra (~-33px) kilitleniyor
           (kucuk bir tiklama alani alt siniri var gibi); transform ise
           boyle bir sinira carpmiyor. Ekran goruntusu + piksel
           olcumuyle dogrulandi: ok ve alttaki "-" dugmesi artik AYNI
           x konumunda (670=670).
        */
        .pw-ignored-expander .expander-row-arrow {
            transform: translateX(-9px);
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _show_toast(self, title, button_label=None, on_action=None, timeout=5):
        if self._toast_timeout_id is not None:
            GLib.source_remove(self._toast_timeout_id)
            self._toast_timeout_id = None
        self._toast_label.set_label(title)
        if button_label:
            self._toast_action_button.set_label(button_label)
            self._toast_action_button.set_visible(True)
        else:
            self._toast_action_button.set_visible(False)
        self._toast_action_callback = on_action
        self._toast.set_can_target(True)
        self._toast.add_css_class("pw-toast-shown")
        if timeout:
            self._toast_timeout_id = GLib.timeout_add_seconds(
                timeout, self._on_toast_timeout
            )

    def _on_toast_timeout(self):
        self._toast_timeout_id = None
        self._hide_toast()
        return GLib.SOURCE_REMOVE

    def _hide_toast(self):
        if self._toast_timeout_id is not None:
            GLib.source_remove(self._toast_timeout_id)
            self._toast_timeout_id = None
        self._toast.remove_css_class("pw-toast-shown")
        self._toast.set_can_target(False)
        self._toast_action_callback = None

    def _on_toast_action_clicked(self, _button):
        callback = self._toast_action_callback
        self._hide_toast()
        if callback is not None:
            callback()

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
                label=format_size(pkg.size),
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
        # _show_toast önceki bildirimi (varsa) anında değiştirir, kuyruğa
        # almaz — art arda birkaç uygulamayı hızlıca işaretlemek gecikmeli
        # hissettirmiyor.
        self._show_toast(
            _("{name} won't be shown again").format(name=pkg.name),
            button_label=_("Undo"),
            on_action=lambda k=key: self._unignore(k),
            timeout=5,
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
        self._ignored_row.set_visible(bool(ignored))
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

        for key in list(self._ignored_rows):
            if key not in ignored:
                row = self._ignored_rows.pop(key)
                self._ignored_row.remove(row)
                self._ignored_row_icons.discard(key)

        for key, name in ignored.items():
            if key in self._ignored_rows:
                # Satır zaten var — sadece ikonu eksikse tamamla (tarama
                # bu satır eklendikten SONRA bitmiş olabilir).
                if key not in self._ignored_row_icons:
                    pkg = pkg_by_key.get(key)
                    if pkg is not None:
                        self._ignored_rows[key].add_prefix(self._icon_for(pkg))
                        self._ignored_row_icons.add(key)
                continue

            row = Adw.ActionRow(title=name)
            pkg = pkg_by_key.get(key)
            if pkg is not None:
                row.add_prefix(self._icon_for(pkg))
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

    def _on_unignore_clicked(self, _button, key):
        ignored = prefs.get("ignored_unused_programs") or {}
        name = ignored.get(key, "")
        self._unignore(key)
        # "Ekle" tarafındaki (yoksay düğmesi) bildirimde Geri al var —
        # kullanıcı burada da aynı simetriyi istedi.
        self._show_toast(
            _("{name} will be shown again").format(name=name),
            button_label=_("Undo"),
            on_action=lambda k=key, n=name: self._reignore(k, n),
            timeout=5,
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
