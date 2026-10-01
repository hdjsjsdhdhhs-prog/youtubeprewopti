# DEPLOYMENT

Два пути (ADR-0013):

| Среда | Как | Статус проверки |
|---|---|---|
| Dev (Windows, эта машина) | нативно: служба PostgreSQL, `backend/.venv`, Node — `scripts/*.ps1` | проверено, используется ежедневно |
| Production / Linux | Docker Compose: `infrastructure/docker-compose.yml` | **сборка и запуск образов UNVERIFIED** — на dev-машине нет Docker Engine (Q-005) |

## 1. Dev-окружение (Windows, без Docker)

См. [README](../README.md#быстрый-старт-dev-windows-без-docker). Кратко:

| Скрипт | Что делает |
|---|---|
| `scripts/setup-db.ps1` | Роли `ytlead_owner` (владелец схемы, миграции) и `ytlead_app` (только DML), базы `ytlead`/`ytlead_test`, расширения `citext`/`pg_trgm`, `lc_messages='C'`; пишет `%USERPROFILE%\.ytlead-secrets\backend.conf` (URL баз, мастер-ключ; ACL — только Administrators/SYSTEM). Идемпотентен; `-RotatePasswords` — сменить пароли ролей. |
| `scripts/setup.ps1` | venv + `pip install -e backend[dev]`, `setup-db.ps1` (`-SkipDb` — пропустить), `alembic upgrade head`, `npm install`. |
| `scripts/dev.ps1` | API (uvicorn `--reload`, selector event loop) + worker + `next dev`. `-ApiOnly`, `-NoWorker`, `-ApiPort`. Worker не перезагружается сам — перезапустите скрипт после изменения кода задач. |

## 2. Production через Docker Compose

### Состав

| Сервис | Образ | Назначение |
|---|---|---|
| `postgres` | `postgres:16` | Данные и очередь задач. `lc_messages=C` (требование Procrastinate, ADR-0003). При первом старте `postgres/initdb/10-ytlead.sh` создаёт роли `ytlead_owner`/`ytlead_app`, базу `ytlead`, расширения и права. |
| `migrate` | `ytlead-backend` | Одноразово: `alembic upgrade head` от владельца схемы. `api` и `worker` стартуют только после его успешного завершения. |
| `api` | `ytlead-backend` | FastAPI (uvicorn :8000), только внутренняя сеть. Healthcheck — `GET /api/health`. |
| `worker` | `ytlead-backend` | `python -m app.workers` (все очереди). `stop_grace_period: 60s`. |
| `web` | `ytlead-web` | Next.js standalone (:3000), единственный опубликованный порт. Проксирует `/api/*` на `http://api:8000` — адрес зашит при сборке образа (build arg `YTL_API_ORIGIN`). |

Тома: `pgdata` (данные PostgreSQL), `storage` (изображения, общий для `api` и `worker`, `/data/storage`).
Процессы в образах работают не от root (`ytlead` uid 10001, `node`).
`api` и `worker` подключаются к БД ролью `ytlead_app` (без DDL); пароль владельца схемы получают только
`postgres` и `migrate`.

### Первый запуск

```bash
cd infrastructure
cp .env.example .env            # заполнить: POSTGRES_PASSWORD, YTL_DB_OWNER_PASSWORD, YTL_DB_APP_PASSWORD, YTL_MASTER_KEY
docker compose config -q        # проверка синтаксиса и обязательных переменных
docker compose up -d --build
docker compose ps               # api — healthy, migrate — exited (0)

# первый пользователь (пароль спросит интерактивно)
docker compose run --rm api python -m app.cli create-owner --email you@example.com --name "Имя" --workspace "Команда"
# по желанию — демо-данные
docker compose run --rm api python -m app.cli seed-demo
```

Требования к секретам:
- пароли БД подставляются в URL — только URL-безопасные символы (`secrets.token_urlsafe(24)`);
- пароли ролей применяются **только при первом старте** на пустом томе `pgdata`; позже — `ALTER ROLE … PASSWORD`
  внутри БД и обновление `.env`;
- `YTL_MASTER_KEY` шифрует сохранённые секреты интеграций (AES-256-GCM). Его потеря делает их нечитаемыми —
  храните копию отдельно от бэкапов БД.

### HTTPS

`web` по умолчанию слушает только `127.0.0.1:3000` (`YTL_WEB_BIND`, `YTL_WEB_PORT`). Перед ним нужен
reverse proxy с TLS (nginx, Caddy, Traefik), проксирующий всё на `127.0.0.1:3000`. `YTL_COOKIE_SECURE=true`
(по умолчанию) требует HTTPS; по `http://localhost` браузеры такие cookie тоже принимают.

### IP клиента и `X-Forwarded-For` (обязательно)

Лимит неудачных входов считается по email **и по IP клиента**. Цепочка: клиент → TLS-прокси → `web` (Next) →
`api`. Как устроено:

- `api` (uvicorn) берёт IP из `X-Forwarded-For` **только** если запрос пришёл от `web`: у `web` фиксированный
  адрес во внутренней сети (`YTL_WEB_IP`), `api` доверяет только ему (`FORWARDED_ALLOW_IPS`).
- Next **не дописывает** адрес в `X-Forwarded-For`: если заголовок уже есть, он передаётся как есть; если нет —
  Next ставит адрес того, кто к нему подключился.
- Поэтому TLS-прокси обязан **перезаписывать** `X-Forwarded-For` реальным адресом клиента, а не дописывать к
  значению от клиента. Иначе клиент подставит любой IP и обойдёт лимит по IP (лимит по email продолжит работать).

nginx:

```nginx
location / {
    proxy_pass http://127.0.0.1:3000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;   # перезапись, НЕ $proxy_add_x_forwarded_for
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Для других прокси проверьте в их документации, что входящий `X-Forwarded-For` от клиента отбрасывается.
Если `web` опубликован без прокси (только `127.0.0.1`), подменить IP может лишь тот, у кого есть доступ к
самому хосту.

Если подсеть `172.30.57.0/24` пересекается с сетями хоста — поменяйте `YTL_DOCKER_SUBNET`,
`YTL_DOCKER_IP_RANGE` и `YTL_WEB_IP` (адрес `web` должен быть в подсети, но вне диапазона динамических адресов).

Dev (`scripts/dev.ps1`) устроен так же: uvicorn доверяет `X-Forwarded-For` от loopback (прокси `next dev`), а
`next dev` слушает только `127.0.0.1` (`-WebHost`). Не открывайте его на другие интерфейсы.

### Обновление

```bash
git pull
cd infrastructure
docker compose up -d --build    # migrate применит новые миграции до старта api/worker
```

Незавершённые задачи воркера после перезапуска подхватывает периодическая `retry_stalled_jobs`.

### Резервное копирование

```bash
# БД
docker compose exec -T postgres pg_dump -U postgres -Fc ytlead > ytlead-$(date +%F).dump
# изображения
docker run --rm -v ytlead_storage:/data:ro -v "$PWD":/backup alpine tar czf /backup/storage-$(date +%F).tgz -C /data .
```

Восстановление БД: `pg_restore -U postgres -d ytlead --clean --if-exists` в пустую базу, созданную init-скриптом.
Файлы в `storage` адресуются по SHA-256 и не меняются после записи — бэкап можно делать инкрементально.

### Логи

`docker compose logs -f api worker`. Backend пишет JSON (structlog) в stdout.

## 3. Что проверено, а что нет

| Проверка | Результат |
|---|---|
| `docker compose config` (с переменными и без) | ✅ конфигурация разбирается; при отсутствии обязательных переменных — понятная ошибка |
| `postgres/initdb/10-ytlead.sh` | ✅ выполнен Git Bash против локального PostgreSQL 16 (временные роли/база, затем удалены): миграции от владельца проходят, `ytlead_app` читает/пишет и вызывает функции Procrastinate, `CREATE TABLE` запрещён. Внутри контейнера `postgres:16` — не запускался. |
| Next standalone (`output: "standalone"`) | ✅ `next build` + `node .next/standalone/server.js` нативно: страницы и статика 200, `/api/health` через прокси — 200 |
| IP клиента за прокси (`FORWARDED_ALLOW_IPS`) | ✅ цепочка «curl → Next standalone → uvicorn» на тестовой БД: без доверия к прокси все входы записываются с адресом прокси; с доверием — с адресом из `X-Forwarded-For`. Контракт закреплён тестами `tests/api/test_auth.py` (IP только от доверенного прокси, лимит по IP — на клиента). В compose (статический IP `web`) — не запускался. |
| Сборка образов `backend/Dockerfile`, `frontend/Dockerfile`, запуск стека | ❌ **UNVERIFIED** локально — нет Docker Engine (Q-005, перепроверено 2026-10-01). Сборку образов выполняет job `build-images` в GitHub Actions; запуск всего стека (`docker compose up`) нигде не проверялся. |

## 4. CI (GitHub Actions, `.github/workflows/ci.yml`)

Репозиторий — GitHub (`hdjsjsdhdhhs-prog/youtubeprewopti`), CI только там (GitLab CI удалён). Запуск: push в
`main`, любой pull request, вручную (`workflow_dispatch`); новый запуск той же ветки отменяет предыдущий.

| Job | Что делает |
|---|---|
| `backend` | `pip install -e .[dev]`, `ruff check .`, `mypy app`; service-контейнер `postgres:16` → `ALTER SYSTEM SET lc_messages TO 'C'` (у service-контейнеров нельзя задать command), `pytest` (случайный `YTL_MASTER_KEY`, JUnit-артефакт), `alembic check` |
| `frontend` | `npm ci`, `npm run lint`, `npm run typecheck`, `npm test` |
| `build-images` | после `backend` и `frontend`: `docker compose config` с фиктивными секретами, `docker build` обоих образов, smoke-импорт `app.main`/`app.workers.queue`/`app.cli` в образе backend. На hosted-раннере Ubuntu Docker Engine есть — это единственное место, где проверяются Dockerfile'ы. |

Проверено локально: YAML разбирается; команды jobs `backend`/`frontend` выполнены на dev-машине (включая
`alembic check` против тестовой базы), `docker compose config` — с теми же подстановками.
**Не проверено:** реальный запуск workflow на GitHub-раннере (будет после push) и `build-images` целиком.

## 5. Известные ограничения

- Хранилище только локальное (том `storage`); S3-совместимый backend (ADR-0006) — позже.
- Playwright E2E на compose-стеке в CI — позже (ADR-0013).
