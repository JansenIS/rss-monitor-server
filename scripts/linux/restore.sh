#!/usr/bin/env bash
set -euo pipefail
APP_DIR="${1:-/opt/lmm-rss-monitor}"
BACKUP_FILE="${2:-}"
if [[ -z "$BACKUP_FILE" || ! -f "$BACKUP_FILE" ]]; then
  echo "Использование: sudo bash scripts/linux/restore.sh /opt/lmm-rss-monitor /path/backup.sql.gz" >&2
  exit 1
fi
cd "$APP_DIR"
set -a
source .env
set +a

gunzip -c "$BACKUP_FILE" | docker compose exec -T postgres psql -U "$POSTGRES_USER" "$POSTGRES_DB"
echo "Restore завершён."
