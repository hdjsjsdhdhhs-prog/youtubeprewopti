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

**CI / REPOSITORY (2026-10-01):** репозиторий — GitHub `hdjsjsdhdhhs-prog/youtubeprewopti`; CI перенесён на GitHub Actions (`.github/workflows/ci.yml`: backend / frontend / build-images), `.gitlab-ci.yml` удалён (Q-015 DECIDED, ADR-0013). Docker Engine на ВМ перепроверен — по-прежнему невозможен (Q-005); сборку образов делает CI. Первый прогон workflow — после push (**не проверен**).

**PHASE 2 — DISCOVERY (2026-10-01):**
- Провайдер (ADR-0008): `app/providers/youtube/` — протокол `YouTubeProvider`, `YouTubeDataApiProvider` (httpx; `search`/`channels`/`playlistItems`/`videos`, `fields` для экономии трафика, ISO 8601 duration, keywords, классификация ошибок по `reason`), `MockYouTubeProvider` (детерминированный пул из 40 каналов `demo-yt-…`, пересекающиеся выдачи для проверки дедупликации). Выбор — `YTL_YOUTUBE_PROVIDER` (`api`/`mock`, по умолчанию mock в demo-режиме, api при наличии ключа).
- Квота: `youtube_quota_ledger` (сутки по Pacific Time считает PostgreSQL), резервирование **до** каждого вызова атомарным upsert, в воркере — в отдельной коммитящейся транзакции; оценка (поиск / максимум с обогащением) в ответе запуска; при исчерпании — задача `failed` `quota_exceeded`, текущий запрос → `pending`, выполненные не повторяются.
- Конвейер (`app/domains/discovery/service.py`): поиск страницами → каналы пачками по 50 (свежие < 24 ч пропускаются) → последние `videos_to_analyze` видео + исходные видео выдачи → upsert глобальных `channels`/`videos`/`thumbnails` (`pending`), суточные снимки, `channel_metrics`, `project_channels` (`discovery_count`), `channel_discoveries` (проект, запрос, исходное видео, способ `keyword_search`/`channel_search`). Задача `discovery` (очередь `bulk`), коммит на каждый запрос, повтор задачи продолжает с невыполненных.
- Ниши (§54): `taxonomy_nodes` niche › topic › subtopic — `GET /api/taxonomy`, `POST /api/taxonomy/bulk`; ниши проекта `GET/PUT /api/projects/{id}/niches`; запросы помечаются нишей и типом поиска при импорте (`taxonomy_node_id`, `search_type`) и `PATCH …/queries/{id}`.
- Фильтры (§3): `GET /api/channels` — подписчики, видео, средние/медианные/последние/последние-10 просмотры, просмотры/подписчики, видео за 7/30/90 дней, интервал и регулярность публикаций, давность последнего видео, страна, язык, ниша включить/исключить (с вложенными темами); сортировки по метрикам. `filter_settings` проекта валидируется той же схемой (сохранить/применить из UI). Карточка канала: метрики Performance, ниши и источники обнаружения.
- Frontend: блок «Поиск каналов» на странице проекта (квота, запуск, последние запуски с итогами, автообновление после завершения), «Ниши проекта», импорт с нишей и типом поиска, колонка ниши у запросов; страница «Ниши» (`g n`); таблица каналов с колонками метрик и панелью фильтров; карточка канала — Performance и «Источники обнаружения».
- Миграция `3b7f0e9c41d2` (`channel_metrics`, `youtube_quota_ledger`, `search_queries.search_type`) применена к `ytlead` и `ytlead_test`; права `ytlead_app` — через default privileges (проверено чтением от роли приложения).
- Проверки: backend `pytest` 169 passed / 1 skipped (+32: адаптер на respx, метрики, конвейер с дедупликацией/провенансом/квотой, API ниш/фильтров/запуска, сквозной воркер с остановкой по квоте и продолжением), `ruff`, `mypy`, `alembic check`; frontend lint (0 ошибок), typecheck, vitest 28, `next build`. Сквозная проверка на тестовой базе: Next standalone → API → воркер → mock — 3 запроса, 33 канала, 342 units (оценка ≤ 372), фильтры по нишам/метрикам, сохранение фильтров; UI в headless Chromium (Playwright вне репозитория) — запуск поиска из интерфейса, панель фильтров, сохранение/применение фильтров проекта, карточка канала, страница ниш, без ошибок в консоли.
- **Не проверено:** реальные вызовы YouTube Data API (ключ не передан в конфигурацию — Q-003), фактический сброс квоты в полночь PT.
- Не сделано в Phase 2 (осознанно): поиск через «связанные видео» (`relatedToVideoId` удалён из API), кэш ответов `provider_cache`, таблица `discovery_runs` (история — `job_runs`), дата первого видео канала (требует обхода всего плейлиста), автоматическая загрузка превью найденных каналов (кнопка на карточке канала; массово — Phase 3 ingestion), AI-классификация ниш (Phase 3).
- Следующее: Phase 3 — thumbnail ingestion + vision-анализ (нужен `YTL_OPENAI_API_KEY`, Q-004).

**PHASE 3.1–3.4 — INGESTION, METRICS, AI FRAMEWORK, BUDGET GATE (2026-10-02):**
Решения владельца: начать с 3.1–3.4; без OCR/детекции лиц (Q-009); бюджет по умолчанию без лимита, массовый запуск —
только после подтверждения оценки.
- **3.1 Ingestion:** `POST /api/projects/{id}/thumbnails/download` — одна задача `thumbnail_download` на все превью
  каналов проекта, которым нужна загрузка или метрики (новые видео первыми, ≤ 5000 за задачу, `remaining`),
  `GET …/thumbnails/stats`. Задача теперь «скачать, если нужно → посчитать метрики, если нет текущей версии»;
  то же для кнопки на карточке канала. Квота YouTube не тратится.
- **3.2 Метрики и prefilter:** `image_metrics` (`app/domains/media/imaging.py`, `METRICS_ALGO_VERSION=1`): яркость,
  RMS-контраст, colorfulness (Hasler–Süsstrunk), резкость (дисперсия лапласиана), плотность контуров (Sobel +
  non-maximum suppression — тест показал, что без утончения размытая картинка давала *больше* «краёв»), доля
  градиентной энергии в центре, 5 доминирующих цветов; рабочая ширина 320 px; чёрные полосы 4:3 `hqdefault` обрезаются.
  `search_projects.prefilter_settings` (`ThumbnailPrefilter`): фильтры каналов проекта → N новейших видео (без Shorts,
  возраст, просмотры) → диапазоны метрик; `POST …/prefilter/preview`. Одинаковые изображения считаются один раз.
- **3.3 AI-слой (ADR-0007, notes):** `app/providers/ai/` — протокол `AIProvider`, `MockAIProvider` (валидный
  детерминированный JSON для любой JSON Schema), `OpenAIResponsesProvider` (SDK `openai` 3.22.1, Responses API,
  JSON-schema format, data-URL изображения, классификация ошибок вкл. регион/квоту/«модель недоступна»).
  `app/domains/ai/`: реестр `ai_models` (умолчания: vision-standard=`gpt-6-sol`, vision-premium/text-premium=`gpt-6-astra`,
  text-bulk=`gpt-6-luna`, mock-vision/mock-text; цены OpenAI пустые), маршруты задач с fallback, промпты
  `app/prompts/{name}/v{N}.md` + `prompt_templates` (SHA-256, изменённая версия отклоняется), `AIRunner` (резерв →
  вызов → Pydantic → 1 repair → запись; refused/invalid_output не становятся результатом), `ai_calls`.
  CLI `python -m app.cli ai-models` — сверка реестра с `GET /v1/models`. API: `GET /api/ai/status`,
  `PATCH /api/ai/models/{key}` (admin), `GET /api/ai/usage`.
- **3.4 Budget gate:** `budgets` (global/project/task × day/month/total, USD и/или число операций; нет строк — нет
  лимита), CRUD `/api/budgets` (admin). Расход = Σ coalesce(actual, estimated) по `ai_calls`; проверка всех лимитов и
  `job_runs.budget_usd` перед **каждым** вызовом под advisory-lock; USD-лимит при неизвестной цене блокирует
  (`pricing_unknown`). `POST …/thumbnail-analysis/estimate` — отбор, модель, цена за превью, итог, проверки бюджетов,
  `confirm_token` (меняется при любом изменении отбора/модели/цены/detail). `job_runs` cost-колонки → NUMERIC(14,6).
- **Frontend:** на странице проекта — «Превью и объективные метрики» (счётчики, запуск, последние задачи,
  автообновление) и «Отбор превью для AI-анализа» (форма prefilter, сохранение, оценка стоимости и бюджетов; кнопка
  запуска неактивна до 3.5); на карточке канала — метрики и палитра под каждым превью, кнопка «Скачать превью и метрики»
  появляется и для превью без метрик.
- Миграция `4c8e1a7d2f90` применена к `ytlead` (тесты пересоздают схему `ytlead_test`); `alembic check` — без
  расхождений в обеих; права `ytlead_app` на новые таблицы проверены (DML да, DDL нет).
- Проверки: backend `pytest` **221 passed / 1 skipped** (+52: метрики, провайдеры на уровне HTTP, runner с
  repair/fallback/refusal/бюджетами/кэпом задачи/версиями промптов, API ingestion/prefilter/estimate/budgets/реестра,
  CLI, воркер считает метрики), `ruff check .`, `mypy app`; frontend lint (0 ошибок), typecheck, vitest 32,
  `next build`; `docker compose config`. Сквозная проверка реальными процессами (uvicorn + воркер + CLI, тестовая БД,
  mock): demo-seed → ingestion досчитал метрики 72 превью (скачивание пропущено) → preview/estimate → бюджет
  «1 операция» даёт «превысит». UI в headless Chromium (`next dev` → API → воркер; Playwright вне репозитория): вход,
  обе новые карточки и бейдж mock, запуск загрузки из UI и автообновление по завершении, оценка, клиентская
  валидация, сохранение отбора и восстановление после перезагрузки, метрики под превью на карточке канала, без ошибок
  в консоли.
- **Не проверено:** реальные вызовы OpenAI (нет ключа; API отвечает этой машине `403 unsupported_country_region_territory`
  — Q-016), ID/цены GPT-6 (Q-004), CI на GitHub для этих изменений (не запускался — изменения не закоммичены).
- **Следующее — 3.5:** схема `ThumbnailAudit` + промпт `thumbnail_analysis/v1`, `scoring_profiles`/`overall_score`
  кодом, `thumbnail_analyses` + кэш SHA-256/pHash, задача анализа с `confirm_token`, UI результатов. Нужен рабочий
  AI-провайдер (Q-016) — до этого на mock.

**VERIFICATION STATUS (этап архитектуры):**
- Verified: версии пакетов (PyPI/npm), Procrastinate на Windows + PG16 (spike), квоты YouTube (официальная документация), модели gpt-image-2.5-flare/sunburst и линейка GPT-6 (документация OpenAI).
- Unverified: API ID/цены GPT-6 (нет ключа), Docker-сборка (нет Docker Engine). (TypeScript: зафиксирован 5.9 — Q-011.)
