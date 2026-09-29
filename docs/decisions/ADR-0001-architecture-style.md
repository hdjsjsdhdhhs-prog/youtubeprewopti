# ADR-0001: Architecture style — modular monolith

## Status
Accepted

## Context
Система включает ~15 функциональных доменов (discovery, enrichment, thumbnail analysis, scoring, generation, contacts, offers, outreach, campaigns, analytics и др.), асинхронную обработку и внешние интеграции. Разработка ведётся одним разработчиком + AI-агентом, эксплуатация — self-hosted на одной машине (сейчас: 2 vCPU / 4 ГБ RAM, Windows Server, без Docker — см. `ENVIRONMENT.md`). ТЗ (§77K) явно запрещает микросервисы и тяжёлую инфраструктуру без доказанной необходимости, но требует слабой связанности и заменяемости модулей (§72, §91).

## Decision
**Модульный монолит** из трёх процессов:

| Процесс | Технология | Роль |
|---|---|---|
| `api` | FastAPI (Python) | REST API, auth, валидация, постановка задач |
| `worker` | Procrastinate worker (тот же Python-пакет) | все фоновые задачи |
| `web` | Next.js | UI |

Общая БД — PostgreSQL (данные + очередь). Backend — один Python-пакет `app` с доменными модулями:

```
app/
  core/          config, db, security, logging, errors, state machine base
  domains/
    identity/    users, workspaces, sessions, RBAC
    projects/    search projects, queries, taxonomy
    youtube/     channels, videos, stats, metrics
    media/       image assets, thumbnails, storage
    analysis/    image metrics, thumbnail/channel AI analysis
    scoring/     thumbnail/lead/priority/contact scoring, profiles
    leads/       leads, statuses, tags, favorites, overrides, feedback
    contacts/    enrichment, normalization, suppression
    generation/  references, generation requests/variants
    offers/      templates, offers, variants, approvals
    outreach/    messages, events, accounts, quotas
    campaigns/   campaigns, A/B tests
    analytics/   aggregates, dashboards
    jobs/        job runs, budgets, cost estimates
  providers/     adapters: youtube, ai (text/vision/image), email, telegram, vk, storage — + mock
  api/           HTTP routers (thin)
  workers/       task definitions (thin, call domain services)
```

Правила связности:
- Роутеры и задачи — тонкие, бизнес-логика только в `domains/*/service.py`.
- Домены не импортируют адаптеры напрямую — только протоколы (интерфейсы) из `providers/base`. Конкретный адаптер выбирается в composition root по конфигурации.
- Домен обращается к таблицам другого домена только через его сервис (исключение — read-only аналитические запросы в `analytics`).

## Alternatives
1. **Микросервисы** (discovery / analysis / outreach отдельно) — отвергнуто: сетевые контракты, распределённые транзакции и деплой нескольких сервисов на 4 ГБ RAM без выигрыша при текущей нагрузке.
2. **Монолит без модулей** (всё в одном слое) — отвергнуто: не выдержит 9 фаз развития, нарушает §91.
3. **Serverless** — отвергнуто: self-hosted требование.

## Why
Одна кодовая база, одна БД, транзакционная согласованность (постановка задачи в той же транзакции, что и смена статуса), простая отладка. Модульные границы позволяют позже вынести, например, воркер генерации на отдельную машину — это уже отдельный процесс.

## Trade-offs
+ Простота деплоя, отладки, тестирования; атомарность.
− Все модули масштабируются вместе (частично снимается разными очередями и числом воркеров).
− Дисциплина границ модулей держится на код-ревью и линтере импортов.

## Risks
- Размывание границ модулей со временем. Митигировать: `import-linter` контракты в CI (добавить в Phase 1 при необходимости).

## Consequences
Единый `pyproject.toml`, единый набор миграций Alembic, один Docker-образ backend для `api` и `worker`.

## Dependencies
ADR-0002 (backend), ADR-0003 (queue), ADR-0004 (DB).

## Migration
Вынос модуля в сервис: модуль уже изолирован сервисным слоем и протоколами → замена in-process вызова на HTTP/очередь. Средняя сложность.

## Date
2026-09-24
