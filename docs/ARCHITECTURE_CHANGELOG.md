# ARCHITECTURE CHANGELOG

| Date | Change | Reason | Affected modules | Migration required | Backward compatibility |
|---|---|---|---|---|---|
| 2026-09-24 | Исходная архитектура: модульный монолит (FastAPI + Procrastinate worker + Next.js), PostgreSQL 16 | Первичное архитектурное решение | все | нет (начальная) | — |
| 2026-09-24 | Очередь задач на PostgreSQL (Procrastinate) вместо Redis + worker из исходного ТЗ | Нет Docker/официального Redis на Windows; Celery/RQ не поддерживают Windows; транзакционная постановка | workers, jobs, infrastructure | нет | — |
| 2026-09-24 | Отдельный кэш-сервис не вводится; кэш в таблицах PostgreSQL | Минимизация сервисов (§77K) | analysis, youtube | нет | — |
| 2026-09-24 | PostgreSQL сервер: `lc_messages='C'` | Procrastinate не распознаёт unique violation при локализованных сообщениях (найдено spike-тестом); читаемые логи | infrastructure, jobs | нет | да |
| 2026-09-24 | `FILTERED` — не хранимое состояние лида, `MANUALLY_EDITED` — флаг оффера, добавлен `REJECTED` | Корректная модель состояний (ADR-0011) | leads, offers | нет | — |
| 2026-09-24 | Dev-окружение без Docker: нативные PostgreSQL/Python/Node; docker-compose — для production | Отсутствие аппаратной виртуализации в ВМ | infrastructure, deployment | нет | — |
