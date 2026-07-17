# PackWarden 🛡️📦

PackWarden is a bulk application manager for Linux. It shows all the
applications installed on your system in one window and lets you remove
the ones you no longer need, cleanly and safely, on any distribution.

## Screenshots

| Main window |
|---|
| ![Main window](data/screenshots/main.png) |

| Removal with leftover cleanup | Settings |
|---|---|
| ![Removal confirmation](data/screenshots/removal.png) | ![Settings](data/screenshots/settings.png) |

## Features

- Lists installed applications from eleven package sources: pacman,
  APT, DNF, Zypper, APK, XBPS, Portage, Nix, Flatpak, Snap and
  AppImage. Sources are detected automatically.
- Removes many applications at once. Select them with Ctrl, Shift or
  by dragging, then confirm once.
- Finds the leftover settings, caches and data of removed applications
  and deletes only the ones you tick.
- Saves a backup to a folder you choose before anything is deleted.
- Warns you before critical system packages are touched, so a cleanup
  cannot break your computer.
- Shows real application icons, publishers, repositories and sizes.
- Has a right-click menu to uninstall, launch or inspect any
  application.
- Speaks English and Turkish.

## What makes it different

Most tools cover only one part of this job. App stores remove one
application at a time. Warehouse manages only Flatpak. BleachBit cleans
files but does not uninstall applications. PackWarden combines all of
it in one window: every package source, bulk removal, leftover cleanup,
backups and system protection together.

## Installation

This command installs the application:

```sh
curl -fsSL https://raw.githubusercontent.com/MuhammetHub111/packwarden/main/install.sh | sh
```

After the installation, open your application menu and search for
PackWarden.

This command removes the application:

```sh
sh ~/.local/share/packwarden/install.sh remove
```

A Flathub release is planned...

## License

GPL-3.0-or-later
