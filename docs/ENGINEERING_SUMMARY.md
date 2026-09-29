# ENGINEERING SUMMARY

_Обновлено: 2026-09-24 — завершён этап архитектурного исследования (до Phase 1)._

**PROJECT:**
C:\youtubesistemprew

**ARCHITECTURE:**
Модульный монолит: `api` (FastAPI) + `worker` (Procrastinate) из одного Python-пакета + `web` (Next.js). PostgreSQL — единственный stateful-сервис (данные, очередь, кэш). Провайдеры (YouTube, AI, email, Telegram, VK, storage) — за протоколами, с mock-реализациями. Human-in-the-Loop закреплён инвариантами данных. (ADR-0001)

**FRONTEND:**
Next.js 16 App Router, React 19, TypeScript, TanStack Query/Table/Virtual, Tailwind 4 + shadcn/ui, типизированный клиент из OpenAPI. Next проксирует `/api` → FastAPI (same-origin cookie). (ADR-0005)

**BACKEND:**
Python 3.12, FastAPI 0.141, Pydantic 2.13, SQLAlchemy 2.0, httpx, structlog. Сессии в БД + Argon2id, RBAC по workspace, CSRF, секреты AES-256-GCM. (ADR-0002, ADR-0009)

**DATABASE:**
PostgreSQL 16.15 (нативно на dev), psycopg 3, Alembic. Нормализованная схема ~45 таблиц; JSONB только для AI/provider metadata; глобальные YouTube-сущности + workspace-изолированные лиды. `lc_messages='C'`. (ADR-0004, DATABASE.md)

**QUEUE:**
Procrastinate 3.10 на PostgreSQL + бизнес-таблица `job_runs`. Очереди interactive / bulk / monitoring / outreach + priority. Spike на Windows пройден: priority, retry, cancel, dedup. (ADR-0003)

**STORAGE:**
`StorageBackend`: локальный FS (default) / S3-compatible (опционально). Content-addressed по SHA-256, дедупликация через `image_assets`, отдача только через API с проверкой доступа. (ADR-0006)

**AI:**
Протоколы Text/Vision/ImageProvider, model registry + task routing с fallback по capabilities, schema-first вывод (Pydantic + repair), версионируемые промпты, учёт стоимости каждого вызова. Двухступенчатый анализ превью: детерминированные метрики → vision-аудит только для отфильтрованных. Scores считаются кодом из subscores. Модели по умолчанию: GPT-6 Sol (vision/analysis), GPT-6 Luna (bulk), GPT-6 Astra (offers) — ID подлежат проверке (Q-004). (ADR-0007, AI_ARCHITECTURE.md)

**IMAGE GENERATION:**
`gpt-image-2.5-flare` (варианты, fast) и `gpt-image-2.5-sunburst` (финал, точное редактирование) — подтверждены документацией OpenAI; задаются конфигурацией. Brief генерации строится из кодов проблем анализа → генерация исправляет выявленное. (AI_ARCHITECTURE.md §7)

**INTEGRATIONS:**
YouTube Data API v3 (quota-aware: search 100 units, channels/videos/playlistItems 1 unit, 10 000/день — verified). Email SMTP. Telegram Bot — уведомления владельцу. Telegram MTProto и VK — после решения владельца (Q-001). Mock для всех. (ADR-0008, ADR-0010, INTEGRATIONS.md)

**KEY LIBRARIES:**
fastapi, pydantic, sqlalchemy, alembic, psycopg, procrastinate, httpx, openai, pillow, numpy, imagehash, argon2-cffi, cryptography, structlog; next, react, @tanstack/*, openapi-fetch, tailwindcss, zod; pytest, respx, hypothesis, vitest, playwright. (DEPENDENCIES.md)

**MAIN RISKS:**
1. Квота YouTube ограничивает масштаб discovery (Q-003).
2. Правовые/платформенные ограничения холодного outreach, особенно Telegram (Q-001, Q-002).
3. Стоимость массового vision-анализа (Q-010).
4. Docker недоступен на dev-машине — Docker-артефакты UNVERIFIED до CI/Docker-хоста (Q-005).
5. Ограниченные ресурсы dev-машины (2 vCPU / 4 ГБ).

**IMPORTANT TRADE-OFFS:**
- Очередь на PostgreSQL вместо Redis: −1 сервис и транзакционность ценой ограниченной (но достаточной) пропускной способности.
- Официальный YouTube API вместо скрапинга: легальность и стабильность ценой квоты.
- Свои тонкие AI-адаптеры вместо LangChain: контроль стоимости/схем ценой большего количества своего кода.
- Наблюдаемость через таблицы БД вместо Prometheus/Grafana: без инфраструктуры, но без готового алертинга.
- Небольшие отклонения от буквального списка статусов ТЗ (FILTERED не хранится; MANUALLY_EDITED — флаг) ради корректной модели.

**FIRST IMPLEMENTATION PHASE (Phase 1 — фундамент):**
1. Скелет репозитория, `.gitignore`, `.env.example`, `scripts/setup.ps1`, `scripts/dev.ps1`.
2. БД: роли `ytlead_owner`/`ytlead_app`, базы `ytlead` и `ytlead_test`.
3. Backend: config, db session, structlog, errors, state machine base, SecretStore, StorageBackend (FS).
4. Alembic baseline: identity (users, workspaces, members, sessions, audit_logs, secrets, idempotency_keys), projects (search_projects, search_queries, taxonomy_nodes, project_niches), YouTube (channels, videos, snapshots), media (image_assets, thumbnails), ops (job_runs).
5. Auth: login/logout/me, CSRF, RBAC dependency, CLI `create-owner`.
6. API: projects CRUD, queries (bulk import), channels/videos read, thumbnails serving, job_runs read.
7. Worker: Procrastinate app, `job_runs` интеграция, тестовая задача `thumbnail_download` (через mock).
8. Frontend: layout с sidebar (§62), login, Projects list/detail, Channels list (virtualized), Jobs page, loading/empty/error states.
9. Demo mode seed (помеченные данные).
10. Тесты: unit (state machine, secrets, storage dedup), API (auth, projects, RBAC), DB (constraints), worker (idempotency).
11. `infrastructure/docker-compose.yml` + Dockerfiles — `docker compose config` (запуск UNVERIFIED).
12. Документация: README, API.md, DEPLOYMENT.md, AI_PIPELINE.md (skeleton), обновление этого summary.

**PHASE 1 PROGRESS (2026-09-26):**
- п.1: `.gitignore`, `.env.example`, `scripts/setup.ps1`, `scripts/dev.ps1` — готово (dev.ps1 пока запускает только API; worker — п.7). Git-репозиторий инициализирован.
- п.2–4: БД и миграции применены к `ytlead` и `ytlead_test`. Исправлена миграция procrastinate-схемы (psycopg трактовал `%` в plpgsql как плейсхолдер — ранее миграция не применялась). `alembic check` — расхождений нет.
- п.3 пробелы закрыты: `SecretStore` (`app/core/secrets.py`, AAD = `{secret_id}:{purpose}`), `idempotency_keys` (миграция `2a59c3efc6c0`, `app/core/idempotency.py`: claim/complete/replay), тесты storage; `app/domains/media/service.py` — `store_image` с дедупликацией по SHA-256.
- п.6 API: `/api/projects` (CRUD + audit), `/api/projects/{id}/queries` (list, bulk import с нормализацией/дедупликацией, delete), `/api/channels` (фильтры q/подписчики/страна/проект, сортировки, пагинация), `/api/channels/{id}`, `/api/channels/{id}/videos`, `/api/videos/{id}`, `/api/images/{asset_id}` (проверка доступа workspace, ETag/304), `/api/jobs`. Чужие сущности → 404. Всего 51 тест — проходят.
- Не сделано в п.6: привязка ниш (`project_niches`) — появится вместе с taxonomy API (Phase 2); `Idempotency-Key` пока не подключён к эндпоинтам (нужен для дорогих POST: discovery, генерация, approve/send).
- п.5 Auth: `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me`, `GET /api/health`; сессии в БД (SHA-256 токена, ротация, idle/absolute timeout), CSRF double-submit с привязкой к сессии, `require(Permission)` RBAC, rate limit логина по email/IP через `audit_logs`, CLI `python -m app.cli create-owner`. 20 тестов (pytest, реальный PostgreSQL) — проходят.
- п.7 Worker — готово: `python -m app.workers [--queues …] [--concurrency N]` (проверяет `lc_messages` при старте); `scripts/dev.ps1` запускает API + worker (`-NoWorker`, `-ApiOnly`).
  - Постановка (`jobs.service.enqueue_job`): строка `job_runs` + `procrastinate_defer_jobs_v1` на **том же** соединении → атомарно с бизнес-изменением; дедупликация по `fingerprint` (тип + workspace + параметры) через partial UNIQUE.
  - `app/workers/registry.py` (`job_task`): QUEUED/RETRYING → RUNNING → COMPLETED / RETRYING / FAILED; retry с экспоненциальным backoff **только** для transient-ошибок (`IntegrationError.retryable`, `RetryableJobError`, сбои БД/сети); баги — fail fast; отменённые run пропускаются; человекочитаемые `error_code`/`error_human`.
  - Периодическая `retry_stalled_jobs` (каждую минуту, очередь `monitoring`): перезапускает задачи умерших воркеров (важно для Windows без graceful shutdown), run → RETRYING `worker_lost`.
  - `thumbnail_download` (очередь `bulk`): покомитно по превью, прогресс N из M, повтор продолжает с места остановки; постоянные ошибки фиксируются на `thumbnails` и не валят задачу. Провайдеры `ThumbnailFetcher`: `http` (i.ytimg.com, allowlist хостов — SSRF-защита, лимит размера) и `mock` (детерминированные JPEG, `ImageSource.DEMO`). Выбор — `YTL_THUMBNAIL_FETCHER` (по умолчанию mock в demo mode, иначе http).
  - API: `POST /api/channels/{id}/thumbnails/download` (202, идемпотентно), `POST /api/jobs/{id}/cancel` (todo → cancelled, doing → abort requested; 409 для завершённых). Audit: `job.enqueued`, `job.cancelled`.
  - Тесты: 99 (было 51), включая прогон реального воркера Procrastinate на тестовой БД (успех, retry с продолжением, исчерпание попыток, fail fast, пропуск отменённой).
  - `http`-провайдер проверен против настоящего i.ytimg.com (2026-09-27): opt-in тест `tests/integration/test_thumbnails_live.py` (`YTL_LIVE_NETWORK=1`) — реальный воркер скачал 3 превью (JPEG 480×360, `source=youtube_thumbnail`), одинаковые байты дедуплицированы в один `image_asset`, реальный 404 (`maxresdefault` у видео 2005 г.) записан как `fetch_status=failed` без падения задачи. В обычном прогоне тест пропускается (офлайн).
- п.8 Frontend — готово (2026-09-27), см. `frontend/README.md`:
  - Next.js 16.3.6 (Turbopack) + React 19.2, TypeScript 5.9 (ставит `create-next-app`; Q-011), TanStack Query 5 / Table 9 (`useTable` + `tableFeatures`) / Virtual 3, openapi-fetch + сгенерированные типы, Tailwind 4, zod 4. UI-примитивы — свои в стиле shadcn (CLI shadcn не запускался; Radix не подключён — появится вместе с диалогами/меню).
  - `/api/*` → FastAPI через rewrites (`YTL_API_ORIGIN`, **фиксируется при `next build`**); `dev.ps1` передаёт порт API.
  - Страницы: login (`?next=` с защитой от open redirect), layout с sidebar и шорткатами (`g p/c/j`, `/`), Projects list/detail (создание, архив, удаление, bulk-импорт запросов с отчётом о дубликатах/отклонённых), Channels (фильтры и сортировка в URL, страницы по 200, виртуализация строк), Jobs (фильтры, прогресс, отмена, опрос каждые 2 с только при активных задачах). Loading/empty/error-состояния везде; 401 → `/login`.
  - Проверки: `npm run lint` (0 ошибок; 1 предупреждение React Compiler про `useVirtualizer` — компилятор выключен), `npm run typecheck`, `npm test` — 21 тест (vitest), `next build` ≈ 31 с на dev-машине (Q-012). Разовый E2E на `ytlead_test` через Next-прокси: 29 HTTP-проверок + 22 шага в headless Chromium (Playwright, вне репозитория) — логин, CSRF, CRUD проектов, импорт, 1200 каналов (в DOM ≤ 34 строк), сортировка/поиск, отмена задачи, logout; ошибок в консоли нет. Постоянного Playwright-набора в репозитории пока нет.
  - Известное: сообщения ошибок backend на английском при русском UI (зависит от Q-002); за прокси все запросы приходят в API с IP 127.0.0.1 — rate limit логина по IP фактически общий (по email работает), нужен доверенный `X-Forwarded-For`.
- п.9 (2026-09-27): Demo mode seed — `app/domains/demo/service.py`, CLI `python -m app.cli seed-demo [--workspace SLUG] [--reset | --remove]`. 2 проекта, 11 каналов (один общий для обоих проектов), по 12 видео с локально сгенерированными превью со штампом «DEMO». Маркировка: `is_demo`, ID с префиксом `demo-`, заголовки `[DEMO]`, `raw={"demo": true}`, `ImageSource.DEMO`, `DiscoveryMethod.DEMO`, URL превью на `.invalid`. Seed идемпотентен; `--remove` удаляет только demo-строки и неиспользуемые файлы (после commit). UI: бейдж «демо», без внешних ссылок на YouTube для demo-каналов, сетка превью на странице канала. Проверки: backend `pytest` — 110 passed / 1 skipped, ruff по новым файлам чист, `alembic check` — расхождений нет; frontend lint/typecheck, vitest — 22 теста.
- п.10 (2026-09-27): закрыты пробелы покрытия — `tests/unit/test_state_machine.py` (база StateMachine, полнота/достижимость состояний лида и job_run, SENT только из APPROVED/SCHEDULED, opt-out из любой стадии активной воронки) и `tests/integration/test_db_constraints.py` (все UNIQUE/CHECK с проверкой имени ограничения, NULLS NOT DISTINCT, частичный уникальный индекс активных задач, каскады удаления). Остальное (secrets, storage dedup, auth/projects/RBAC, idempotency воркера и очереди) было покрыто ранее. Backend `pytest` — 135 passed / 1 skipped (live-сеть).
- п.11 (2026-09-27): `backend/Dockerfile` (multi-stage, один образ для api/worker/migrate, non-root uid 10001), `frontend/Dockerfile` (Next `output: "standalone"`, `YTL_API_ORIGIN` — build arg), `infrastructure/docker-compose.yml` (postgres:16 с `lc_messages=C`, одноразовый `migrate` от владельца схемы, `api`/`worker` под ролью `ytlead_app`, наружу только `web` на 127.0.0.1), `infrastructure/postgres/initdb/10-ytlead.sh` (аналог `setup-db.ps1`), `infrastructure/.env.example`, `.gitattributes` (LF для `*.sh`). Проверено: `docker compose config` (с переменными и без); init-скрипт — Git Bash против локального PG16 на временных ролях/базе (миграции от владельца, права `ytlead_app`, запрет DDL), затем очистка; standalone-сервер Next нативно (страницы/статика 200, `/api/health` через прокси 200). **UNVERIFIED:** сборка образов и запуск стека (нет Docker Engine, Q-005), init-скрипт внутри контейнера `postgres:16`.
- п.12 (2026-09-27): `README.md`, `docs/API.md` (авторизация/CSRF, роли, формат ошибок, пагинация, все 21 эндпоинт из OpenAPI и их неочевидное поведение — сверено с кодом), `docs/DEPLOYMENT.md` (dev-скрипты, compose, первый запуск, HTTPS, обновление, бэкапы, статус проверки, ограничения), `docs/AI_PIPELINE.md` (каркас: поток данных, текущее состояние, сквозные правила). Относительные ссылки проверены (18/18).
- Закрытие оговорок Phase 1 (2026-09-27):
  - **IP клиента за прокси.** Выяснено: Next не дописывает адрес в `X-Forwarded-For` (`??=` — пропускает значение клиента как есть). Проверено на цепочке «curl → Next standalone → uvicorn»: в dev IP подделывался заголовком (uvicorn доверяет loopback), в compose все входы шли с IP прокси. Исправлено: compose — фиксированный IP `web` в своей подсети, `api` доверяет `X-Forwarded-For` только от него (`FORWARDED_ALLOW_IPS`); dev — `next dev -H 127.0.0.1` (`dev.ps1 -WebHost`); DEPLOYMENT.md — TLS-прокси обязан перезаписывать `X-Forwarded-For` (пример nginx). Два теста в `test_auth.py`.
  - **ruff** — 0 замечаний (ручной перенос строк без переформатирования файлов; `StateMachine` на PEP 695).
  - **mypy** (был в плане CI, не запускался) — 17 ошибок исправлены: явные аргументы `set_cookie`, `CursorResult` для `rowcount`, `WorkerOptions` в воркере (без `queues=None`), аннотация ключа сортировки, лишние `type: ignore`. Чисто и под Windows, и с `--platform linux`.
  - **CI** — `.gitlab-ci.yml` (lint / test / build, см. DEPLOYMENT.md §4). Команды проверены локально, включая прогон тестов только на переменных CI; на GitLab-раннере — **не запускался** (Q-015).
  - Проверки: backend `pytest` 137 passed / 1 skipped, `ruff`, `mypy`, `alembic check`; frontend lint/typecheck/vitest 22.
- Остаётся: сборка Docker-образов (Q-005 или первый прогон CI), Playwright E2E в CI, `Idempotency-Key` на эндпоинтах (нужен для дорогих операций Phase 2+).
- Следующее: Phase 2 — discovery через YouTube Data API (нужен `YTL_YOUTUBE_API_KEY`, Q-003).

**VERIFICATION STATUS (этап архитектуры):**
- Verified: версии пакетов (PyPI/npm), Procrastinate на Windows + PG16 (spike), квоты YouTube (официальная документация), модели gpt-image-2.5-flare/sunburst и линейка GPT-6 (документация OpenAI).
- Unverified: API ID/цены GPT-6 (нет ключа), Docker-сборка (нет Docker Engine). (TypeScript: зафиксирован 5.9 — Q-011.)
