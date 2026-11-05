#!/usr/bin/env bash
# GPUSA veritabanı yedeği (sunucu PC, Docker'daki Postgres).
#
#   bash server/scripts/backup_db.sh            # ~/gpusa-backups/gpusa-YYYY-MM-DD_HHMM.dump yazar
#   bash server/scripts/backup_db.sh --daily    # bugün zaten yedek varsa hiçbir şey yapmaz
#
# Son 14 yedek tutulur, eskileri silinir. Yedek dosyası PIN özetleri ve tüm iş verisini içerir:
# başkasıyla paylaşma, USB/başka bilgisayara kopyalarken dikkat et.
#
# Ortam değişkenleri (hepsi isteğe bağlı):
#   GPUSA_DB_CONTAINER  Postgres kapsayıcısı (varsayılan gpusa-db)
#   GPUSA_DB_NAME       veritabanı adı (varsayılan gpusa)
#   GPUSA_BACKUP_DIR    yedek klasörü (varsayılan ~/gpusa-backups)
#   GPUSA_BACKUP_KEEP   kaç yedek tutulsun (varsayılan 14)
#   GPUSA_BACKUP_COPY_DIR  ikinci kopya klasörü, örn. USB/ikinci disk (/run/media/ali/YEDEK/gpusa). Klasör yoksa
#                          (USB takılı değilse) uyarı verir, yedek yine de alınmış sayılır.
#   GPUSA_BACKUP_RSYNC     ikinci kopya için uzak adres, örn. kullanici@baska-pc:/yedekler/gpusa/ (ssh anahtarı gerekir)
set -eu
CONTAINER="${GPUSA_DB_CONTAINER:-gpusa-db}"
DB="${GPUSA_DB_NAME:-gpusa}"
DIR="${GPUSA_BACKUP_DIR:-$HOME/gpusa-backups}"
KEEP="${GPUSA_BACKUP_KEEP:-14}"
mkdir -p "$DIR"; chmod 700 "$DIR"
if [ "${1:-}" = "--daily" ] && ls "$DIR"/gpusa-"$(date +%F)"_*.dump >/dev/null 2>&1; then
    echo "Bugünün yedeği zaten var."; exit 0
fi
FILE="$DIR/gpusa-$(date +%F_%H%M).dump"
docker exec "$CONTAINER" pg_dump -U postgres -Fc "$DB" > "$FILE.part"
mv "$FILE.part" "$FILE"; chmod 600 "$FILE"
echo "Yedek alındı: $FILE ($(du -h "$FILE" | cut -f1))"
COPY="${GPUSA_BACKUP_COPY_DIR:-}"
if [ -n "$COPY" ]; then
    if [ -d "$COPY" ] && cp "$FILE" "$COPY/" 2>/dev/null; then
        chmod 600 "$COPY/$(basename "$FILE")" 2>/dev/null || true
        ls -1t "$COPY"/gpusa-*.dump 2>/dev/null | tail -n +"$((KEEP + 1))" | while read -r old; do rm -f "$old"; done
        echo "İkinci kopya: $COPY"
    else
        echo "UYARI: ikinci kopya yazılamadı ($COPY yok ya da dolu; USB takılı mı?)" >&2
    fi
fi
if [ -n "${GPUSA_BACKUP_RSYNC:-}" ]; then
    if rsync -q "$FILE" "$GPUSA_BACKUP_RSYNC" 2>/dev/null; then echo "Uzak kopya: $GPUSA_BACKUP_RSYNC"; else echo "UYARI: uzak kopya gönderilemedi ($GPUSA_BACKUP_RSYNC)" >&2; fi
fi
ls -1t "$DIR"/gpusa-*.dump | tail -n +"$((KEEP + 1))" | while read -r old; do rm -f "$old"; done
