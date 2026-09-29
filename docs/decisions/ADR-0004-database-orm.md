# ADR-0004: Database — PostgreSQL 16 + SQLAlchemy 2.0 + Alembic

## Status
Accepted

## Context
Нужна нормализованная реляционная модель (~40 таблиц, §30), сложные комбинированные фильтры по числовым полям (§3, §9), JSON для AI-метаданных, уникальные ограничения для идемпотентности (§32), очередь задач (ADR-0003), миграции (§69).

## Decision
- **PostgreSQL 16** (нативно на dev, `postgres:16` в production compose).
- **SQLAlchemy 2.0** (typed `Mapped[]` модели), драйвер **psycopg 3** — один драйвер для ORM (sync+async) и Procrastinate.
- **Alembic** — все изменения схемы только через миграции; autogenerate + ручная проверка.
- Основные поля — обычные колонки с индексами; `JSONB` только для raw AI output, provider-specific полей, настроек.
- Все score-поля, используемые в фильтрах (subscores, overall, lead score components), — отдельные числовые колонки с `CHECK (x BETWEEN 1 AND 10)`.
- Расширения: `pg_trgm` (поиск по названию канала). `citext` — для email/handle.
- Отдельный пользователь БД приложения (не superuser); `lc_messages='C'`.
- Кэш: отдельный кэш-сервис (Redis) **не вводится**. Кэш AI-анализа — таблица (по SHA-256 изображения), кэш YouTube-ответов — таблица с TTL.

## Alternatives
| Вариант | Почему не выбран |
|---|---|
| MySQL/MariaDB | Слабее JSONB, частичные индексы, `SKIP LOCKED`/`LISTEN` экосистема для очереди. |
| SQLite | Нет конкурентной записи воркеров, нет LISTEN/NOTIFY. |
| MongoDB | ТЗ требует нормализованную реляционную модель. |
| SQLModel | Обёртка над SQLAlchemy; меньше контроля, отстаёт от SQLAlchemy. |
| asyncpg | Быстрее, но второй драйвер параллельно с psycopg (нужен Procrastinate). |
| Django ORM / Tortoise | Связаны с другим стеком / слабее. |

## Why
PostgreSQL закрывает хранение, очередь, кэш и поиск — меньше сервисов. SQLAlchemy 2 — самый зрелый ORM Python с полноценным контролем SQL для сложных фильтров.

## Trade-offs
+ Один сервис данных, ACID для всего.
− PostgreSQL становится single point of failure (бэкапы — `pg_dump` по расписанию, см. `DEPLOYMENT.md`).

## Risks
- Рост таблиц снапшотов статистики. Митигировать: снапшоты по дням; при необходимости — партиционирование по месяцу (не сейчас).

## Consequences
Тесты БД — на реальном PostgreSQL (отдельная тестовая БД), не SQLite.

## Dependencies
sqlalchemy, alembic, psycopg[binary,pool].

## Migration
Смена СУБД маловероятна; ORM-уровень снижает стоимость, но очередь и JSONB привязаны к PostgreSQL.

## Date
2026-09-24
