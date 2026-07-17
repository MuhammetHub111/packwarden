"""Basit çeviri katmanı.

Sistem dili Türkçe ise arayüz tamamen Türkçe görünür; diğer dillerde
İngilizce kalır. İleride gettext'e geçilecek; sözlük yapısı o geçişi
kolaylaştırmak için gettext ile aynı mantıkta kuruldu.
"""

import os

TR = {
    "Search packages…": "Paket ara…",
    "Refresh package list": "Paket listesini yenile",
    "About PackWarden": "PackWarden Hakkında",
    "Quit": "Çık",
    "All sources": "Tüm kaynaklar",
    "{name} — all": "{name} — tümü",
    "Sort by name": "Ada göre sırala",
    "Sort by size": "Boyuta göre sırala",
    "Loading packages…": "Paketler yükleniyor…",
    "Reading installed packages from every source":
        "Tüm kaynaklardan kurulu paketler okunuyor",
    "{shown} of {total} packages": "{total} paketten {shown} tanesi",
    "{count} selected": "{count} seçildi",
    "Uninstall {count} selected…": "Seçilen {count} uygulamayı kaldır…",
    "Uninstall {count} packages?": "{count} paket kaldırılsın mı?",
    "Cancel": "Vazgeç",
    "Uninstall": "Kaldır",
    "Some packages could not be removed": "Bazı paketler kaldırılamadı",
    "OK": "Tamam",
    "Authorization was cancelled": "Yetkilendirme iptal edildi",
    "Uninstalled {count} packages": "{count} paket kaldırıldı",
    "Backup failed — nothing was removed":
        "Yedek oluşturulamadı — hiçbir şey kaldırılmadı",
    "Backup saved: {path}": "Yedek kaydedildi: {path}",
    "No leftover files found": "Kalıntı dosya bulunamadı",
    "Deleted {count} leftover items": "{count} kalıntı silindi",
    "Settings": "Ayarlar",
    "General": "Genel",
    "Package list": "Paket listesi",
    "Safety": "Güvenlik",
    "Protect system packages": "Sistem paketlerini koru",
    "Shows an extra warning before removing packages your system needs to run":
        "Sistemin çalışması için gereken paketleri silmeden önce ek uyarı gösterir",
    "critical system package": "kritik sistem paketi",
    "⚠ These packages are vital to your system!":
        "⚠ Bu paketler sistemin çalışması için hayati!",
    "{names}\n\nRemoving them can leave your computer unable to start, "
    "show no display, or lose its network connection. Only continue if "
    "you know exactly what you are doing.":
        "{names}\n\nBunlar silinirse bilgisayarın açılmayabilir, görüntü "
        "gelmeyebilir veya internet bağlantın kopabilir. Yalnızca ne "
        "yaptığından kesinlikle eminsen devam et.",
    "Remove anyway (I accept the risk)": "Yine de kaldır (riski kabul ediyorum)",
    "Language": "Dil",
    "Interface language": "Arayüz dili",
    "Automatic (system)": "Otomatik (sistem)",
    "Takes effect after restarting the app":
        "Uygulama yeniden başlatılınca uygulanır",
    "Show applications only": "Sadece uygulamaları göster",
    "Hides libraries and system packages":
        "Kütüphaneleri ve sistem paketlerini gizler",
    "Select all": "Tümünü seç",
    "{count} selected • {size}": "{count} seçildi • {size}",
    "Confirm Removal": "Kaldırma Onayı",
    "Packages to remove": "Kaldırılacak paketler",
    "{count} packages • {size}": "{count} paket • {size}",
    "Scanning leftovers…": "Kalıntılar taranıyor…",
    "These packages keep no extra files behind":
        "Bu paketler geride ek dosya bırakmıyor",
    "Back up and Remove": "Yedekle ve Kaldır",
    "Remove without Backup": "Yedeksiz Kaldır",
    "Choose where to save the backup": "Yedeğin kaydedileceği yeri seç",
    "Uninstall…": "Kaldır…",
    "Launch": "Çalıştır",
    "Copy package ID": "Paket kimliğini kopyala",
    "Delete leftover files…": "Kalıntı dosyalarını sil…",
    "Properties": "Özellikler",
    "{name} has no launchable window": "{name} çalıştırılabilir pencereye sahip değil",
    "Launching {name}…": "{name} başlatılıyor…",
    "Could not launch {name}": "{name} başlatılamadı",
    "Copied: {text}": "Kopyalandı: {text}",
    "Delete leftovers of {name}?": "{name} kalıntıları silinsin mi?",
    "Total: {size}": "Toplam: {size}",
    "Delete": "Sil",
    "Package ID": "Paket kimliği",
    "Version": "Sürüm",
    "Size": "Boyut",
    "Source": "Kaynak",
    "Repository": "Depo",
    "Publisher": "Yayıncı",
    "Description": "Açıklama",
    "Cache": "Önbellek",
    "App data": "Uygulama verileri",
    "State logs": "Durum kayıtları",
    "Flatpak data": "Flatpak verileri",
    "Updates": "Güncellemeler",
    "A newer version is ready. Restart to use it.":
        "Daha yeni bir sürüm hazır. Kullanmak için yeniden başlat.",
    "You are running the latest version.": "En güncel sürümü kullanıyorsun.",
    "Running version": "Çalışan sürüm",
    "Latest version on disk": "Diskteki en son sürüm",
    "Update and Restart": "Güncelle ve Yeniden Başlat",
    "Restart App": "Uygulamayı Yeniden Başlat",
    "Batch uninstaller for every Linux distribution. Lists packages from "
    "pacman, APT, DNF and Flatpak, and removes them in bulk.":
        "Tüm Linux dağıtımları için toplu uygulama kaldırıcı. pacman, APT, "
        "DNF ve Flatpak paketlerini tek listede gösterir ve topluca kaldırır.",
}


def _detect_lang() -> str:
    # Kullanıcı Ayarlar'dan dil seçtiyse o kazanır; "auto" sistem diline bakar
    try:
        from . import prefs
        chosen = prefs.get("language")
        if chosen in ("tr", "en"):
            return chosen
    except Exception:
        pass
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var)
        if value:
            return value.split(":")[0].split(".")[0].split("_")[0].lower()
    return "en"


_LANG = _detect_lang()


def _(text: str) -> str:
    if _LANG == "tr":
        return TR.get(text, text)
    return text
