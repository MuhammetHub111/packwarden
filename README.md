# PackWarden 🛡️📦

PackWarden is a bulk application manager for Linux. It shows all the
applications installed on your system in one window and lets you remove
the ones you no longer need, cleanly and safely, on any distribution.

| Main window |
|---|
| ![PackWarden main window](data/screenshots/main.png) |

| Removal with leftover cleanup | Settings |
|---|---|
| ![Removal confirmation](data/screenshots/removal.png) | ![Settings](data/screenshots/settings.png) |

## Installation

One command installs PackWarden and adds it to your application menu:

```sh
curl -fsSL https://raw.githubusercontent.com/MuhammetHub111/packwarden/main/install.sh | sh
```

That is all. Open your application menu and search for PackWarden.

To remove it later:

```sh
sh ~/.local/share/packwarden/install.sh remove
```

A Flathub release is planned.

## License

GPL-3.0-or-later
