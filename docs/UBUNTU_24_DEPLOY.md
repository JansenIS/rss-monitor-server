# Развёртывание RSS Monitor Server на Ubuntu Server 24.04 LTS

## 1. Минимальная установка

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y unzip rsync openssl curl jq
```

## 2. Установка Docker Engine и Docker Compose plugin

```bash
sudo bash scripts/linux/install_docker_ubuntu24.sh
```

## 3. Деплой проекта в /opt/lmm-rss-monitor

```bash
sudo bash scripts/linux/deploy_ubuntu24.sh /opt/lmm-rss-monitor
```

Скрипт создаст `.env`, сгенерирует пароль PostgreSQL и API token, затем запустит контейнеры.

## 4. Проверка

```bash
cd /opt/lmm-rss-monitor
TOKEN=$(grep API_TOKEN .env | cut -d= -f2)
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8080/api/v1/health
```

## 5. Открытие порта 8080

Если сервер должен быть доступен с Windows-клиента по IP:

```bash
sudo bash scripts/linux/open_firewall_8080.sh
```

После этого API будет доступен:

```text
http://SERVER_IP:8080/api/v1/health
```

## 6. Импорт базы RSS

```bash
cd /opt/lmm-rss-monitor
TOKEN=$(grep API_TOKEN .env | cut -d= -f2)
curl -X POST "http://127.0.0.1:8080/api/v1/sources/import?include_secondary=false" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@/path/to/africa-local-rss-feeds-enriched-v3.json"
```

## 7. Проверка материалов

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8080/api/v1/articles?country_code=SC&limit=20"
```

## 8. Автозапуск через systemd

Docker Compose уже использует `restart: unless-stopped`. Если нужен отдельный systemd-unit для всего стека:

```bash
sudo cp systemd/lmm-rss-monitor.service /etc/systemd/system/lmm-rss-monitor.service
sudo systemctl daemon-reload
sudo systemctl enable --now lmm-rss-monitor.service
sudo systemctl status lmm-rss-monitor.service
```

## 9. Логи

```bash
cd /opt/lmm-rss-monitor
docker compose logs -f api
docker compose logs -f worker
docker compose logs -f postgres
```

## 10. Backup

```bash
sudo bash scripts/linux/backup.sh /opt/lmm-rss-monitor
```

## 11. Restore

```bash
sudo bash scripts/linux/restore.sh /opt/lmm-rss-monitor /opt/lmm-rss-monitor/backups/rss_monitor_YYYYMMDD-HHMMSS.sql.gz
```
