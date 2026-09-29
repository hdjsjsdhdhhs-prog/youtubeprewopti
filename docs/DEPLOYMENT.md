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
| Сборка образов `backend/Dockerfile`, `frontend/Dockerfile`, запуск стека | ❌ **UNVERIFIED** — нет Docker Engine (Q-005). Закроется задачей `build-images` в CI или удалённым Docker-хостом (`DOCKER_HOST=ssh://…`). |

## 4. CI (`.gitlab-ci.yml`)

| Задача | Stage | Что делает |
|---|---|---|
| `lint-backend` | lint | `ruff check .`, `mypy app` |
| `lint-frontend` | lint | `npm run lint`, `npm run typecheck` |
| `test-backend` | test | pytest на сервисе `postgres:16` (`lc_messages=C`), JUnit-отчёт, затем `alembic check` |
| `test-frontend` | test | vitest |
| `build-images` | build | `docker compose config` + `docker build` обоих образов + smoke-импорт приложения в образе. Нужен раннер с Docker-in-Docker (privileged). |

Задачи запускаются только при изменениях в своей части репозитория; пайплайны — для MR и веток (без дублей).
Проверено локально: YAML разбирается, у всех задач корректные stage/script; команды `test-backend`
выполнены **только с переменными CI** (без `backend.conf`) — 137 passed, `alembic check` без расхождений;
шаг `sed` + `docker compose config` из `build-images` выполнен в Git Bash; smoke-импорт — нативно.
**Не проверено:** реальный запуск на GitLab-раннере (нет GitLab-проекта, Q-015) и `build-images` целиком.

## 5. Известные ограничения

- Хранилище только локальное (том `storage`); S3-совместимый backend (ADR-0006) — позже.
- Playwright E2E на compose-стеке в CI — позже (ADR-0013).
