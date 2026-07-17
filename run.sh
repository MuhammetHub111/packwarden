#!/bin/sh
# Geliştirme başlatıcısı. Sandbox (ör. Flatpak VS Code) içinden çağrılırsa
# uygulamayı ana sistemde başlatır; aksi halde doğrudan çalıştırır.
DIR="$(cd "$(dirname "$0")" && pwd)"

if [ -f /.flatpak-info ]; then
    exec flatpak-spawn --host env PYTHONPATH="$DIR/src" python3 -m bulkuninstaller "$@"
fi
exec env PYTHONPATH="$DIR/src" python3 -m bulkuninstaller "$@"
