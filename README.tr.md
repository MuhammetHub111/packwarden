# PackWarden 🛡️📦

**Tüm Linux dağıtımları için toplu uygulama yöneticisi.** Windows'taki
Bulk Crap Uninstaller'dan esinlenildi: her paket kaynağındaki kurulu
uygulamalar tek pencerede — ara, boyuta göre sırala, çoklu seçimle
topluca kaldır, kalıntıları temizle, yedeğini al. Türkçe ve İngilizce.

**🌐 Dil:** [English](https://github.com/MuhammetHub111/packwarden#readme) · [Türkçe](https://github.com/MuhammetHub111/packwarden/blob/main/README.tr.md)

![PackWarden ana pencere](data/screenshots/main.png)

| Kalıntı temizlikli kaldırma | Ayarlar |
|---|---|
| ![Kaldırma onayı](data/screenshots/removal.png) | ![Ayarlar](data/screenshots/settings.png) |

## Özellikler

- **Tüm kaynaklar tek listede** — açılışta otomatik algılanır, boş
  kaynaklar kendini gizler: pacman (Arch/CachyOS), APT (Debian/Ubuntu),
  DNF (Fedora), Zypper (openSUSE), APK (Alpine), XBPS (Void), Portage
  (Gentoo), Nix (NixOS) + her yerde Flatpak, Snap ve AppImage
- **Depo bazlı filtreler** — paket sayılarıyla (core, extra, AUR,
  flathub…) ve her uygulama için yayıncı bilgisi
- **Toplu kaldırma** — tık, Ctrl+tık, Shift+tık veya fareyle çekerek
  seç; tek onayla hepsi gider
- **Kalıntı temizliği** — kaldırılan uygulamaların ayar, önbellek ve
  veri klasörleri bulunur, boyutlarıyla listelenir; ancak senin tikinle
  silinir
- **Silmeden önce yedek** — paket listesi + ayar arşivi seçtiğin
  klasöre kaydedilir; yedek alınamazsa hiçbir şey silinmez
- **Sistem koruma kalkanı** — çekirdek, glibc, önyükleyici, ekran
  sürücüsü gibi 58+ kritik paket ek uyarı ister; toplu temizlik
  sistemini asla bozamaz
- **Sağ tık menüsü** — kaldır, çalıştır, kimlik kopyala, kalıntı sil,
  özellikler
- **"Sadece uygulamalar" görünümü** — kütüphaneleri varsayılan olarak
  gizler (1365 paket ~100 gerçek uygulamaya iner); Ayarlar'dan
  açılıp kapanır
- **GTK4 + libadwaita** arayüz: gerçek uygulama simgeleri, orta tuşla
  otomatik kaydırma, Türkçe/İngilizce

## Kaynaktan çalıştırma

```sh
git clone https://github.com/MuhammetHub111/packwarden.git
cd packwarden
sh run.sh
```

Gereksinimler: Python ≥ 3.10, GTK4, libadwaita, PyGObject — modern
GNOME/KDE sistemlerinde hazır gelir.

## Lisans

GPL-3.0-or-later
