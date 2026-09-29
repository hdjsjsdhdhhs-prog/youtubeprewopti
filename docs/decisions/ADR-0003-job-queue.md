# ADR-0003: Background jobs — Procrastinate (PostgreSQL-based queue)

## Status
Accepted

## Context
Требования (§31–33, §73–74): отдельные типы задач; статусы queued/running/completed/failed/retrying/cancelled; приоритеты; retry; идемпотентность; отмена очереди; бюджет; задача не должна теряться при недоступности AI.

Ограничения среды (см. `ENVIRONMENT.md`): Windows Server без Docker и без официального Redis. Production — Linux + Docker Compose.

## Decision
**Procrastinate 3.10** (очередь на PostgreSQL: `SELECT … FOR UPDATE SKIP LOCKED` + `LISTEN/NOTIFY`) + собственная бизнес-таблица **`job_runs`** поверх неё.

- Procrastinate отвечает за доставку: постановка, приоритет, retry с backoff, блокировки, отмена, периодические задачи.
- `job_runs` отвечает за продуктовый уровень: тип, прогресс (N из M), fingerprint для идемпотентности, оценка/факт стоимости, бюджет, человекочитаемая ошибка, связь с проектом. UI читает только `job_runs`.
- Постановка задачи — в той же транзакции, что и изменение бизнес-данных (транзакционный outbox «бесплатно», т. к. очередь в той же БД).
- Очереди: `interactive` (ручная генерация, интерактивный анализ — HIGH), `bulk` (NORMAL), `monitoring` (LOW), `outreach` (отдельно, чтобы отправка не блокировалась анализом). Внутри очереди — числовой `priority`.

### Проверено spike-тестом на этой машине (Windows + PG 16.15, 2026-09-24)
| Свойство | Результат |
|---|---|
| Применение схемы | OK |
| Приоритеты | порядок `high(10) → normal(0) → … → low(-10)` соблюдён |
| Retry | задача с 2 падениями выполнена с 3-й попытки |
| Отмена | `cancel_job_by_id` → задача не выполнена |
| Дедупликация `queueing_lock` | повторная постановка → `AlreadyEnqueued` |
| Signal handling | «Skipping signal handling, does not work on Windows» — graceful shutdown по Ctrl+C на Windows ограничен |

**Найденный дефект окружения:** при `lc_messages=Russian_Russia.1251` Procrastinate не распознаёт unique violation (парсит английский текст ошибки) → `AssertionError` вместо `AlreadyEnqueued`. Исправлено: `ALTER SYSTEM SET lc_messages TO 'C'`. Требование зафиксировано в `DEPLOYMENT.md` / docker-compose (`LC_MESSAGES=C`), и приложение проверяет `SHOW lc_messages` при старте.

## Alternatives
| Вариант | Почему не выбран |
|---|---|
| Celery + Redis | Windows официально не поддерживается с Celery 4; нужен Redis (недоступен без Docker); нет транзакционной постановки вместе с данными. |
| RQ | Использует `fork` — не работает на Windows; нужен Redis. |
| Dramatiq + Redis/RabbitMQ | Хорош, но требует брокер — +1 сервис. |
| arq | Требует Redis; меньше функциональности (приоритеты ограничены). |
| Своя очередь на `SKIP LOCKED` | Реально, но пришлось бы писать retry/locks/periodic/cancel самостоятельно. Procrastinate — это и есть такая очередь, протестированная. |
| Temporal / Hatchet | Мощные workflow-движки, но отдельный сервер и сложность, избыточны. |

## Why
Нулевой дополнительный сервис; атомарность данных и задач; все нужные свойства подтверждены на целевой ОС.

## Trade-offs
+ Нет Redis; бэкапы БД включают очередь; транзакционная постановка.
− Пропускная способность ограничена PostgreSQL (сотни–тысячи задач/мин — многократно выше потребности: узкое место — квоты YouTube/OpenAI).
− Нагрузка очереди делит ресурсы с основной БД.

## Risks
- Graceful shutdown на Windows. Митигировать: задачи идемпотентны; зависшие задачи возвращаются через `retry_stalled_jobs` (периодическая задача).
- Зависимость от формата сообщений PG (см. дефект выше).

## Consequences
Отдельный процесс `worker`: `python -m app.workers`. Все задачи идемпотентны по fingerprint.

## Dependencies
procrastinate, psycopg[pool] (уже драйвер БД).

## Migration
Задачи объявляются через тонкий слой `app/workers/registry.py`; бизнес-логика в сервисах. Смена на Dramatiq/Celery — замена декораторов и `defer`, `job_runs` не меняется. Средняя сложность.

## Date
2026-09-24
