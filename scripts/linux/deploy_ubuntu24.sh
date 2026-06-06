#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${1:-/opt/lmm-rss-monitor}"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

if [[ $EUID -ne 0 ]]; then
  echo "Запусти от root: sudo bash scripts/linux/deploy_ubuntu24.sh [/opt/lmm-rss-monitor]" >&2
  exit 1
fi

mkdir -p "$APP_DIR"
rsync -a --delete \
  --exclude '.env' \
  --exclude '.git' \
  --exclude '__pycache__' \
  "$SRC_DIR/" "$APP_DIR/"

cd "$APP_DIR"

if [[ ! -f .env ]]; then
  cp .env.example .env
  DB_PASSWORD="$(openssl rand -base64 36 | tr -d '\n' | tr '/+' '_-' | cut -c1-36)"
  API_TOKEN="$(openssl rand -hex 32)"
  sed -i "s|POSTGRES_PASSWORD=change_me|POSTGRES_PASSWORD=${DB_PASSWORD}|" .env
  sed -i "s|rss_monitor:change_me@postgres|rss_monitor:${DB_PASSWORD}@postgres|" .env
  sed -i "s|API_TOKEN=change_this_to_a_long_random_token|API_TOKEN=${API_TOKEN}|" .env
  echo "Создан .env с новым API_TOKEN: ${API_TOKEN}"
else
  echo ".env уже существует, не перезаписываю."
fi

docker compose pull || true
docker compose up -d --build

echo ""
echo "Готово. Проверка:"
echo "  cd ${APP_DIR}"
echo "  docker compose ps"
echo "  curl -H \"Authorization: Bearer \\$(grep API_TOKEN .env | cut -d= -f2)\" http://127.0.0.1:8080/api/v1/health"
