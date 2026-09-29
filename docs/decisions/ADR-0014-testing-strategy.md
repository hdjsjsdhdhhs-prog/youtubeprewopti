# ADR-0014: Testing strategy

## Status
Accepted

## Context
§66: unit, integration, API, DB, worker, AI schema validation, provider adapter, e2e тесты; особенно: deduplication, score calculation, filtering, state machine, manual approval, duplicate-send prevention, retry, contact normalization, idempotency. §67: mock providers.

## Decision
**Backend**
- `pytest` + `pytest-asyncio`; API-тесты через `httpx.AsyncClient(ASGITransport(app))`.
- **Реальный PostgreSQL**: тестовая БД `ytlead_test`, схема через Alembic `upgrade head` один раз за сессию, каждый тест — в транзакции с откатом (SAVEPOINT). Это также проверяет миграции.
- Внешние HTTP — `respx` (YouTube, OpenAI, Telegram, VK) с записанными фикстурами ответов, включая 401/403/429/5xx/timeout.
- Worker-тесты: задачи вызываются напрямую + отдельный интеграционный тест с Procrastinate `InMemoryConnector` и с реальной PG-очередью.
- AI schema tests: набор валидных/невалидных JSON (score 11, 0, отсутствующее поле, строка вместо числа) → проверка repair/отклонения.
- Property-based тесты (`hypothesis`) для scoring (монотонность, границы 1–10 / 0–100) и нормализации контактов.

**Frontend**
- `vitest` + Testing Library (компоненты фильтров, таблиц, approval-карточек).
- `Playwright` e2e: login → проект → demo discovery (mock) → фильтр → shortlist → генерация (mock) → оффер → approve → отправка (mock) → статус.

**Обязательные сценарии** (из §66) оформляются как отдельные тест-модули с понятными названиями, чтобы покрытие было видно по структуре.

## Alternatives
- SQLite для тестов — отвергнуто (другое поведение constraints/JSONB/locking).
- testcontainers — требует Docker (недоступен).
- VCR.py — respx с явными фикстурами прозрачнее.

## Why
Тесты на той же СУБД, что и production; внешние API детерминированы.

## Trade-offs
− Тесты требуют запущенный PostgreSQL (есть локально и в CI как service).

## Risks
- Медленность на 2 vCPU — транзакционный откат вместо пересоздания БД.

## Consequences
`backend/tests/{unit,integration,api,workers,providers}`; `frontend/tests`, `frontend/e2e`.

## Dependencies
pytest, pytest-asyncio, respx, hypothesis, vitest, @testing-library/react, @playwright/test.

## Migration
—

## Date
2026-09-24
