import threading

from gi.repository import Adw, GLib, Gtk

from . import VERSION, host, prefs, usage_service
from .backends.selfinstall import SelfInstallBackend
from .i18n import _
from .updater import fetch_remote_version


def _wide_collapsed_factory() -> Gtk.SignalListItemFactory:
    """Adw.ComboRow'un VARSAYILAN satır fabrikası, seçili değeri gösteren
    (kapalı/collapsed) etikette ellipsize AÇIK bırakıyor - GTK'da
    ellipsize açıkken bir Gtk.Label, gerçek metin ne kadar uzun olursa
    olsun KASITLI OLARAK küçük bir "doğal genişlik" istiyor (canlı
    doğrulandı: "Çöp Kutusu'na taşı" için GtkLabel.measure() yalnızca
    117px doğal genişlik döndürüyordu - metnin kendisi çok daha
    fazlasını gerektirdiği hâlde). Kısa seçenekler (Türkçe, Koyu gibi)
    bu payda rastlantısal olarak sığdığı için fark edilmiyor, ama daha
    uzun seçenekler (Çöp Kutusu'na taşı, Kalıcı olarak sil) sürekli
    "..." ile kesiliyordu.

    Fix: ellipsize'ı tamamen KAPALI bırakan düz bir Gtk.Label (canlı
    doğrulandı: width_chars YERİNE bunu kullanınca etiket gerçek metin
    genişliğine - ne bir piksel fazla ne eksik - tam oturuyor; önceki
    width_chars=22 denemesi "Çöp Kutusu'na taşı" için gerçekte gereken
    117px yerine 160px talep ediyordu - kullanıcının şikayet ettiği
    fazladan boşluğun kaynağı tam olarak buydu). xalign=1 (sağa yaslı),
    Dil/Tema satırlarının kapalı görünümüyle aynı hizalama. Bu sadece
    KAPALI görünüm için - açılır listenin kendi görünümü
    _wide_popup_factory()'de ayrı tanımlı."""
    factory = Gtk.SignalListItemFactory()

    def on_setup(_factory, list_item):
        list_item.set_child(Gtk.Label(xalign=1))

    def on_bind(_factory, list_item):
        list_item.get_child().set_text(list_item.get_item().get_string())

    factory.connect("setup", on_setup)
    factory.connect("bind", on_bind)
    return factory


def _wide_popup_factory(combo_row: Adw.ComboRow) -> Gtk.SignalListItemFactory:
    """Açılır listenin (popup) kendi görünümü - Dil/Tema satırlarının
    VARSAYILAN görünümüyle BİREBİR aynı olacak şekilde elle yeniden
    kuruluyor (sola yaslı metin + seçili öğenin yanında onay işareti),
    çünkü `Adw.ComboRow.factory`'yi _wide_collapsed_factory() ile
    değiştirince (kapalı görünümdeki kesilmeyi düzeltmek için) bu,
    AYNI ZAMANDA açılır listenin görünümünü de (onay işareti dahil)
    sessizce kaldırıyordu. Etiket düz (ellipsize yok, width_chars yok)
    - canlı ölçümle doğrulandı: width_chars=20 popup içeriğini 224px'e
    şişiriyordu, gerçek ihtiyaç (metin + boşluk + onay ikonu) sadece
    181px; aradaki 43px fark kullanıcının "sağda gereksiz boşluk"
    olarak gördüğü şeydi.

    Onay işareti opacity'si sadece `bind` sırasında BİR KEZ ayarlanıyordu
    - GtkListView kısa (2 öğelik) bir liste için öğeleri geri dönüştürmediği
    için `bind` bir daha hiç tetiklenmiyor, dolayısıyla seçim değiştikten
    sonra popup tekrar açıldığında onay işareti hâlâ ESKİ öğenin yanında
    kalıyordu (canlı doğrulandı: kullanıcı raporu). Şimdi hangi kutunun
    hangi öğeye bağlı olduğu `bound` listesinde tutuluyor ve
    `combo_row`'un "selected-item" değişimi dinlenip tüm bağlı kutular
    buna göre yeniden çiziliyor.

    Satır kutusu `halign=START` idi - bu, her kutunun KENDİ metninin
    genişliğine göre ayrı ayrı daralmasına yol açıyordu (canlı
    doğrulandı: "Çöp Kutusu'na taşı" kutusu 145px, "Kalıcı olarak sil"
    kutusu sadece 120px - onay işareti iki seçenekte FARKLI sütunlarda
    duruyordu). Dil/Tema'nın varsayılan fabrikasında tüm satırlar AYNI
    genişliğe sahip (134px, metin uzunluğundan bağımsız). Kutudan
    `halign=START`'ı kaldırıp (satırın GtkListView'ın verdiği ortak
    genişliğe yayılmasına izin vererek) etikete `hexpand=True` vermek
    bunu taklit ediyor - etiket boşluğu yutuyor, onay işareti her
    satırda aynı sağ sütuna hizalanıyor."""
    factory = Gtk.SignalListItemFactory()
    bound = []

    def refresh(*_args):
        selected = combo_row.get_selected_item()
        for item, check in bound:
            check.set_opacity(1.0 if item == selected else 0.0)

    def on_setup(_factory, list_item):
        box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        box.append(Gtk.Label(xalign=0, hexpand=True, valign=Gtk.Align.CENTER))
        box.append(
            Gtk.Image(
                icon_name="object-select-symbolic", valign=Gtk.Align.CENTER
            )
        )
        list_item.set_child(box)

    def on_bind(_factory, list_item):
        box = list_item.get_child()
        label, check = box.get_first_child(), box.get_last_child()
        item = list_item.get_item()
        label.set_text(item.get_string())
        bound.append((item, check))
        refresh()

    def on_unbind(_factory, list_item):
        box = list_item.get_child()
        check = box.get_last_child()
        bound[:] = [pair for pair in bound if pair[1] is not check]

    factory.connect("setup", on_setup)
    factory.connect("bind", on_bind)
    factory.connect("unbind", on_unbind)
    combo_row.connect("notify::selected-item", refresh)
    return factory


class SettingsDialog(Adw.Window):
    def __init__(self, app, parent_window):
        # Adw.Dialog DEGIL Adw.Window (bkz. PackWarden 0.15.3 -
        # UnusedAppsWindow'un ayni gecisi): Ayarlar ve Kullanilmayan
        # Programlar ayni ebeveynin tek dialog-slotunu paylasinca
        # (bkz. eski close_settings_if_open/close_unused_if_open)
        # ikisi ayni anda acik kalamiyordu, biri digerinin arkasindan
        # tasip goruntu karisiyordu. Gercek, bagimsiz bir ust-seviye
        # pencere olunca bu celiski yapisal olarak ortadan kalkiyor -
        # ikisi artik ayni anda, birbirini hic etkilemeden acik
        # kalabiliyor.
        #
        # 780x830 - Kullanılmayan Programlar'la gorsel olarak benzer
        # boyut (sadece estetik tercih, artik ikisi ayni anda acik
        # kalabildigi icin islevsel bir zorunluluk degil).
        super().__init__(
            transient_for=parent_window,
            title=_("Settings"),
            default_width=780,
            default_height=830,
        )
        self._app = app

        page = Adw.PreferencesPage(
            title=_("General"),
            icon_name="emblem-system-symbolic",
        )

        # ---------------------------------------------------------
        # Package list
        # ---------------------------------------------------------
        list_group = Adw.PreferencesGroup(title=_("Package list"))
        page.add(list_group)

        apps_only_row = Adw.SwitchRow(
            title=_("Show applications only"),
            subtitle=_("Hides libraries and system packages"),
            active=bool(prefs.get("apps_only")),
        )
        apps_only_row.connect(
            "notify::active",
            self._on_apps_only_changed,
        )
        list_group.add(apps_only_row)

        # ---------------------------------------------------------
        # Safety
        # ---------------------------------------------------------
        safety_group = Adw.PreferencesGroup(title=_("Safety"))
        page.add(safety_group)

        protect_row = Adw.SwitchRow(
            title=_("Protect system packages"),
            subtitle=_(
                "Shows an extra warning before removing packages "
                "your system needs to run"
            ),
            active=bool(prefs.get("protect_system")),
        )
        protect_row.connect(
            "notify::active",
            lambda row, _p: prefs.set(
                "protect_system",
                row.get_active(),
            ),
        )
        safety_group.add(protect_row)

        # Leftover deletion method
        self._leftover_delete_modes = ["trash", "permanent"]

        deletion_row = Adw.ComboRow(
            title=_("Leftover deletion method"),
            subtitle=_("Choose what happens to leftover files"),
            model=Gtk.StringList.new(
                [
                    _("Move to Trash"),
                    _("Delete permanently"),
                ]
            ),
        )
        deletion_row.set_factory(_wide_collapsed_factory())
        deletion_row.set_list_factory(_wide_popup_factory(deletion_row))

        current_mode = prefs.get("leftover_delete_mode")
        if current_mode in self._leftover_delete_modes:
            deletion_row.set_selected(
                self._leftover_delete_modes.index(current_mode)
            )

        deletion_row.connect(
            "notify::selected",
            self._on_leftover_delete_mode_changed,
        )
        safety_group.add(deletion_row)

        # ---------------------------------------------------------
        # Privacy
        # ---------------------------------------------------------
        privacy_group = Adw.PreferencesGroup(
            title=_("Privacy"),
            description=_(
                "Off by default. Fully local, no network access. Only a "
                "package id and a timestamp are stored, never process "
                "names or window titles."
            ),
        )
        page.add(privacy_group)

        # Bu anahtar systemd --user birimine (bkz. usage_service.py)
        # dayanıyor - o birim SADECE install.sh ile kurulduğunda yazılır.
        # Flatpak'ta veya bir dağıtım paketiyle kurulduğunda hiç yoktur -
        # anahtarı göstermek, açmaya çalışınca her zaman "install.sh
        # çalıştırın" diyen bir hatayla sonuçlanırdı (Flatpak kullanıcısı
        # için anlamsız/yanıltıcı bir tavsiye). usage_service.is_installed()
        # zaten bu AYRIMIN kendisini (hangi kurulum yöntemi olursa olsun)
        # tek bir, ucuz dosya-varlığı kontrolüyle veriyor.
        if usage_service.is_installed():
            self._background_usage_row = Adw.SwitchRow(
                title=_("Detect app usage in the background"),
                subtitle=_(
                    "Without this, app usage isn't updated while PackWarden "
                    "is closed, so the Unused Programs list can be inaccurate"
                ),
                active=bool(prefs.get("background_usage_detection")),
            )
            self._background_usage_row.connect(
                "notify::active",
                self._on_background_usage_changed,
            )
            privacy_group.add(self._background_usage_row)
            self._sync_background_usage_state()
        else:
            privacy_group.add(Adw.ActionRow(
                title=_("Background usage detection isn't available"),
                subtitle=_(
                    "This needs PackWarden's own installer script. It "
                    "isn't available for a Flatpak or distro-package install."
                ),
            ))

        # ---------------------------------------------------------
        # Language
        # ---------------------------------------------------------
        language_group = Adw.PreferencesGroup(title=_("Language"))
        page.add(language_group)

        self._language_values = ["auto", "tr", "en"]

        language_row = Adw.ComboRow(
            title=_("Interface language"),
            subtitle=_("Takes effect after restarting the app"),
            model=Gtk.StringList.new(
                [
                    _("Automatic (system)"),
                    "Türkçe",
                    "English",
                ]
            ),
        )

        current_language = prefs.get("language")
        if current_language in self._language_values:
            language_row.set_selected(
                self._language_values.index(current_language)
            )

        language_row.connect(
            "notify::selected",
            self._on_language_changed,
        )
        language_group.add(language_row)

        # ---------------------------------------------------------
        # Appearance
        # ---------------------------------------------------------
        theme_group = Adw.PreferencesGroup(title=_("Appearance"))
        page.add(theme_group)

        self._theme_values = ["system", "light", "dark"]

        theme_row = Adw.ComboRow(
            title=_("Theme"),
            subtitle=_("Choose application appearance"),
            model=Gtk.StringList.new(
                [
                    _("System default"),
                    _("Light"),
                    _("Dark"),
                ]
            ),
        )

        current_theme = prefs.get("theme")
        if current_theme in self._theme_values:
            theme_row.set_selected(
                self._theme_values.index(current_theme)
            )

        theme_row.connect(
            "notify::selected",
            self._on_theme_changed,
        )
        theme_group.add(theme_row)

        # ---------------------------------------------------------
        # Updates
        # ---------------------------------------------------------
        updates_group = Adw.PreferencesGroup(title=_("Updates"))
        page.add(updates_group)

        running_row = Adw.ActionRow(
            title=_("Running version"),
            subtitle=VERSION,
        )
        updates_group.add(running_row)

        # Kendiliğinden güncelleme (updater.py: download_and_install/
        # restart_app) yalnızca install.sh ile yapılan self-install kurulumu
        # için tasarlandı - ~/.local/share/packwarden'ı doğrudan üzerine
        # yazıyor. Flatpak içinde bu yol sandbox'ta yönlendirilir (gerçek
        # /app ağacına hiç dokunmaz - build-aux/*.PackWarden.yaml, Flatpak'ın
        # GERÇEK bir dağıtım kanalı olduğunu doğruluyor) ve "Yeniden
        # Başlat" aynı eski sürümü açar; aynı şekilde pacman/AUR gibi bir
        # dağıtım paketiyle kurulduğunda da (SelfInstallBackend.is_available()
        # False) bu yol o kurulumla hiç ilişkili olmayan, ayrı bir kopya
        # oluşturur. İkisinde de "Güncelleme kuruldu" diyen akış hiçbir şey
        # değiştirmez - kullanıcıyı gerçek dağıtım kanalına yönlendirmek
        # daha doğru. application.py:_seed_default_ignored_packwarden()
        # zaten aynı host.IN_FLATPAK ayrımını kullanıyor.
        if host.IN_FLATPAK or not SelfInstallBackend().is_available():
            info_row = Adw.ActionRow(
                title=_("Update from where you installed PackWarden"),
                subtitle=_(
                    "This copy wasn't installed with PackWarden's own "
                    "installer script, so the built-in updater can't "
                    "update it. Use Flatpak, your package manager, or "
                    "your software center instead."
                ),
            )
            updates_group.add(info_row)
        else:
            auto_update_row = Adw.SwitchRow(
                title=_("Automatic Updates"),
                subtitle=_(
                    "Automatically checks for a new version on startup and "
                    "opens the update dialog when one is found"
                ),
                active=bool(prefs.get("auto_update")),
            )
            auto_update_row.connect(
                "notify::active",
                lambda row, _p: prefs.set(
                    "auto_update",
                    row.get_active(),
                ),
            )
            updates_group.add(auto_update_row)

            self._check_button = Gtk.Button(
                label=_("Check for Updates"),
                valign=Gtk.Align.CENTER,
                css_classes=["suggested-action"],
            )
            self._check_button.connect(
                "clicked",
                self._on_check_updates,
            )

            self._check_spinner = Gtk.Spinner(
                valign=Gtk.Align.CENTER,
            )

            check_row = Adw.ActionRow(title="")
            check_row.add_suffix(self._check_spinner)
            check_row.add_suffix(self._check_button)
            updates_group.add(check_row)

        header = Adw.HeaderBar()
        scrolled = Gtk.ScrolledWindow(child=page, vexpand=True)
        toolbar_view = Adw.ToolbarView(content=scrolled)
        toolbar_view.add_top_bar(header)
        self.set_content(toolbar_view)

    # -------------------------------------------------------------
    # Theme
    # -------------------------------------------------------------
    def _on_theme_changed(self, row, _pspec):
        index = row.get_selected()

        if not (0 <= index < len(self._theme_values)):
            return

        theme = self._theme_values[index]
        prefs.set("theme", theme)
        self._app.apply_theme(theme)

    # -------------------------------------------------------------
    # Background usage detection
    # -------------------------------------------------------------
    def _sync_background_usage_state(self):
        """Açılışta gösterilen anahtar, kayıtlı tercihe (prefs) göre
        ayarlanıyor - ama gerçek systemd durumu PackWarden'ın DIŞINDA
        (ör. kullanıcı elle `systemctl --user disable ...` çalıştırırsa,
        ya da bir sistem güncellemesi birimi sıfırlarsa) değişmiş
        olabilir. Burada GERÇEK durumu (usage_service.is_enabled())
        sorup, kayıtlı tercihten FARKLIYSA anahtarı ve tercihi gerçeğe
        göre düzeltiyoruz - tersi değil, çünkü systemd burada tek
        doğruluk kaynağı.

        Diğer tüm systemctl çağrıları gibi (bkz. _on_background_usage_
        changed) arka plan thread'inde - bu da birkaç yüz milisaniye
        sürebilen bir alt süreç çağrısı, ana thread'de UI'ı kilitlemesin.
        """
        def worker():
            real = usage_service.is_enabled()
            GLib.idle_add(self._on_background_usage_synced, real)

        threading.Thread(target=worker, daemon=True).start()

    def _on_background_usage_synced(self, real_enabled):
        stored = bool(prefs.get("background_usage_detection"))
        if real_enabled != stored:
            self._background_usage_row.handler_block_by_func(
                self._on_background_usage_changed
            )
            self._background_usage_row.set_active(real_enabled)
            self._background_usage_row.handler_unblock_by_func(
                self._on_background_usage_changed
            )
            prefs.set("background_usage_detection", real_enabled)
        return GLib.SOURCE_REMOVE

    def _on_background_usage_changed(self, row, _pspec):
        wanted = row.get_active()
        # systemctl senkron olarak birkaç saniye sürebilir (daemon-reload,
        # servis başlatma) — ana thread'de çağrılırsa arayüz o süre boyunca
        # tamamen kilitlenir. Diğer ayarlardaki güncelleme kontrolü gibi
        # arka plan thread'ine alınıyor.
        row.set_sensitive(False)

        def worker():
            ok, error = usage_service.set_enabled(wanted)
            GLib.idle_add(self._on_background_usage_done, row, wanted, ok, error)

        threading.Thread(target=worker, daemon=True).start()

    def _on_background_usage_done(self, row, wanted, ok, error):
        row.set_sensitive(True)
        if not ok:
            # Anahtarı gerçek duruma geri al — sinyali tekrar tetiklememek
            # için handler'ı geçici olarak susturuyoruz.
            row.handler_block_by_func(self._on_background_usage_changed)
            row.set_active(not wanted)
            row.handler_unblock_by_func(self._on_background_usage_changed)

            dialog = Adw.AlertDialog(
                heading=_("Could not change this setting"),
                body=error,
            )
            dialog.add_response("ok", _("OK"))
            dialog.present(self)
            return GLib.SOURCE_REMOVE
        prefs.set("background_usage_detection", wanted)
        return GLib.SOURCE_REMOVE

    # -------------------------------------------------------------
    # Leftover deletion method
    # -------------------------------------------------------------
    def _on_leftover_delete_mode_changed(self, row, _pspec):
        index = row.get_selected()

        if not (0 <= index < len(self._leftover_delete_modes)):
            return

        prefs.set(
            "leftover_delete_mode",
            self._leftover_delete_modes[index],
        )

    # -------------------------------------------------------------
    # Language
    # -------------------------------------------------------------
    def _on_language_changed(self, row, _pspec):
        index = row.get_selected()

        if not (0 <= index < len(self._language_values)):
            return

        prefs.set(
            "language",
            self._language_values[index],
        )

        dialog = Adw.AlertDialog(
            heading=_("Restart to apply?"),
            body=_(
                "The interface language changes after the app restarts."
            ),
        )
        dialog.add_response(
            "later",
            _("Later"),
        )
        dialog.add_response(
            "restart",
            _("Restart Now"),
        )
        dialog.set_response_appearance(
            "restart",
            Adw.ResponseAppearance.SUGGESTED,
        )
        dialog.set_default_response("restart")
        dialog.set_close_response("later")
        dialog.connect(
            "response",
            self._on_language_restart_response,
        )
        dialog.present(self)

    def _on_language_restart_response(self, _dialog, response):
        if response == "restart":
            from .updater import restart_app

            restart_app()
            self._app.quit()

    # -------------------------------------------------------------
    # Applications-only
    # -------------------------------------------------------------
    def _on_apps_only_changed(self, row, _pspec):
        window = self._app.props.active_window

        if window is not None and hasattr(window, "set_apps_only"):
            window.set_apps_only(row.get_active())
        else:
            prefs.set(
                "apps_only",
                row.get_active(),
            )

    # -------------------------------------------------------------
    # Update checking
    # -------------------------------------------------------------
    def _on_check_updates(self, _button):
        self._check_button.set_sensitive(False)
        self._check_spinner.set_spinning(True)

        def worker():
            remote = fetch_remote_version()

            GLib.idle_add(
                self._on_check_done,
                remote,
            )

        threading.Thread(
            target=worker,
            daemon=True,
        ).start()

    def _on_check_done(self, remote):
        self._check_button.set_sensitive(True)
        self._check_spinner.set_spinning(False)

        from .updatewindow import open_if_newer

        if remote is None:
            dialog = Adw.AlertDialog(
                heading=_("Could not check for updates"),
                body=_(
                    "Please check your internet connection and try again."
                ),
            )
            dialog.add_response(
                "ok",
                _("OK"),
            )
            dialog.present(self)

        elif open_if_newer(self._app, remote):
            pass

        else:
            dialog = Adw.AlertDialog(
                heading=_("You are up to date"),
                body=_("You are running the latest version."),
            )
            dialog.add_response(
                "ok",
                _("OK"),
            )
            dialog.present(self)

        return GLib.SOURCE_REMOVE