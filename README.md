<a id="english"></a>

**🌐 Language:** [English](#english) · [Türkçe](#turkce)

# PackWarden 🛡️📦

**Bulk application manager for every Linux distribution.** Inspired by
Bulk Crap Uninstaller on Windows: every installed application from every
package source in one window — search it, sort it by size, multi-select
and remove in bulk, clean up leftovers, and keep backups.

![PackWarden main window](data/screenshots/main.png)

| Removal with leftover cleanup | Settings |
|---|---|
| ![Removal confirmation](data/screenshots/removal.png) | ![Settings](data/screenshots/settings.png) |

## Features

- **Every package source, one list** — backends are auto-detected at
  startup and empty ones hide themselves:

  | Backend | Distributions |
  |---------|---------------|
  | pacman | Arch, CachyOS, Manjaro, EndeavourOS |
  | APT | Debian, Ubuntu, Mint, Pop!_OS |
  | DNF | Fedora, RHEL, AlmaLinux, Rocky |
  | Zypper | openSUSE |
  | APK | Alpine |
  | XBPS | Void |
  | Portage | Gentoo |
  | Nix | NixOS |
  | Flatpak / Snap / AppImage | everywhere |

- **Repository-aware filters** with package counts (core, extra, AUR,
  flathub, …) and a publisher column for every app
- **Bulk removal** — click, Ctrl+click, Shift+click or rubber-band drag
  to select; one confirmation removes them all
- **Leftover cleanup** — config, cache and data directories belonging to
  the removed apps are found, listed with sizes and deleted only with
  your tick of approval
- **Backups before removal** — package list + settings archive saved to
  a folder you choose; nothing is removed if the backup fails
- **System protection shield** — 58+ critical packages (kernel, glibc,
  bootloader, display driver…) trigger an extra warning so a bulk sweep
  can never brick your machine
- **Right-click menu** — uninstall, launch, copy package ID, delete
  leftovers, properties
- **Apps-only view** — hides libraries and system packages by default;
  toggle it in Settings
- **GTK4 + libadwaita** UI with real application icons, middle-click
  autoscroll, English/Turkish interface

## Running from source

```sh
git clone https://github.com/MuhammetHub111/packwarden.git
cd packwarden
sh run.sh
```

Requirements: Python ≥ 3.10, GTK4, libadwaita, PyGObject — all present
by default on modern GNOME/KDE systems. The script detects Flatpak
sandboxes (e.g. VS Code) and escapes to the host automatically.

## Building the Flatpak

```sh
flatpak run org.flatpak.Builder --user --install --force-clean \
    build-dir build-aux/io.github.muhammethub111.PackWarden.yaml
flatpak run io.github.muhammethub111.PackWarden
```

## Architecture

- `src/bulkuninstaller/host.py` — sandbox bridge: commands run on the
  host via `flatpak-spawn --host`, privileged ones through `pkexec`
  (polkit dialog)
- `src/bulkuninstaller/backends/` — one `Backend` subclass per package
  manager; adding a new distro means adding one file
- `src/bulkuninstaller/window.py` — GTK4 UI built on `Gtk.ListView`
  with filter/sort models, smooth with thousands of rows

## License

GPL-3.0-or-later

---

<a id="turkce"></a>

**🌐 Dil:** [English](#english) · [Türkçe](#turkce)

# PackWarden 🛡️📦 (Türkçe)

**Tüm Linux dağıtımları için toplu uygulama yöneticisi.** Windows'taki
Bulk Crap Uninstaller'dan esinlenildi: her paket kaynağındaki kurulu
uygulamalar tek pencerede — ara, boyuta göre sırala, çoklu seçimle
topluca kaldır, kalıntıları temizle, yedeğini al.

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
  gizler; Ayarlar'dan açılıp kapanır
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
