#!/usr/bin/env bash
set -euo pipefail
APP_DIR="${1:-/opt/lmm-rss-monitor}"
BACKUP_DIR="${2:-/opt/lmm-rss-monitor/backups}"
cd "$APP_DIR"
mkdir -p "$BACKUP_DIR"
TS="$(date +%Y%m%d-%H%M%S)"
DB="${POSTGRES_DB:-rss_monitor}"
USER="${POSTGRES_USER:-rss_monitor}"

docker compose exec -T postgres pg_dump -U "$USER" "$DB" | gzip > "$BACKUP_DIR/rss_monitor_${TS}.sql.gz"
cp .env "$BACKUP_DIR/env_${TS}.txt"
echo "Backup: $BACKUP_DIR/rss_monitor_${TS}.sql.gz"
