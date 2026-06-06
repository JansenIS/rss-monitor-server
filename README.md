# RSS Monitor Server v0.1.2 — Ubuntu 24 / Docker

Headless-сервер для постоянного RSS-ingestion: обходит базу RSS-источников циклом `полный проход → пауза 10 минут → следующий полный проход`, сохраняет материалы в PostgreSQL и отдаёт их локальному Tauri-клиенту через HTTP API.

Текущий Windows/Tauri-клиент не урезается: он сможет работать автономно, а серверный режим позже будет добавлен как дополнительный источник данных.

## Архитектура

```text
Ubuntu Server 24.04
├─ Docker Engine
├─ PostgreSQL 16
├─ FastAPI API на :8080
└─ Worker постоянного RSS-сбора
```

Контейнеры:

```text
lmm-rss-postgres
lmm-rss-api
lmm-rss-worker
```

Worker работает так:

```text
1. Берёт все активные источники.
2. Параллельно обходит RSS/Atom.
3. Сохраняет новые материалы.
4. Логирует ошибки.
5. Не ставит карантин.
6. После полного прохода ждёт FETCH_INTERVAL_SECONDS, по умолчанию 600 секунд.
7. Начинает новый проход.
```

## Быстрый деплой на Ubuntu Server 24.04 LTS

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y unzip rsync openssl curl jq
unzip rss-monitor-server-v0.1.2-ubuntu24.zip
cd rss-monitor-server
sudo bash scripts/linux/install_docker_ubuntu24.sh
sudo bash scripts/linux/deploy_ubuntu24.sh /opt/lmm-rss-monitor
```

Проверка:

```bash
cd /opt/lmm-rss-monitor
TOKEN=$(grep API_TOKEN .env | cut -d= -f2)
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8080/api/v1/health
```

Если нужен доступ с Windows-клиента по IP сервера:

```bash
sudo bash scripts/linux/open_firewall_8080.sh
```

API будет доступен так:

```text
http://SERVER_IP:8080
```

## Импорт RSS-базы

```bash
cd /opt/lmm-rss-monitor
TOKEN=$(grep API_TOKEN .env | cut -d= -f2)
curl -X POST "http://127.0.0.1:8080/api/v1/sources/import?include_secondary=false" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@/path/to/africa-local-rss-feeds-enriched-v3.json"
```

## API

```text
GET  /api/v1/health
GET  /api/v1/sources
POST /api/v1/sources/import
POST /api/v1/sources/import-json
POST /api/v1/runs/start
GET  /api/v1/runs
GET  /api/v1/runs/{run_id}/logs
GET  /api/v1/articles
GET  /api/v1/sync/articles
GET  /api/v1/export/articles.ndjson
```

Пример выгрузки за период:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://SERVER_IP:8080/api/v1/articles?country_code=SC&from=2026-06-01&to=2026-06-06&limit=1000"
```

Инкрементальная синхронизация:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://SERVER_IP:8080/api/v1/sync/articles?after_id=0&limit=5000"
```

## Настройки `.env`

```env
POSTGRES_DB=rss_monitor
POSTGRES_USER=rss_monitor
POSTGRES_PASSWORD=...
DATABASE_URL=postgresql+psycopg2://rss_monitor:...@postgres:5432/rss_monitor
API_TOKEN=...
API_BIND=0.0.0.0
FETCH_CONCURRENCY=40
FETCH_TIMEOUT_SECONDS=15
FETCH_INTERVAL_SECONDS=600
FETCH_USER_AGENT=LocalMediaMonitorRSSServer/0.1.1 (+self-hosted; contact=admin@example.local)
MAX_ARTICLES_PER_SOURCE=200
```

Рекомендация: не публикуй PostgreSQL наружу. В `docker-compose.yml` он не проброшен на внешний порт. Наружу открыт только API `8080`, защищённый Bearer token.

## Логи

```bash
cd /opt/lmm-rss-monitor
docker compose logs -f api
docker compose logs -f worker
docker compose logs -f postgres
```

## Backup / Restore

Backup:

```bash
sudo bash scripts/linux/backup.sh /opt/lmm-rss-monitor
```

Restore:

```bash
sudo bash scripts/linux/restore.sh /opt/lmm-rss-monitor /path/to/rss_monitor_YYYYMMDD-HHMMSS.sql.gz
```

## Подробная инструкция

См. `docs/UBUNTU_24_DEPLOY.md`.


## v0.1.2 hotfix

Исправлен Docker-default `DATABASE_URL`: внутри контейнеров нужно обращаться к PostgreSQL по имени сервиса `postgres`, а не `localhost`. Также добавлено ожидание готовности БД при старте API/worker.

Если `.env` уже создан и содержит `@localhost:5432`, исправь:

```bash
sed -i 's/@localhost:5432/@postgres:5432/g' .env
sed -i 's/@127.0.0.1:5432/@postgres:5432/g' .env
docker compose down
docker compose up -d --build --force-recreate
```

## Import full Tauri client database export

This server accepts the JSON created by the Tauri client in `Перенос → Выгрузить всё`.

Upload from the server filesystem:

```bash
cd /opt/lmm-rss-monitor
TOKEN=$(grep '^API_TOKEN=' .env | cut -d= -f2-)

curl -X POST "http://127.0.0.1:8080/api/v1/database/import?import_sources=true&import_mentions=true" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@/opt/lmm-rss-monitor/local-media-monitor-full.json"
```

The import endpoint supports:

- sources from the Tauri `sources` array;
- saved local materials from the Tauri `mentions` array;
- duplicate detection by URL hash;
- old Tauri source IDs remapped to server-side source IDs.

The legacy endpoint `/api/v1/sources/import` also detects `schema=local-media-monitor-transfer-v1` and imports both sources and materials.
