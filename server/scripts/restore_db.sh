#!/usr/bin/env bash
# GPUSA yedeğini geri yükler. DİKKAT: şu anki veritabanının yerine geçer.
#
#   bash server/scripts/restore_db.sh ~/gpusa-backups/gpusa-2026-11-05_1200.dump
#
# Önce sunucuyu (API) durdur. Geri yüklemeden hemen önce mevcut hâlin de yedeğini alır.
set -eu
FILE="${1:-}"
[ -f "$FILE" ] || { echo "Yedek dosyası ver: bash $0 <dosya.dump>"; exit 1; }
CONTAINER="${GPUSA_DB_CONTAINER:-gpusa-db}"
DB="${GPUSA_DB_NAME:-gpusa}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
read -r -p "'$DB' veritabanı '$FILE' ile DEĞİŞTİRİLECEK. Devam için EVET yaz: " answer
[ "$answer" = "EVET" ] || { echo "Vazgeçildi."; exit 1; }
bash "$HERE/backup_db.sh"   # geri dönüş yolu
docker exec -i "$CONTAINER" pg_restore -U postgres -d "$DB" --clean --if-exists --no-owner < "$FILE"
echo "Geri yüklendi. Sunucuyu yeniden başlat."
