#!/usr/bin/env bash
# Masaüstüne "GPUSA Sunucusu" simgesi koyar: çift tıklayınca start_server.sh bir terminalde açılır.
set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
chmod +x "$SCRIPT_DIR/start_server.sh"
DESK="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
[ -d "${DESK:-}" ] || DESK="$HOME/Desktop"
mkdir -p "$DESK"
FILE="$DESK/GPUSA-Sunucusu.desktop"
cat > "$FILE" <<DESKTOP
[Desktop Entry]
Type=Application
Name=GPUSA Sunucusu
Comment=Docker, veritabanı ve API'yi başlatır
Exec=bash "$SCRIPT_DIR/start_server.sh"
Terminal=true
Icon=utilities-terminal
Categories=Development;
DESKTOP
chmod +x "$FILE"
# GNOME çift tıklamaya izin vermek için "güvenilir" işareti ister; KDE'de gerekmez.
gio set "$FILE" metadata::trusted true 2>/dev/null || true
echo "Hazır: $FILE"
echo "İlk çift tıklamada 'Çalıştırmaya izin ver / Trust and Launch' sorarsa onayla."
