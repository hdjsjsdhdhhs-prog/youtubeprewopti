# ARCHITECTURE CHANGELOG

| Date | Change | Reason | Affected modules | Migration required | Backward compatibility |
|---|---|---|---|---|---|
| 2026-09-24 | Исходная архитектура: модульный монолит (FastAPI + Procrastinate worker + Next.js), PostgreSQL 16 | Первичное архитектурное решение | все | нет (начальная) | — |
| 2026-09-24 | Очередь задач на PostgreSQL (Procrastinate) вместо Redis + worker из исходного ТЗ | Нет Docker/официального Redis на Windows; Celery/RQ не поддерживают Windows; транзакционная постановка | workers, jobs, infrastructure | нет | — |
| 2026-09-24 | Отдельный кэш-сервис не вводится; кэш в таблицах PostgreSQL | Минимизация сервисов (§77K) | analysis, youtube | нет | — |
| 2026-09-24 | PostgreSQL сервер: `lc_messages='C'` | Procrastinate не распознаёт unique violation при локализованных сообщениях (найдено spike-тестом); читаемые логи | infrastructure, jobs | нет | да |
| 2026-09-24 | `FILTERED` — не хранимое состояние лида, `MANUALLY_EDITED` — флаг оффера, добавлен `REJECTED` | Корректная модель состояний (ADR-0011) | leads, offers | нет | — |
| 2026-09-24 | Dev-окружение без Docker: нативные PostgreSQL/Python/Node; docker-compose — для production | Отсутствие аппаратной виртуализации в ВМ | infrastructure, deployment | нет | — |
| 2026-10-02 | Phase 3.1–3.4: метрики превью считаются в задаче `thumbnail_download`; AI-слой (`providers/ai`, `domains/ai`) с резервированием стоимости в `ai_calls`; бюджеты scope `task` вместо `job_type`; один протокол провайдера (ADR-0007 notes) | Один проход по превью; бюджеты видят параллельные вызовы; лимит относится к AI-задаче | media, workers, ai, analysis, projects | да (`4c8e1a7d2f90`) | да: `thumbnail_download` теперь берёт и скачанные превью без метрик; `job_runs` cost-колонки расширены до NUMERIC(14,6) |
