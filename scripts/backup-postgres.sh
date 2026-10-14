#!/usr/bin/env bash
set -euo pipefail
umask 077

APP_DIR="/opt/car-analyzer"
BACKUP_DIR="$APP_DIR/backups"
RETENTION_COUNT="${BACKUP_RETENTION_COUNT:-30}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_FILE="$BACKUP_DIR/car_analyzer-$TIMESTAMP.dump.gz"

cd "$APP_DIR"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

set -a
source "$APP_DIR/.env"
set +a

: "${DB_USER:?DB_USER is required}"
: "${DB_NAME:?DB_NAME is required}"

printf 'Starting Car Analyzer PostgreSQL backup: %s\n' "$BACKUP_FILE"

docker compose exec -T postgres \
  pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc \
  | gzip -9 > "$BACKUP_FILE"

chmod 600 "$BACKUP_FILE"

gzip -t "$BACKUP_FILE"
gunzip -c "$BACKUP_FILE" \
  | docker compose exec -T postgres pg_restore --list >/dev/null

find "$BACKUP_DIR" -maxdepth 1 -type f -name 'car_analyzer-*.dump.gz' -printf '%T@ %p\0' \
  | sort -z -nr \
  | awk -v RS='\0' -v ORS='\0' -v keep="$RETENTION_COUNT" 'NR > keep { sub(/^[^ ]+ /, ""); print }' \
  | xargs -0r rm -f --

printf 'Backup complete: %s\n' "$BACKUP_FILE"
printf 'Backup size bytes: %s\n' "$(stat -c '%s' "$BACKUP_FILE")"
printf 'Backups retained: %s\n' "$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'car_analyzer-*.dump.gz' | wc -l)"
