# ADR-0011: State machines & idempotency

## Status
Accepted

## Context
§10: lead lifecycle через state machine (16 состояний). §29: статусы кампаний. §31: статусы задач. §32: повторный запуск не создаёт дубликатов.

## Decision
**State machines** — явные таблицы переходов в коде (`app/core/state_machine.py`, без внешних библиотек): `StateMachine(transitions: dict[State, set[State]], guards)`. Переход выполняется только через сервис `transition(entity, to_state, actor, reason)`, который:
1. проверяет допустимость перехода и guards (например, `PENDING_APPROVAL → APPROVED` требует роль ≥ operator);
2. обновляет статус с оптимистичной блокировкой (`WHERE status = :from`);
3. пишет историю (`lead_status_history` / `offer_status_history`) и audit log.

Разделение статусов (в ТЗ они смешаны в одном списке):
- **Lead** (воронка): `DISCOVERED → ANALYZED → SHORTLISTED → SELECTED → GENERATION_PENDING → GENERATED → OFFER_DRAFT → PENDING_APPROVAL → APPROVED → SCHEDULED → SENT → REPLIED`; терминальные/боковые: `DECLINED`, `OPTED_OUT`, `FAILED`, `REJECTED` (отклонён оператором).
- `FILTERED` — **не хранимое состояние**, а результат применения фильтров (иначе статус зависел бы от набора фильтров конкретного проекта). В воронке показывается как «прошли фильтры проекта».
- **Offer**: `DRAFT → PENDING_APPROVAL → APPROVED → SCHEDULED → SENT`; `REJECTED`, `FAILED`; флаг `manually_edited` (не статус — оффер может быть отредактирован и одобрен).
- **Message**: `QUEUED → SENDING → SENT → DELIVERED`; `FAILED_RETRYABLE`, `FAILED`, `CANCELLED`; события (opened/replied) — в `message_events`.
- **Campaign**: `DRAFT, READY, PENDING_APPROVAL, RUNNING, PAUSED, COMPLETED, ARCHIVED`.
- **JobRun**: `QUEUED, RUNNING, COMPLETED, FAILED, RETRYING, CANCELLED`.

**Idempotency**
- Натуральные UNIQUE-ограничения: `channels.youtube_channel_id`, `videos.youtube_video_id`, `image_assets.sha256`, `thumbnail_analyses(image_asset_id, prompt_template_id, model_key)`, `contacts(channel_id, type, value_normalized)`, `messages.idempotency_key`, `channel_discoveries(project_id, channel_id, query_id, source_video_id)`.
- Upsert (`INSERT … ON CONFLICT`) во всех ingestion-задачах.
- `job_runs.fingerprint` = sha256(type + нормализованные параметры); partial UNIQUE для активных статусов → повторный запуск того же job при активном возвращает существующий; + Procrastinate `queueing_lock`.
- HTTP: заголовок `Idempotency-Key` для дорогих POST (генерация, запуск discovery, approve/send) → таблица `idempotency_keys` (ключ, hash запроса, сохранённый ответ, TTL).

## Alternatives
- `python-statemachine` / `transitions` — лишняя зависимость для простых таблиц переходов; хуже интеграция с транзакциями БД.
- Хранение `FILTERED` как статуса — отвергнуто (см. выше).

## Why
Инварианты проверяются в одном месте и полностью покрываются unit-тестами.

## Trade-offs
+ Прозрачность, тестируемость.
− Небольшое отклонение от буквального списка статусов ТЗ (FILTERED, MANUALLY_EDITED, REJECTED) — обосновано выше.

## Risks
- Рассинхрон статуса лида и оффера. Митигировать: статус лида обновляется событиями оффера в той же транзакции.

## Consequences
Unit-тесты на каждую таблицу переходов (все запрещённые переходы).

## Dependencies
Нет.

## Migration
Добавление состояния = изменение таблицы переходов + миграция enum. Низкая сложность.

## Date
2026-09-24
