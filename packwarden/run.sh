#!/bin/sh
# Geliştirme başlatıcısı. Sandbox (ör. Flatpak VS Code) içinden çağrılırsa
# uygulamayı ana sistemde başlatır; aksi halde doğrudan çalıştırır.
DIR="$(cd "$(dirname "$0")" && pwd)"

if [ -f /.flatpak-info ]; then
    # flatpak-spawn --host, sandbox kabuğunun ortam değişkenlerini ana
    # sisteme otomatik taşımaz (ör. PACKWARDEN_DEV=1 ./run.sh burada
    # kaybolur); bu yüzden aktarılacak değişkenler env'e açıkça verilir.
    exec flatpak-spawn --host env \
        PYTHONPATH="$DIR/src" \
        PACKWARDEN_DEV="$PACKWARDEN_DEV" \
        python3 -m bulkuninstaller "$@"
fi
exec env PYTHONPATH="$DIR/src" python3 -m bulkuninstaller "$@"
