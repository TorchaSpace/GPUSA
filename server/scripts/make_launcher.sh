#!/usr/bin/env bash
# Masaüstüne iki simge koyar:
#   "GPUSA Sunucusu"  -> sadece sunucu (start_server.sh)
#   "GPUSA Hepsi"     -> sunucu + Admin + POS + Depo bu bilgisayarda (../../start_all.sh)
set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
chmod +x "$SCRIPT_DIR/start_server.sh" "$REPO_DIR/start_all.sh"
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

ALL="$DESK/GPUSA-Hepsi.desktop"
cat > "$ALL" <<DESKTOP
[Desktop Entry]
Type=Application
Name=GPUSA Hepsi
Comment=Sunucu + Admin + POS + Depo
Exec=bash "$REPO_DIR/start_all.sh"
Terminal=true
Icon=applications-office
Categories=Office;
DESKTOP
chmod +x "$ALL"
gio set "$ALL" metadata::trusted true 2>/dev/null || true
echo "Hazır: $FILE"
echo "Hazır: $ALL"
echo "İlk çift tıklamada 'Çalıştırmaya izin ver / Trust and Launch' sorarsa onayla."
