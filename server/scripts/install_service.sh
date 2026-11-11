#!/usr/bin/env bash
# Sunucu PC açılınca GPUSA sunucusunu (Docker + Postgres + API) kendiliğinden başlatır ve her gün yedek alır.
#
#   sudo bash server/scripts/install_service.sh          # kur ve aç
#   sudo bash server/scripts/install_service.sh remove   # kaldır
#
# Kurulunca: PC açılır -> Docker açılır -> veritabanı -> API. Durum: systemctl status gpusa-server
# Kayıtlar: journalctl -u gpusa-server -f
set -eu
[ "$(id -u)" = "0" ] || { echo "sudo ile çalıştır: sudo bash $0"; exit 1; }
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RUN_USER="${SUDO_USER:-root}"
UNIT=/etc/systemd/system

if [ "${1:-}" = "remove" ]; then
    systemctl disable --now gpusa-server.service gpusa-backup.timer 2>/dev/null || true
    rm -f $UNIT/gpusa-server.service $UNIT/gpusa-backup.service $UNIT/gpusa-backup.timer
    systemctl daemon-reload; echo "Kaldırıldı."; exit 0
fi
[ -f "$SERVER_DIR/.env" ] || { echo "$SERVER_DIR/.env yok; önce bash server/scripts/start_server.sh ile bir kez kur"; exit 1; }
[ -x "$SERVER_DIR/.venv/bin/uvicorn" ] || { echo "$SERVER_DIR/.venv yok; önce bash server/scripts/start_server.sh ile bir kez kur"; exit 1; }
if pgrep -u "$RUN_USER" -f "uvicorn gpusa_server|gpusa_server.run" >/dev/null 2>&1; then
    echo "UYARI: elle açılmış bir sunucu çalışıyor; kurulumdan sonra onu kapat (port çakışır)."
fi

cat > $UNIT/gpusa-server.service <<EOF
[Unit]
Description=GPUSA sunucusu (API + Postgres)
After=network-online.target docker.service
Wants=network-online.target docker.service

[Service]
User=$RUN_USER
ExecStart=/usr/bin/env bash $SCRIPT_DIR/run_service.sh
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
cat > $UNIT/gpusa-backup.service <<EOF
[Unit]
Description=GPUSA günlük yedek

[Service]
Type=oneshot
User=$RUN_USER
ExecStart=/usr/bin/env bash -c 'set -a; . $SERVER_DIR/.env; set +a; exec bash $SCRIPT_DIR/backup_db.sh'
EOF
cat > $UNIT/gpusa-backup.timer <<EOF
[Unit]
Description=GPUSA yedeği her gün 03:00

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
EOF
systemctl enable --now docker.service
systemctl daemon-reload
systemctl enable --now gpusa-server.service gpusa-backup.timer
echo "Kuruldu. PC her açıldığında sunucu kendiliğinden başlar."
echo "Durum: systemctl status gpusa-server   |   Kayıt: journalctl -u gpusa-server -f"
