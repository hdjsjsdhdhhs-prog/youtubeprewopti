# ADR-0013: Deployment & CI

## Status
Accepted (с ограничением: Docker-запуск на dev-машине невозможен — см. ENVIRONMENT.md)

## Context
§63: Docker Compose для локального запуска, production Docker-ready. Реальная dev-среда: Windows Server без виртуализации → Linux-контейнеры не запускаются.

## Decision
- **Dev (эта машина)**: нативно — PostgreSQL служба, `backend/.venv`, Node. Скрипты `scripts/dev.ps1` (api + worker + web), `scripts/setup.ps1` (venv, npm install, создание БД/пользователя, миграции).
- **Production / Linux**: `docker-compose.yml` с сервисами `postgres` (16, `LC_MESSAGES=C`), `api`, `worker` (один образ backend), `web` (Next standalone), volume для `storage/` и данных PG. Multi-stage Dockerfile, non-root user.
- Проверка compose на dev-машине: только `docker compose config` (синтаксис/интерполяция). Сборка и запуск образов — **UNVERIFIED** до появления Docker-хоста.
- **CI (GitLab CI)**, когда появится репозиторий: `lint` (ruff, mypy, eslint, tsc), `test-backend` (pytest + service postgres:16), `test-frontend` (vitest), `build` (docker build), `e2e` (Playwright на compose — позже). CI также закроет пробел проверки Docker-сборки.
- Kubernetes, Helm — не используются (§77K).

## Alternatives
- Только Docker для dev — невозможно на текущей машине.
- Windows-контейнеры — нет официальных образов PostgreSQL/Node для нашего стека, неприменимо для production.

## Why
Разработка не блокируется отсутствием Docker; production-путь остаётся стандартным.

## Trade-offs
− Расхождение dev (Windows) и prod (Linux): пути, кодировки, сигналы. Митигировать: `pathlib`, UTF-8 везде, CI на Linux.

## Risks
- Docker-артефакты могут содержать ошибки, не обнаруженные локально — до CI/Docker-хоста помечаются UNVERIFIED.

## Consequences
Все пути через `pathlib`; конфигурация только через env (`.env.example`).

## Dependencies
Docker Compose v5 (CLI установлен), GitLab CI runner (при наличии).

## Migration
—

## Date
2026-09-24
