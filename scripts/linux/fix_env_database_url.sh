#!/usr/bin/env bash
set -euo pipefail
APP_DIR="${1:-/opt/lmm-rss-monitor}"
cd "$APP_DIR"
if [[ ! -f .env ]]; then
  echo ".env не найден: $APP_DIR/.env" >&2
  exit 1
fi
sed -i 's/@localhost:5432/@postgres:5432/g' .env
sed -i 's/@127.0.0.1:5432/@postgres:5432/g' .env
if ! grep -q '^DATABASE_URL=' .env; then
  DB_USER="$(grep '^POSTGRES_USER=' .env | cut -d= -f2-)"
  DB_PASS="$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)"
  DB_NAME="$(grep '^POSTGRES_DB=' .env | cut -d= -f2-)"
  echo "DATABASE_URL=postgresql+psycopg2://${DB_USER}:${DB_PASS}@postgres:5432/${DB_NAME}" >> .env
fi
echo "DATABASE_URL после правки:"
grep '^DATABASE_URL=' .env
