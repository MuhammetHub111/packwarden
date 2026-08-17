#!/bin/sh
# PackWarden installer.
# Install:  curl -fsSL https://raw.githubusercontent.com/MuhammetHub111/packwarden/main/install.sh | sh
# Remove:   sh ~/.local/share/packwarden/install.sh remove
set -e

REPO="https://github.com/MuhammetHub111/packwarden"
DIR="$HOME/.local/share/packwarden"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"

desktop_dir() {
    if command -v xdg-user-dir >/dev/null 2>&1; then
        xdg-user-dir DESKTOP
    else
        echo "$HOME/Desktop"
    fi
}

if [ "$1" = "remove" ] || [ "$1" = "--remove" ]; then
    rm -rf "$DIR" "$BIN/packwarden" "$APPS/io.github.muhammethub111.PackWarden.desktop"
    rm -f "$(desktop_dir)/packwarden.desktop" "$(desktop_dir)/io.github.muhammethub111.PackWarden.desktop"
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
exec env PYTHONPATH="$DIR/src" python3 -m bulkuninstaller "\$@"
LAUNCHER
chmod +x "$BIN/packwarden"

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
