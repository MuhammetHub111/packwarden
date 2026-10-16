#!/bin/sh
# PackWarden installer.
# Install:  curl -fsSL https://raw.githubusercontent.com/MuhammetHub111/packwarden/main/install.sh | sh
# Remove:   sh ~/.local/share/packwarden/install.sh remove
set -e

REPO="https://github.com/MuhammetHub111/packwarden"
DIR="$HOME/.local/share/packwarden"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
SYSTEMD_USER="$HOME/.config/systemd/user"
USAGE_SERVICE="packwarden-usage-daemon.service"

desktop_dir() {
    if command -v xdg-user-dir >/dev/null 2>&1; then
        xdg-user-dir DESKTOP
    else
        echo "$HOME/Desktop"
    fi
}

if [ "$1" = "remove" ] || [ "$1" = "--remove" ]; then
    if command -v systemctl >/dev/null 2>&1; then
        systemctl --user disable --now "$USAGE_SERVICE" >/dev/null 2>&1 || true
    fi
    rm -rf "$DIR" "$BIN/packwarden" "$BIN/packwarden-usage-daemon" \
        "$APPS/io.github.muhammethub111.PackWarden.desktop" \
        "$SYSTEMD_USER/$USAGE_SERVICE"
    rm -f "$(desktop_dir)/packwarden.desktop" "$(desktop_dir)/io.github.muhammethub111.PackWarden.desktop"
    command -v systemctl >/dev/null 2>&1 && systemctl --user daemon-reload 2>/dev/null || true
    command -v kbuildsycoca6 >/dev/null 2>&1 && kbuildsycoca6 2>/dev/null || true
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" 2>/dev/null || true
    echo "PackWarden removed."
    exit 0
fi

echo "Installing PackWarden..."

if command -v curl >/dev/null 2>&1; then
    GET="curl -fsSL"
elif command -v wget >/dev/null 2>&1; then
    GET="wget -qO-"
else
    echo "Error: curl or wget is required." >&2
    exit 1
fi

if ! python3 -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')" 2>/dev/null; then
    echo "Error: Python 3 with GTK4 and libadwaita (PyGObject) is required." >&2
    echo "Install it with your package manager, for example:" >&2
    echo "  Arch:   sudo pacman -S python-gobject gtk4 libadwaita" >&2
    echo "  Debian: sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1" >&2
    echo "  Fedora: sudo dnf install python3-gobject gtk4 libadwaita" >&2
    exit 1
fi

rm -rf "$DIR"
mkdir -p "$DIR" "$BIN" "$APPS"
$GET "$REPO/archive/refs/heads/main.tar.gz" | tar -xz -C "$DIR" --strip-components=1

cat > "$BIN/packwarden" << LAUNCHER
#!/bin/sh
# Bu betik, PackWarden'in kendi Python kodu HENUZ calismadan once
# calisir - bu yuzden kurtarma mantigi burada, saf POSIX shell'de.
# Bir guncelleme iki "rename" arasinda kesilirse (surec oldurulur,
# guc kesilir), DIR kaybolup BACKUP_DIR saglam kalabilir; DIR icindeki
# Python kodu (updater.py) bu durumda hic calisma sansi bulamaz,
# cunku calismasi gereken kod da DIR'in icinde kaybolmustur.
DIR="$DIR"
BACKUP_DIR="\$DIR.update-backup"

is_valid_install() {
    [ -f "\$1/install.sh" ] && [ -d "\$1/src/bulkuninstaller" ]
}

if ! is_valid_install "\$DIR"; then
    if is_valid_install "\$BACKUP_DIR"; then
        # Bozuk hedefi DOGRUDAN silmek yerine benzersiz bir karantina
        # adina tasi - geri alinabilir kalsin.
        quarantine=""
        if [ -e "\$DIR" ] || [ -L "\$DIR" ]; then
            quarantine="\$DIR.broken-\$\$"
            mv "\$DIR" "\$quarantine" 2>/dev/null
        fi
        if mv "\$BACKUP_DIR" "\$DIR" 2>/dev/null; then
            echo "PackWarden: an interrupted update was detected and the previous version was restored." >&2
        else
            echo "PackWarden: could not restore the previous version from \$BACKUP_DIR. Please check it manually." >&2
            [ -n "\$quarantine" ] && mv "\$quarantine" "\$DIR" 2>/dev/null
            exit 1
        fi
    else
        echo "PackWarden: the install at \$DIR is missing or broken, and no usable backup was found." >&2
        echo "Reinstall with: curl -fsSL https://raw.githubusercontent.com/MuhammetHub111/packwarden/main/install.sh | sh" >&2
        exit 1
    fi
fi

exec env PYTHONPATH="\$DIR/src" python3 -m bulkuninstaller "\$@"
LAUNCHER
chmod +x "$BIN/packwarden"

cat > "$BIN/packwarden-usage-daemon" << LAUNCHER
#!/bin/sh
exec env PYTHONPATH="$DIR/src" python3 -m bulkuninstaller.usage_daemon "\$@"
LAUNCHER
chmod +x "$BIN/packwarden-usage-daemon"

# Arkaplan kullanım algılama servisi: birim dosyası burada yazılır ama
# hiç etkinleştirilmez — yalnızca Ayarlar > Gizlilik'ten bilerek
# açıldığında systemctl ile devreye girer (bkz. usage_service.py).
mkdir -p "$SYSTEMD_USER"
cat > "$SYSTEMD_USER/$USAGE_SERVICE" << SERVICE
[Unit]
Description=PackWarden local app-usage detector (optional, off by default)

[Service]
ExecStart=$BIN/packwarden-usage-daemon
Restart=on-failure

[Install]
WantedBy=default.target
SERVICE
command -v systemctl >/dev/null 2>&1 && systemctl --user daemon-reload 2>/dev/null || true

cat > "$APPS/io.github.muhammethub111.PackWarden.desktop" << DESKTOP
[Desktop Entry]
Type=Application
Name=PackWarden
Comment=Uninstall applications in bulk from every package manager
Comment[tr]=Tüm paket yöneticilerinden uygulamaları toplu kaldırın
Exec=$BIN/packwarden
Icon=$DIR/data/icons/io.github.muhammethub111.PackWarden.svg
Terminal=false
Categories=System;Utility;
Keywords=uninstall;remove;package;bulk;cleaner;kaldır;
DESKTOP

command -v kbuildsycoca6 >/dev/null 2>&1 && kbuildsycoca6 2>/dev/null || true
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" 2>/dev/null || true

# Masaüstü kısayolu sorusu: curl | sh ile bile klavyeden okuyabilmek
# için /dev/tty kullanılır; etkileşimli değilse sessizce atlanır
REPLY=""
if { printf "Add a desktop icon? [y/N] " && read -r REPLY; } < /dev/tty > /dev/tty 2>/dev/null; then :; fi
case "$REPLY" in
    [yYeE]*)
        DESK="$(desktop_dir)"
        mkdir -p "$DESK"
        cp "$APPS/io.github.muhammethub111.PackWarden.desktop" "$DESK/io.github.muhammethub111.PackWarden.desktop"
        chmod +x "$DESK/io.github.muhammethub111.PackWarden.desktop" 2>/dev/null || true
        echo "Desktop icon added."
        ;;
esac

echo ""
echo "Done! PackWarden is in your application menu."
echo "You can also start it with: $BIN/packwarden"
echo "To remove it later: sh $DIR/install.sh remove"
