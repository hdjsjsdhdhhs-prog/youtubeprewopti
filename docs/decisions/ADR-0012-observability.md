# ADR-0012: Observability — structured logs + queryable operational tables

## Status
Accepted

## Context
§61: structured/job/AI/integration/generation/error/audit логи и дашборд Jobs/Queue/Errors/Retries/API Usage/AI Usage. §60: человекочитаемые ошибки. §75: стоимость каждой AI-операции.

## Decision
- **structlog** → JSON в stdout (и в файл с ротацией на Windows dev). Контекст: `request_id`, `job_run_id`, `workspace_id`, `provider`.
- **Операционные данные — таблицы PostgreSQL**, которые UI читает напрямую:
  - `job_runs` (статусы, прогресс, retries, ошибки),
  - `ai_calls` (provider, model, prompt version, tokens, images, estimated/actual cost, duration, status, validation errors),
  - `integration_events` (вызовы внешних API: статус-код, классифицированная ошибка, latency),
  - `youtube_quota_ledger`,
  - `audit_logs`.
- Ошибки интеграций классифицируются в коды (`AUTH_EXPIRED`, `RATE_LIMITED`, `QUOTA_EXCEEDED`, `FORBIDDEN`, `PROVIDER_UNAVAILABLE`, `INVALID_CONTACT`, `TIMEOUT`, `UNKNOWN`) + `human_message` (например: «Telegram account session expired. Re-authentication required.»).
- Периодическая очистка: `integration_events` старше N дней, агрегаты сохраняются.

## Alternatives
| Вариант | Почему не выбран |
|---|---|
| Prometheus + Grafana + Loki | Стандарт для крупных систем, но +3 сервиса; на 4 ГБ RAM и одном пользователе избыточно. Экспорт метрик `/metrics` можно добавить позже. |
| Sentry | Полезен для ошибок; опционально через `SENTRY_DSN` позже. |
| OpenTelemetry | Можно добавить при выносе сервисов. |

## Why
Данные уже в БД → дашборды в самом приложении без новых сервисов; стоимость и ошибки связаны с бизнес-сущностями SQL-джойнами.

## Trade-offs
+ Нет дополнительной инфраструктуры.
− Нет алертинга «из коробки» (частично — Telegram-уведомления владельцу, §57).

## Risks
- Рост `ai_calls`/`integration_events` — ретенция + индексы по времени.

## Consequences
Страница Ops в UI (Phase 1: базовая таблица jobs).

## Dependencies
structlog.

## Migration
Добавление OTel/Prometheus не требует изменения доменной логики.

## Date
2026-09-24
