"""Kaldırma onayı penceresi.

"Seçilenleri Kaldır" düğmesine basınca açılır. Üstte kaldırılacak
paketler, altında o paketlere ait kalıntı dosyalar kategorilere
ayrılmış ve tiklenebilir hâlde listelenir. Alt çubukta üç yol vardır:
Vazgeç / Yedeksiz Kaldır / Yedekle ve Kaldır.
"""

import os
import re
import threading

from gi.repository import Adw, Gio, GLib, Gtk

from . import host, prefs, usage
from .backends.base import format_size
from .backup import create_backup
from .i18n import _
from .leftovers import find_package_leftovers, remove_leftovers
from .protected import is_protected

# Wine'ın HER çağrısında çıkan rutin teşhis satırları ("0088:fixme:...",
# "00fc:err:module:import_dll ..."e) - başarısızlığın gerçek NEDENİYLE
# ilgisi yok, her zaman aynı şekilde basılıyor (canlı doğrulandı: hem
# başarılı hem başarısız kaldırmalarda aynı satırlar çıkıyor). Hata
# özetinde bunları gösterip asıl anlamlı satırı gürültüye boğmamak için
# eleniyor.
_WINE_NOISE_RE = re.compile(r"^[0-9a-f]+:(fixme|err):", re.IGNORECASE)


def _installed_flatpak_ids() -> set:
    try:
        proc = host.run(
            ["flatpak", "list", "--app", "--columns=application"], timeout=15
        )
        return {line.strip() for line in proc.stdout.splitlines() if line.strip()}
    except Exception:
        return set()


def _filter_leftovers_to_packages(
    leftovers: list, packages: list
) -> list:
    """`leftovers`'ı yalnızca `packages` için YENİDEN bulunabilen bir alt
    kümeyle sınırlar.

    Karma backend'li bir toplu kaldırmada bir backend iptal/başarısız
    olup diğeri başarılı olursa, kullanıcının işaretlediği kalıntılar
    HÂLÂ kaldırılmamış paketlere ait olabilir (ör. pacman iptal edildi -
    o paket hâlâ kurulu - ama onun yapılandırma klasörü yine de
    silinirse hâlâ kullanılan bir uygulamanın ayarları kaybolur). Leftover
    nesnesinin hangi paketten geldiğine dair bir geri referansı yok, bu
    yüzden en güvenli yol: kalıntı taramasını SADECE gerçekten kaldırılan
    paketlerle tekrar çalıştırıp, kullanıcının işaretlediği kalıntılardan
    yalnızca bu yeni sonuçta da çıkanları silinecek olarak kabul etmek."""
    if not packages:
        return []
    valid_paths = {
        item.path
        for _category, items in find_package_leftovers(packages)
        for item in items
    }
    return [item for item in leftovers if item.path in valid_paths]


def _clean_unused_flatpak_data() -> None:
    """Kaldırılan Flatpak paketlerinin arkasında kalan paylaşımlı, artık
    yetim runtime'ları ve OSTree depo nesnelerini temizler (Bazaar'ın
    "Trash Data" dediği şey — bkz. 7050a23, 96dc5ee, e4c0366). Daha önce
    ayrı bir Araçlar menü öğesiydi, kafa karıştırdığı için kaldırılıp
    kaldırma akışının içine gömülmesi planlanmıştı: bir paket kaldırılırken
    Flatpak kaynaklıysa bu adım otomatik ve sessizce çalışır, ayrı bir
    onay istemez — çağıran taraf (worker) before/after kurulu-uygulama
    listesini kıyaslayıp yan etki olursa kullanıcıyı ayrıca uyarır."""
    try:
        host.run(["flatpak", "uninstall", "--unused", "-y"], timeout=120)
    except Exception:
        pass
    try:
        import gi
        gi.require_version("Flatpak", "1.0")
        from gi.repository import Flatpak
        for make_installation in (
            Flatpak.Installation.new_system, Flatpak.Installation.new_user,
        ):
            try:
                make_installation().prune_local_repo()
            except Exception:
                pass
    except Exception:
        pass


class RemovalWindow(Adw.Window):
    def __init__(self, main_window, items):
        super().__init__(
            transient_for=main_window,
            modal=True,
            title=_("Confirm Removal"),
            default_width=760,
            default_height=620,
        )
        self._main = main_window
        self._pkgs = [item.pkg for item in items]
        self._checks: list[tuple[Gtk.CheckButton, object]] = []
        self._busy = False
        self._protected = [pkg for pkg in self._pkgs if is_protected(pkg)]
        self._risk_acknowledged = False

        header = Adw.HeaderBar()

        self._list_box = Gtk.ListBox(
            css_classes=["boxed-list"],
            selection_mode=Gtk.SelectionMode.NONE,
            margin_top=12, margin_bottom=12, margin_start=12, margin_end=12,
            valign=Gtk.Align.START,
        )
        clamp = Adw.Clamp(maximum_size=860, child=self._list_box)
        scrolled = Gtk.ScrolledWindow(child=clamp, vexpand=True)

        # Üst bölüm: kaldırılacak paketler (her zaman görünür)
        total = sum(pkg.size for pkg in self._pkgs)
        self._pkg_total_size = total
        pkg_expander = Adw.ExpanderRow(
            title=_("Packages to remove"),
            subtitle=_("{count} packages • {size}").format(
                count=len(self._pkgs), size=format_size(total) or "—"
            ),
        )
        pkg_expander.set_expanded(True)
        for pkg in self._pkgs:
            badge = f"{pkg.source} · {pkg.origin}" if pkg.origin else pkg.source
            row = Adw.ActionRow(title=pkg.name, subtitle=badge)
            if is_protected(pkg):
                row.set_subtitle(
                    badge + "  •  ⚠ " + _("critical system package")
                )
                warn = Gtk.Image(icon_name="dialog-warning-symbolic")
                warn.add_css_class("warning")
                row.add_prefix(warn)
            size_label = Gtk.Label(
                # format_size(0) boş dize döner (bilinmeyen boyut - ör.
                # Wine Mono Runtime) - boş bırakınca bozuk görünüyordu.
                label=format_size(pkg.size) or "—",
                css_classes=["numeric", "dim-label"],
                valign=Gtk.Align.CENTER,
            )
            row.add_suffix(size_label)
            pkg_expander.add_row(row)
        self._list_box.append(pkg_expander)

        # Alt bölüm: kalıntı kategorileri (tarama bitince dolar)
        self._scan_row = Adw.ActionRow(title=_("Scanning leftovers…"))
        self._scan_row.add_prefix(Gtk.Spinner(spinning=True))
        self._list_box.append(self._scan_row)

        # Alt çubuk
        self._cancel_button = Gtk.Button(label=_("Cancel"))
        self._cancel_button.connect("clicked", lambda *_a: self.close())

        self._select_all = Gtk.CheckButton(label=_("Select all"), sensitive=False)
        self._select_all.connect("toggled", self._on_select_all)
        self._selection_label = Gtk.Label(css_classes=["dim-label"])

        self._backup_button = Gtk.Button(
            label=_("Back up and Remove"), css_classes=["suggested-action"]
        )
        self._backup_button.connect("clicked", self._on_remove, True)
        self._nobackup_button = Gtk.Button(
            label=_("Remove without Backup"), css_classes=["destructive-action"]
        )
        self._nobackup_button.connect("clicked", self._on_remove, False)
        self._spinner = Gtk.Spinner()

        action_bar = Gtk.ActionBar()
        action_bar.pack_start(self._cancel_button)
        action_bar.pack_start(self._select_all)
        action_bar.pack_start(self._selection_label)
        action_bar.pack_end(self._backup_button)
        action_bar.pack_end(self._nobackup_button)
        action_bar.pack_end(self._spinner)

        toolbar_view = Adw.ToolbarView(content=scrolled)
        toolbar_view.add_top_bar(header)
        toolbar_view.add_bottom_bar(action_bar)

        self._toast_overlay = Adw.ToastOverlay(child=toolbar_view)
        self.set_content(self._toast_overlay)

        self._scan()

    # ---------------- Kalıntı taraması ----------------

    def _scan(self):
        def worker():
            try:
                groups = find_package_leftovers(self._pkgs)
            except Exception:
                groups = []
            GLib.idle_add(self._on_scanned, groups)

        threading.Thread(target=worker, daemon=True).start()

    def _on_scanned(self, groups):
        self._list_box.remove(self._scan_row)

        if not groups:
            row = Adw.ActionRow(
                title=_("No leftover files found"),
                subtitle=_("These packages keep no extra files behind"),
            )
            row.add_prefix(Gtk.Image(icon_name="emblem-ok-symbolic"))
            self._list_box.append(row)
            return GLib.SOURCE_REMOVE

        for category, items in groups:
            cat_size = sum(item.size for item in items)
            size_text = format_size(cat_size)
            # Registry girdisi gibi anlamli bir bayt boyutu olmayan
            # kalintilarda "0" gostermek yerine sayimi tek basina birak.
            subtitle = f"{len(items)} • {size_text}" if size_text else str(len(items))
            expander = Adw.ExpanderRow(title=_(category), subtitle=subtitle)
            expander.set_expanded(True)
            for item in items:
                is_registry = item.kind == "registry"
                # Bu kayıt WineBackend.remove() tarafından paketin kendisi
                # kaldırılırken HER DURUMDA silinir - işareti kaldırılsa
                # da, "Yedekle ve Kaldır" seçilse de (bkz. backends/wine.py
                # _force_remove, leftovers.py _find_wine_registry_leftovers
                # docstring'i). Ayrıca GERÇEK bir dosya yolu olmadığından
                # (bkz. Leftover.path burada sentetik bir metin) backup.py
                # _archive() bunu arşive HİÇ EKLEYEMEZ - sessizce atlar.
                # Burada SAHTE bir seçim sunmak (işareti kaldırmak/
                # yedeklemek hiçbir şeyi değiştirmiyor) yerine, bunu
                # AÇIKÇA kilitli ve etiketli gösteriyoruz.
                check = Gtk.CheckButton(
                    valign=Gtk.Align.CENTER, active=True,
                    sensitive=not is_registry,
                )
                if is_registry:
                    check.set_tooltip_text(_(
                        "Always removed together with the package - "
                        "cannot be backed up or skipped separately"
                    ))
                check.connect("toggled", lambda *_a: self._update_selection())
                subtitle = item.path
                if is_registry:
                    subtitle += "  •  " + _(
                        "not included in the backup"
                    )
                row = Adw.ActionRow(
                    title=os.path.basename(item.path),
                    subtitle=subtitle,
                    activatable_widget=check,
                )
                row.add_prefix(check)
                size_label = Gtk.Label(
                    # format_size(0) boş dize döner (ör. "Registry entry"
                    # kalıntısı - gerçek bir bayt boyutu yok) - diğer
                    # kalıntı satırları gerçek boyut gösterirken bu satır
                    # tamamen boş kalıp bozuk görünüyordu.
                    label=format_size(item.size) or "—",
                    css_classes=["numeric", "dim-label"],
                    valign=Gtk.Align.CENTER,
                )
                row.add_suffix(size_label)
                expander.add_row(row)
                self._checks.append((check, item))
            self._list_box.append(expander)

        self._select_all.set_sensitive(True)
        self._select_all.set_active(True)
        self._update_selection()
        return GLib.SOURCE_REMOVE

    # ---------------- Seçim ----------------

    def _selected_leftovers(self):
        return [item for check, item in self._checks if check.get_active()]

    def _on_select_all(self, check):
        active = check.get_active()
        for row_check, _item in self._checks:
            row_check.set_active(active)
        self._update_selection()

    def _update_selection(self):
        selected = self._selected_leftovers()
        # Ana paket(ler) kalıntı seçiminden bağımsız olarak zaten
        # kaldırılıyor - toplam buna da dahil edilmezse "gerçekte ne
        # kadar yer boşalacak" eksik/yanıltıcı gösteriliyordu (canlı
        # bildirildi: hiç kalıntı seçili değilken toplamda paketin
        # kendisi hiç yoktu).
        grand_total = self._pkg_total_size + sum(item.size for item in selected)
        size_text = format_size(grand_total)
        if size_text:
            label = _("{count} selected • {size}").format(
                count=len(selected), size=size_text
            )
        else:
            label = _("{count} selected").format(count=len(selected))
        self._selection_label.set_label(label)

    # ---------------- Kaldırma ----------------

    def _on_remove(self, _button, make_backup):
        if self._busy:
            return
        if (
            self._protected
            and prefs.get("protect_system")
            and not self._risk_acknowledged
        ):
            self._show_protection_warning(make_backup)
            return
        if make_backup:
            # Kilit, klasör seçme diyaloğu AÇILMADAN ÖNCE alınır - bu
            # diyalog asenkron (kullanıcı cevap verene kadar bu fonksiyon
            # çoktan geri dönmüş olur) ve kendisi tam o aralıkta
            # `self._busy`yi hiç değiştirmiyordu. GTK'nın kendi modal
            # ablukası normalde ikinci bir tıklamayı engeller, ama buna
            # körü körüne güvenmek yerine (bkz. canlı doğrulanan
            # yarış durumu) burada da kendi kilidimizi alıyoruz - ayrı
            # bir bayrak yerine mevcut `_busy`yi kullanmak daha az
            # değişiklik ve daha güvenli: zaten test edilmiş
            # `_set_busy()` hem düğmeleri hem "Vazgeç"i (bkz. Aşama 1,
            # madde 1) devre dışı bırakıyor, YENİ bir durum/değişken
            # eklemiyor. Kullanıcının bu noktada kaybettiği GERÇEK bir
            # yetenek yok: iyi davranan bir modal diyalogda "Vazgeç"e
            # zaten ulaşamıyordu; tek fark, diyalog kapanıp iptal/hata
            # dönünce kilidi KENDİMİZİN bırakması gerekmesi (aşağıda).
            self._set_busy(True)
            # Yedeğin nereye kaydedileceğini kullanıcı seçer
            dialog = Gtk.FileDialog(title=_("Choose where to save the backup"))
            dialog.set_initial_folder(
                Gio.File.new_for_path(os.path.expanduser("~"))
            )
            dialog.select_folder(self, None, self._on_backup_folder_chosen)
            return
        self._start(backup_base=None)

    def _show_protection_warning(self, make_backup):
        names = "\n".join(f"• {pkg.name}" for pkg in self._protected[:10])
        if len(self._protected) > 10:
            names += "\n…"
        dialog = Adw.AlertDialog(
            heading=_("⚠ These packages are vital to your system!"),
            body=_(
                "{names}\n\nRemoving them can leave your computer unable "
                "to start, show no display, or lose its network connection. "
                "Only continue if you know exactly what you are doing."
            ).format(names=names),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("proceed", _("Remove anyway (I accept the risk)"))
        dialog.set_response_appearance(
            "proceed", Adw.ResponseAppearance.DESTRUCTIVE
        )
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_dialog, response):
            if response == "proceed":
                self._risk_acknowledged = True
                self._on_remove(None, make_backup)

        dialog.connect("response", on_response)
        dialog.present(self)

    def _on_backup_folder_chosen(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            # Kullanıcı seçim penceresini kapattı YA DA diyalog bir hata
            # verdi (GLib.Error ikisini de aynı şekilde taşır) - her iki
            # durumda da _on_remove()'un yukarıda aldığı kilit GERİ
            # bırakılmalı, yoksa pencere kalıcı olarak "busy" kalır ve
            # kullanıcı bir daha hiç kaldırma başlatamaz.
            self._set_busy(False)
            return
        self._start(backup_base=folder.get_path())

    def _start(self, backup_base):
        self._set_busy(True)
        checked = self._selected_leftovers()

        groups: dict[str, list[str]] = {}
        for pkg in self._pkgs:
            groups.setdefault(pkg.source, []).append(pkg.id)

        removes_flatpak = any(pkg.source == "flatpak" for pkg in self._pkgs)
        # FlatpakBackend.list_packages(), yalnızca GERÇEKTEN çakışan
        # (hem sistem hem kullanıcı kurulumunda bulunan) uygulamaları
        # "app_id:kurulum" biçiminde etiketler (bkz. ilgili düzeltme).
        # _installed_flatpak_ids() ise HER ZAMAN çıplak app_id döner
        # ("flatpak list --columns=application" sonucu) - etiket hiç
        # soyulmazsa, kullanıcının BİLEREK kaldırdığı çift-kapsamlı bir
        # uygulama, aşağıdaki affected_flatpak_apps farkında yanlışlıkla
        # "yan etkiyle etkilendi" diye görünürdü. Flatpak uygulama
        # kimlikleri ":" hiç içermez (yalnızca harf/rakam/alt çizgi ve
        # son bileşende "-"), bu yüzden ":" öncesini almak güvenlidir.
        removed_flatpak_ids = {
            pkg.id.partition(":")[0]
            for pkg in self._pkgs if pkg.source == "flatpak"
        }

        def worker():
            before_flatpak = _installed_flatpak_ids() if removes_flatpak else set()
            backup_path = None
            if backup_base:
                try:
                    # Yedek, dosyalar henüz yerindeyken alınır
                    backup_path = create_backup(
                        self._pkgs, checked, base=backup_base
                    )
                except Exception as exc:
                    GLib.idle_add(self._on_backup_failed, str(exc))
                    return

            # Kaldırma "hiç kurulmamış gibi" olmalı — paket dosyaları
            # silinse bile uygulama arkada açık/kullanılabilir kalıyorsa
            # bu amaca ulaşılmamış demektir. Silmeden önce çalışan
            # süreçleri kapatmayı dene (reddetse bile kaldırma sürer).
            #
            # "packwarden" (SelfInstallBackend) BURADA ATLANIYOR: canlı
            # doğrulandı - bu paketin "çalışan süreci", PackWarden'ın
            # KENDİSİ. close_running() bunu bulup SIGTERM/SIGKILL
            # gönderirdi - yani PackWarden kendi kaldırma işlemi
            # SÜRERKEN kendini öldürürdü, aşağıdaki backend.remove()
            # döngüsüne (install.sh remove'u asıl çalıştıracak adım)
            # hiç ulaşamadan - hem kendisi hem de AYNI toplu seçimdeki
            # diğer tüm paketler kaldırılmadan kalırdı. install.sh
            # remove zaten kendi ilgili servislerini doğru şekilde
            # durduruyor, bu genel mekanizmaya hiç ihtiyacı yok.
            closed_running = [
                pkg.name for pkg in self._pkgs
                if pkg.source != "packwarden"
                and usage.close_running(pkg, self._main._launcher_map)
            ]

            # Her backend'in sonucu BAĞIMSIZ değerlendirilir - tek bir
            # paylaşılan "cancelled" bayrağı kullanmak, karma backend'li
            # bir toplu kaldırmada (ör. pacman + flatpak birlikte) BİR
            # backend'in pkexec'i reddedilince DİĞER backend'in gerçekten
            # başarmış olduğu kaldırmayı da "iptal edildi" diye raporlar,
            # ona ait kalıntıları hiç temizlemez ve pencereyi/listeyi
            # güncellemeden bırakırdı (canlı: RemoveResult.cancelled
            # yalnızca kendi backend'i için anlamlıdır, bkz. backends/base.py).
            errors = []
            cancelled_backend_ids: set[str] = set()
            succeeded_backend_ids: set[str] = set()
            for backend in self._main._backends:
                ids = groups.get(backend.id)
                if not ids:
                    continue
                result = backend.remove(ids)
                if result.cancelled:
                    cancelled_backend_ids.add(backend.id)
                elif not result.ok:
                    lines = [
                        line for line in result.output.strip().splitlines()
                        if not _WINE_NOISE_RE.match(line.strip())
                    ]
                    tail = "\n".join(lines[-8:]) if lines else _("No further details")
                    errors.append(f"{backend.display_name}:\n{tail}")
                else:
                    succeeded_backend_ids.add(backend.id)

            succeeded_pkgs = [
                pkg for pkg in self._pkgs if pkg.source in succeeded_backend_ids
            ]
            removed_count = len(succeeded_pkgs)
            cancelled_count = sum(
                1 for pkg in self._pkgs if pkg.source in cancelled_backend_ids
            )

            leftovers_to_remove = []
            leftover_errors = []
            if checked:
                # Tam başarıda (her şey kaldırıldı) kullanıcının işaretlediği
                # kalıntılar zaten doğru kümedir - gereksiz bir yeniden
                # tarama yapmaya değmez. Kısmi başarıda ise SADECE gerçekten
                # kaldırılan paketlere ait olduğu yeniden doğrulanan
                # kalıntılar silinir (bkz. _filter_leftovers_to_packages).
                if len(succeeded_pkgs) == len(self._pkgs):
                    leftovers_to_remove = checked
                else:
                    leftovers_to_remove = _filter_leftovers_to_packages(
                        checked, succeeded_pkgs
                    )
                if leftovers_to_remove:
                    leftover_errors = remove_leftovers(leftovers_to_remove)
            cleaned_count = len(leftovers_to_remove) - len(leftover_errors)

            affected_flatpak_apps = set()
            if removes_flatpak and "flatpak" in succeeded_backend_ids:
                _clean_unused_flatpak_data()
                after_flatpak = _installed_flatpak_ids()
                # Kendi kaldırdığımız paketler zaten "eksik" olacak — asıl
                # aranan, --unused/prune'un YAN ETKİ olarak götürdüğü,
                # kaldırma listemizde hiç olmayan uygulamalar.
                affected_flatpak_apps = (
                    before_flatpak - after_flatpak
                ) - removed_flatpak_ids

            GLib.idle_add(
                self._on_done,
                errors, leftover_errors, removed_count, cancelled_count,
                backup_path, cleaned_count,
                closed_running, affected_flatpak_apps,
            )

        threading.Thread(target=worker, daemon=True).start()

    def _on_backup_failed(self, message):
        self._set_busy(False)
        dialog = Adw.AlertDialog(
            heading=_("Backup failed — nothing was removed"),
            body=message,
        )
        dialog.add_response("ok", _("OK"))
        dialog.present(self)
        return GLib.SOURCE_REMOVE

    def _on_done(
        self, errors, leftover_errors, removed_count, cancelled_count,
        backup_path, cleaned, closed_running=(), affected_flatpak_apps=(),
    ):
        self._set_busy(False)

        if closed_running:
            self._main._toast_overlay.add_toast(Adw.Toast(
                title=_("Closed {count} running app(s) before removal").format(
                    count=len(closed_running)
                )
            ))
        if backup_path:
            self._main._toast_overlay.add_toast(
                Adw.Toast(title=_("Backup saved: {path}").format(path=backup_path))
            )

        if errors or leftover_errors:
            # Paket kaldırma hatası ile kalıntı silme hatası AYRI başlıklarla
            # gösterilir — paketler zaten başarıyla kaldırılmışken (sadece
            # bir kalıntı dosyası silinemediğinde) "paketler kaldırılamadı"
            # demek yanlış ve yanıltıcıdır.
            heading = (
                _("Some packages could not be removed")
                if errors else _("Some leftover files could not be removed")
            )
            dialog = Adw.AlertDialog(
                heading=heading,
                body="\n\n".join(errors + leftover_errors),
            )
            dialog.add_response("ok", _("OK"))
            dialog.present(self._main)
        elif removed_count == 0:
            # Hiçbir backend hata vermedi ama hiçbir şey de kaldırılamadı -
            # tek açıklama, en az birinin yetkilendirmesinin iptal
            # edilmesidir (errors boşken removed_count==0 olmasının tek yolu
            # bu, bkz. worker() döngüsü).
            self._main._toast_overlay.add_toast(
                Adw.Toast(title=_("Authorization was cancelled"))
            )
        elif affected_flatpak_apps:
            dialog = Adw.AlertDialog(
                heading=_("Installed apps were affected"),
                body=_(
                    "Cleaning up unused Flatpak data also removed these "
                    "installed apps as a side effect. You may want to "
                    "reinstall them:"
                ) + "\n\n" + "\n".join(sorted(affected_flatpak_apps)),
            )
            dialog.add_response("ok", _("OK"))
            dialog.present(self._main)
        else:
            if cancelled_count:
                # Kısmi başarı: bir kısmı kaldırıldı, bir kısmının
                # yetkilendirmesi iptal edildi - ikisini de ayrı ayrı
                # bildir, hiçbirini gizleme.
                title = _("Uninstalled {count} of {total} packages").format(
                    count=removed_count, total=len(self._pkgs)
                )
            else:
                title = _("Uninstalled {count} packages").format(
                    count=removed_count
                )
            self._main._toast_overlay.add_toast(Adw.Toast(title=title))
            if cancelled_count:
                self._main._toast_overlay.add_toast(Adw.Toast(
                    title=_("{count} removal(s) were cancelled").format(
                        count=cancelled_count
                    )
                ))
            if cleaned:
                if prefs.get("leftover_delete_mode") == "permanent":
                    title = _("Permanently deleted {count} leftover items").format(
                        count=cleaned
                    )
                else:
                    title = _("Moved {count} leftover items to Trash").format(
                        count=cleaned
                    )
                self._main._toast_overlay.add_toast(Adw.Toast(title=title))

        # Liste yenileme ve pencerenin kapanması artık HER durumda
        # (tam başarı / kısmi başarı / tam iptal / hata) tutarlı şekilde
        # çalışır - eskiden "cancelled" durumunda burası hiç çalışmıyordu,
        # bu da gerçekte kaldırılmış paketler listede eski görünmeye devam
        # ediyordu (bkz. karma backend + iptal senaryosu).
        self._main.refresh()
        self.close()
        return GLib.SOURCE_REMOVE

    def _set_busy(self, busy):
        self._busy = busy
        self._spinner.set_spinning(busy)
        self._backup_button.set_sensitive(not busy)
        self._nobackup_button.set_sensitive(not busy)
        # "Vazgeç" kaldırma başladıktan sonra da tıklanabilir kalıyordu ve
        # arka plandaki işlemi hiç durdurmadan sadece pencereyi kapatıyordu
        # (gerçek bir iptal MEKANİZMASI yok - backend.remove() zaten
        # başlamışsa geri alınamaz). Kullanıcıya "iptal ettim" yanılgısı
        # vermemek için, işlem sürerken düğme devre dışı bırakılır; spinner
        # zaten işlemin sürdüğünü gösteriyor, araç ipucu bunu netleştiriyor.
        self._cancel_button.set_sensitive(not busy)
        self._cancel_button.set_tooltip_text(
            _("Removal is in progress and cannot be stopped") if busy else ""
        )
