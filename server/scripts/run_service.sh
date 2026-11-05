#!/usr/bin/env bash
# GPUSA sunucusunu pencere olmadan çalıştırır (systemd servisi bunu açar; elle de denenebilir).
# Docker'daki Postgres'in hazır olmasını bekler, tabloları günceller, API'yi ön planda çalıştırır.
# Ayarlar server/.env dosyasından gelir (DATABASE_URL, GPUSA_JWT_SECRET, GPUSA_SETUP_CODE, GPUSA_BACKUP_*...).
set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONTAINER="${GPUSA_DB_CONTAINER:-gpusa-db}"
PORT="${GPUSA_PORT:-8000}"
VENV="${GPUSA_VENV:-$SERVER_DIR/.venv}"
cd "$SERVER_DIR"
set -a; . ./.env; set +a
export GPUSA_PORT="$PORT"

if [ "${GPUSA_SKIP_DOCKER:-0}" != "1" ]; then
    for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 2; done
    docker info >/dev/null 2>&1 || { echo "Docker açılmadı"; exit 1; }
    docker start "$CONTAINER" >/dev/null 2>&1 || true
    for _ in $(seq 1 60); do docker exec "$CONTAINER" pg_isready -U postgres >/dev/null 2>&1 && break; sleep 2; done
    docker exec "$CONTAINER" pg_isready -U postgres >/dev/null 2>&1 || { echo "Postgres hazır olmadı"; exit 1; }
    bash "$SCRIPT_DIR/backup_db.sh" --daily || echo "Yedek alınamadı (devam ediliyor)"
fi
"$VENV/bin/alembic" upgrade head
exec "$VENV/bin/uvicorn" gpusa_server.main:app --host 0.0.0.0 --port "$PORT"
