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


## v0.1.5

Исправлено падение worker/API на `duplicate url_hash` при полном проходе по источникам.
Причина была в стандартном поведении SQLAlchemy `expire_on_commit=True`: после commit ORM-объекты `Source` становились detached, а асинхронный сборщик пытался читать их поля уже вне Session. Теперь `SessionLocal` создаётся с `expire_on_commit=False`, поэтому загруженные поля источников остаются доступными для fetch-pass.

## RouterAI → WordPress publishing pipeline

Сервер остаётся headless для фоновой обработки, но FastAPI также отдаёт ту же standalone-админку по `/admin`, чтобы её можно было открыть с того же origin, что и API, без CORS/mixed-content проблем. Сам файл админки лежит в каталоге `admin/` и по-прежнему может запускаться отдельно через npm.

Новые возможности:

- выбор новостей по `country_code` за последний час или другой период до 24 часов;
- сохранение ключа RouterAI на сервере;
- настройка RouterAI-compatible `base_url`, LLM-модели рерайта и модели генерации изображений;
- генерация уникальной SEO-версии статьи для каждого активного WordPress-сайта;
- обязательное включение конкретной страны в промпт изображения и запрет стереотипной природы: носорогов, попугаев, крокодилов, джунглей и похожих generic wildlife-сцен;
- загрузка изображения в WordPress Media API и публикация поста через WordPress REST API;
- настройка категорий WordPress по каждому сайту.

Новые API endpoints:

```text
GET  /api/v1/publishing/settings
PUT  /api/v1/publishing/settings
GET  /api/v1/publishing/sites
POST /api/v1/publishing/sites
PUT  /api/v1/publishing/sites/{site_id}
DELETE /api/v1/publishing/sites/{site_id}
GET  /api/v1/publishing/recent-news
POST /api/v1/publishing/jobs
GET  /api/v1/publishing/jobs
GET  /api/v1/publishing/jobs/{job_id}/articles
```


Рекомендуемый запуск админки с того же сервера API:

```text
http://SERVER_IP:8080/admin
```

При таком запуске поле `Server URL` заполнится автоматически текущим origin (`http://SERVER_IP:8080`). Нажми `Проверить API`, чтобы быстро проверить доступность `/api/v1/health` и токен.

Альтернативный запуск админки через npm:

```bash
cd admin
npm start
```

По умолчанию npm-сервер админки слушает все сетевые интерфейсы и доступен с другого устройства в локальной сети по адресу `http://SERVER_IP:5173`:

```bash
cd admin
PORT=5173 npm start
```

Если нужно ограничить доступ только текущей машиной, запусти `HOST=127.0.0.1 npm run start:local`.

При открытии `http://SERVER_IP:5173` поле `Server URL` автоматически подставит текущий адрес админки: `http://SERVER_IP:5173`. Встроенный npm-сервер проксирует запросы `/api/*` в FastAPI по адресу `API_TARGET` (по умолчанию `http://127.0.0.1:8080`), поэтому браузеру не нужно напрямую открывать порт 8080. Если FastAPI находится на другом адресе, запусти админку так: `API_TARGET=http://API_HOST:8080 PORT=5173 npm start`. Проверь Bearer token из `.env` и нажми `Проверить API`.

Минимальный сценарий:

1. Открой админку на сервере API: `http://SERVER_IP:8080/admin` (или запусти отдельно: `cd admin && npm start`, затем с внешнего устройства открой `http://SERVER_IP:5173`).
2. Укажи URL сервера и Bearer token.
3. Сохрани RouterAI key, модели и общие ограничения.
4. Добавь WordPress-сайты с Application Password и ID категорий.
5. Создай publishing job по стране, языку и количеству сайтов.
6. Worker заберёт задание из очереди, создаст уникальные статьи и выгрузит их в WordPress.

### Ретроспективный publishing по архиву

Endpoint `POST /api/v1/publishing/jobs` поддерживает `pipeline_type=retrospective`. В этом режиме сервер берёт архивные материалы из собственной БД за календарный период и планирует публикации с датами в прошлом.

Пример тела запроса:

```json
{
  "pipeline_type": "retrospective",
  "country_code": "SC",
  "country_name": "Seychelles",
  "target_language": "ru",
  "period_start": "2026-06-01",
  "period_end": "2026-06-20",
  "articles_per_day": 8,
  "site_limit": 3
}
```

Для каждого активного сайта worker создаёт `articles_per_day × количество_календарных_дней_включительно` уникальных публикаций. Например, период `2026-06-01` → `2026-06-20` включает 20 дней, поэтому при `articles_per_day=8` каждый сайт получит 160 публикаций; если нужен результат 180 публикаций, укажи 9 статей в день на 20 дней или период/квоту, произведение которых равно 180.

Каждая публикация получает `scheduled_for`, а при отправке в WordPress API дата передаётся в поле `date`, чтобы пост был опубликован/создан с исторической датой.

### Лимиты генерации и защита от дублей

Для каждого WordPress-сайта можно настроить:

- `generation_limit_per_hour` — максимум попыток генерации статей за скользящий час;
- `generation_limit_per_24h` — максимум попыток генерации статей за скользящие 24 часа.

Перед каждым вызовом RouterAI worker проверяет эти лимиты по истории `published_articles`. Если лимит исчерпан, статья не генерируется, job переводится в `rate_limited`, получает `retry_after` и будет автоматически продолжена после сброса соответствующего окна.

Исходные материалы получают флаги `publishing_used_at` и `publishing_job_id` после успешной публикации. Последующие recent/retrospective snapshots выбирают только материалы без `publishing_used_at`, чтобы не писать новые статьи по уже использованным новостям.

Категоризация выполняется на этапе написания статьи: в prompt передаются доступные ID категорий WordPress конкретного сайта (`available_wordpress_category_ids`), а модель должна вернуть `category_ids`. Эти ID затем уходят в WordPress REST API в поле `categories`.
