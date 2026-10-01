# YT Lead Intelligence

Платформа для поиска YouTube-каналов со слабыми превью, AI-анализа превью, генерации улучшенных вариантов и
подготовки персональных предложений авторам. Каждая отправка проходит через ручное одобрение (Human-in-the-Loop).

**Статус:** Phase 2 (discovery). Готово: авторизация и RBAC, проекты и поисковые запросы, ниши (ниша › тема ›
подтема), поиск каналов через YouTube Data API с учётом квоты (или офлайн-mock), дедупликация каналов и история
обнаружения, метрики каналов и фильтры §3, загрузка и отдача превью, фоновые задачи, UI оператора, демо-данные.
AI-анализ, генерация и outreach — следующие фазы (см. [docs/ENGINEERING_SUMMARY.md](docs/ENGINEERING_SUMMARY.md)).

Поиск каналов включается ключом `YTL_YOUTUBE_API_KEY` в `%USERPROFILE%\.ytlead-secrets\backend.conf` (вне
репозитория); без ключа — `YTL_YOUTUBE_PROVIDER=mock` (синтетические каналы с пометкой «демо»).
CI — GitHub Actions ([.github/workflows/ci.yml](.github/workflows/ci.yml)).

## Архитектура

Модульный монолит ([ADR-0001](docs/decisions/ADR-0001-architecture-style.md)):

| Компонент | Технологии | Где |
|---|---|---|
| API | Python 3.12, FastAPI, SQLAlchemy 2, Pydantic 2 | `backend/app` |
| Worker | Procrastinate (очередь на PostgreSQL) — тот же Python-пакет | `backend/app/workers` |
| Web | Next.js 16, React 19, TanStack Query/Table/Virtual, Tailwind 4 | `frontend/` |
| БД | PostgreSQL 16 — данные, очередь задач, кэш | миграции: `backend/alembic` |
| Файлы | Локальная ФС, content-addressed (SHA-256) | `storage/` (не в git) |

Браузер обращается только к Next; `/api/*` проксируется в FastAPI (cookie-сессия same-origin + CSRF).

```
backend/          API + worker + CLI, тесты (pytest, реальный PostgreSQL)
frontend/         UI оператора (см. frontend/README.md)
infrastructure/   docker-compose для production/Linux
scripts/          setup/dev-скрипты для Windows (без Docker)
docs/             архитектура, ADR, API, развёртывание
```

## Быстрый старт (dev, Windows, без Docker)

Нужны Python 3.12, Node.js 24, PostgreSQL 16 (служба) и `psql` в PATH. Пароль суперпользователя `postgres`
должен лежать в `%USERPROFILE%\.ytlead-secrets\postgres_superuser.txt`.

```powershell
scripts\setup.ps1                 # venv, роли/базы ytlead и ytlead_test, конфиг с секретами, миграции, npm install
scripts\dev.ps1                   # API :8000 + worker + web :3000
```

`setup-db.ps1` (вызывается из `setup.ps1`) пишет URL баз и мастер-ключ в `%USERPROFILE%\.ytlead-secrets\backend.conf`
вне репозитория; `dev.ps1` передаёт его через `YTL_ENV_FILE`. Все переменные описаны в [.env.example](.env.example).

Первый пользователь и (по желанию) демо-данные:

```powershell
cd backend
$env:YTL_ENV_FILE = "$env:USERPROFILE\.ytlead-secrets\backend.conf"
.venv\Scripts\python -m app.cli create-owner --email you@example.com --name "Имя" --workspace "Моя команда"
.venv\Scripts\python -m app.cli seed-demo            # --reset — пересоздать, --remove — удалить
```

Пароль вводится интерактивно (или `--password-stdin`), в аргументах командной строки не передаётся. Демо-данные
синтетические и помечены везде: `[DEMO]` в названиях, ID с префиксом `demo-`, бейдж «демо» в UI.

Открыть: http://localhost:3000 (UI), http://127.0.0.1:8000/api/docs (Swagger).

Параметры `dev.ps1`: `-ApiOnly` (только API на переднем плане), `-NoWorker`, `-ApiPort N`,
`-WebHost` (по умолчанию `127.0.0.1`; не открывайте на другие интерфейсы — см.
[DEPLOYMENT.md](docs/DEPLOYMENT.md#ip-клиента-и-x-forwarded-for-обязательно)).

## Проверки

```powershell
cd backend
.venv\Scripts\python -m pytest -q          # нужен YTL_TEST_DATABASE_URL (берётся из backend.conf); схема пересоздаётся
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m mypy app
cd ..\frontend
npm run lint; npm run typecheck; npm test
```

Тесты backend никогда не работают с dev-базой: `conftest.py` подменяет все URL на `YTL_TEST_DATABASE_URL`.
Тест с реальной загрузкой превью с i.ytimg.com включается через `YTL_LIVE_NETWORK=1`.

## Документация

| Документ | Содержание |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/decisions/](docs/decisions/) | Архитектура и ADR |
| [docs/API.md](docs/API.md) | REST API: авторизация, ошибки, эндпоинты |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Dev-окружение и production через Docker Compose |
| [docs/DATABASE.md](docs/DATABASE.md) | Схема данных |
| [docs/AI_ARCHITECTURE.md](docs/AI_ARCHITECTURE.md), [docs/AI_PIPELINE.md](docs/AI_PIPELINE.md) | AI-модули и конвейер |
| [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md) | Внешние сервисы |
| [docs/ENVIRONMENT.md](docs/ENVIRONMENT.md) | Состояние dev-машины и ограничения (нет Docker Engine) |
| [docs/OPEN_QUESTIONS.md](docs/OPEN_QUESTIONS.md) | Открытые вопросы и риски |
| [docs/ENGINEERING_SUMMARY.md](docs/ENGINEERING_SUMMARY.md) | Сводка и прогресс по фазам |
