# Purga

Linux için toplu uygulama kaldırıcı — Windows'taki **Bulk Crap Uninstaller**'dan
esinlenildi. Tüm paket kaynaklarını tek pencerede listeler, arama/filtreleme/boyuta
göre sıralama sunar ve seçilenleri tek onayla topluca kaldırır.

## Desteklenen kaynaklar (v0.1.0)

| Kaynak  | Dağıtımlar                              |
|---------|------------------------------------------|
| pacman  | Arch, CachyOS, Manjaro, EndeavourOS      |
| APT     | Debian, Ubuntu, Mint, Pop!_OS            |
| DNF     | Fedora, RHEL, AlmaLinux, Rocky           |
| Flatpak | Hepsi                                    |

Mimari gereği yeni bir paket yöneticisi desteği eklemek,
`src/bulkuninstaller/backends/` altına tek dosya eklemekten ibaret.

## Geliştirme ortamında çalıştırma

```sh
sh run.sh
```

Betik, Flatpak sandbox'ı (ör. Flatpak VS Code) içinden çağrılırsa uygulamayı
`flatpak-spawn --host` ile ana sistemde başlatır.

Gereksinimler: Python ≥ 3.10, GTK4, libadwaita, PyGObject.

## Mimari

- `src/bulkuninstaller/host.py` — sandbox köprüsü: Flatpak içinde
  `flatpak-spawn --host`, kök yetkisi gerekince `pkexec` (polkit dialogu).
- `src/bulkuninstaller/backends/` — her paket yöneticisi için bir `Backend`
  alt sınıfı: `list_packages()` + `remove_argv()`.
- `src/bulkuninstaller/window.py` — GTK4/libadwaita arayüz; binlerce paketi
  akıcı göstermek için `Gtk.ListView` + `FilterListModel` + `SortListModel`.

## Flatpak olarak derleme

```sh
flatpak-builder --user --install --force-clean build-dir \
    build-aux/io.github.muhammethub111.Purga.yaml
flatpak run io.github.muhammethub111.Purga
```

## Flathub yayın yol haritası

1. **GitHub deposu aç** (ör. `MuhammetHub111/purga`), kodu it,
   `data/*.metainfo.xml` içindeki TODO adresleri gerçek depoyla güncelle.
2. Sürüm etiketle (`v0.1.0`) ve manifestteki `sources` bölümünü `type: dir`
   yerine `type: git` + `tag` olarak değiştir (Flathub yerel dizin kabul etmez).
3. Ekran görüntüleri çek, metainfo'ya `<screenshots>` ekle (Flathub zorunlu tutar).
4. `flatpak run --command=flatpak-builder-lint org.flatpak.Builder ...` ile
   manifest ve metainfo'yu doğrula.
5. [Flathub'a başvuru](https://docs.flathub.org/docs/for-app-authors/submission):
   `flathub/flathub` deposuna PR aç.
   - Not: `--talk-name=org.freedesktop.Flatpak` izni inceleme sırasında
     sorgulanır; bunun bir *sistem yönetim aracı* için zorunlu olduğunu
     PR açıklamasında gerekçelendir (benzer örnek: Warehouse, Flatseal).

## Sonraki sürümler (yol haritası)

- [x] Artık dosya temizliği: kaldırma sonrası `~/.config`, `~/.local/share`,
      `~/.cache`, `~/.var/app` kalıntılarını tespit edip onayla silme
- [ ] zypper (openSUSE), apk (Alpine), Snap, AppImage arka uçları
- [ ] Kaldırma sırasında canlı ilerleme/çıktı penceresi
- [ ] gettext ile çeviri altyapısı (ilk dil: Türkçe)
- [ ] "Yetim paketler" görünümü (hiçbir şeyin bağımlı olmadığı paketler)

## Lisans

GPL-3.0-or-later
