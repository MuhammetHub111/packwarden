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
            default_width=640,
            default_height=560,
        )
        self._main = main_window
        self._data: list[tuple[object, float | None]] = []
        self._checks: list[tuple[Gtk.CheckButton, object]] = []
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

        settings_group = Adw.PreferencesGroup()
        settings_group.add(self._threshold_row)
        settings_group.add(self._custom_row)

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
        self.set_content(toolbar_view)

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
        self._data = data
        # Liste üyeliği (hangi paketler eşiğin üzerinde) değişmediyse
        # yeniden kurma — sadece metin/zaman damgası birkaç saniye
        # ilerlemiş olabilir, bu görsel olarak önemli değil ve sürekli
        # yeniden kurmak seçimleri/kaydırmayı bozuyordu.
        new_stale_keys = frozenset(
            _pkg_key(pkg) for pkg, ts in data
            if ts is not None and ts <= time.time() - self._threshold_days * 86400
        )
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

        self._last_stale_keys = frozenset(_pkg_key(pkg) for pkg, _ts in stale)
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
        for pkg, ts in stale:
            key = _pkg_key(pkg)
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
            self._list_box.append(row)
            self._checks.append((check, pkg))

        self._select_all.set_sensitive(True)
        self._update_selection()

    def _on_row_toggled(self, check, key):
        if check.get_active():
            self._selected_keys.add(key)
        else:
            self._selected_keys.discard(key)
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
