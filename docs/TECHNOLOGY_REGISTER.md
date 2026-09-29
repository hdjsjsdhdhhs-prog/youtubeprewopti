# TECHNOLOGY REGISTER

Актуальные версии проверены в реестрах PyPI/npm и установленных пакетах 2026-09-24. При изменении — обновить этот файл и `ARCHITECTURE_CHANGELOG.md`.

| Component | Technology | Version | Purpose | Reason | ADR |
|---|---|---|---|---|---|
| Architecture | Modular monolith (api + worker + web) | — | Структура системы | Простота, атомарность, соответствие нагрузке | 0001 |
| Backend language | Python | 3.12.10 | API, workers, обработка изображений | Экосистема AI/изображений; стабильные wheels на Windows | 0002 |
| Backend framework | FastAPI | 0.141.x | REST API, OpenAPI | Pydantic-native, async | 0002 |
| Validation / schemas | Pydantic | 2.13.x | API + AI output schemas | Одна схема → API, JSON Schema, валидация | 0002, 0007 |
| Config | pydantic-settings | 2.15.x | Конфигурация из env | Типизированный конфиг | 0002 |
| ASGI server | Uvicorn | 0.53.x | HTTP-сервер | Стандарт для FastAPI | 0002 |
| Database | PostgreSQL | 16.15 | Данные, очередь, кэш | JSONB, SKIP LOCKED, LISTEN/NOTIFY | 0004 |
| DB driver | psycopg 3 | 3.3.x | Драйвер (sync+async) | Один драйвер для ORM и очереди | 0004 |
| ORM | SQLAlchemy | 2.0.x | Доступ к БД | Зрелость, контроль SQL | 0004 |
| Migrations | Alembic | 1.20.x | Миграции схемы | Стандарт для SQLAlchemy | 0004 |
| Queue | Procrastinate | 3.10.x | Фоновые задачи | PG-based, Windows OK (spike), без Redis | 0003 |
| Cache | PostgreSQL tables | — | Кэш AI/YouTube | Без отдельного сервиса | 0004 |
| Storage | Local FS / S3-compatible | — | Изображения | Абстракция, content-addressed | 0006 |
| Image processing | Pillow + NumPy | 12.3 / 2.5 | Метрики, ресайз, проверка файлов | Стандарт | 0006, AI_ARCH |
| Perceptual hash | imagehash | 4.3.x | Дедупликация визуально идентичных | Простая, проверенная | 0006 |
| HTTP client | httpx | 0.28.x | YouTube, Telegram Bot, VK | async, respx-тестируемость | 0008 |
| AI SDK | openai (official) | 3.19.x | Vision/Text/Image | Официальный SDK | 0007 |
| AI Vision/Text | GPT-6 Sol / Luna / Astra (config) | — | Анализ, классификация, офферы | Документация OpenAI; ID подтвердить (Q-004) | 0007 |
| Image Generation | gpt-image-2.5-flare / -sunburst (config) | — | Генерация превью | Документация OpenAI | 0007 |
| Prompt templates | Jinja2 | 3.x | Версионируемые промпты | Уже зависимость экосистемы | 0007 |
| Auth hashing | argon2-cffi | 25.1.x | Хэширование паролей | Argon2id | 0009 |
| Crypto | cryptography | 50.0.x | AES-GCM секреты | Стандарт | 0009 |
| Logging | structlog | 26.1.x | JSON-логи | Структурированность | 0012 |
| Email | aiosmtplib | 5.1.x | SMTP-отправка | async | 0010 |
| Telegram (user) | Telethon | 1.45.x | MTProto — **только после решения Q-001** | Зрелая библиотека | 0010 |
| Frontend framework | Next.js (App Router) | 16.3.x | UI + /api proxy | Routing/layout/proxy | 0005 |
| UI library | React | 19.3.x | UI | — | 0005 |
| Language (FE) | TypeScript | версия, поддерживаемая Next (Q-011) | Типизация | latest npm = 7.0.2 | 0005 |
| Server state | TanStack Query | 5.103.x | Data fetching/cache | Инвалидация, мутации | 0005 |
| Tables | TanStack Table | 9.2.x | Таблицы | Headless | 0005 |
| Virtualization | TanStack Virtual | 3.14.x | Большие списки | Headless | 0005 |
| Styling | Tailwind CSS | 4.3.x | Стили | — | 0005 |
| Components | shadcn/ui (Radix) | copy-in | UI компоненты | Нет runtime lock-in | 0005 |
| API types | openapi-typescript + openapi-fetch | 7.13.x | Типы из OpenAPI | Единый контракт | 0005 |
| Forms validation | zod | 4.6.x | Формы | — | 0005 |
| Backend tests | pytest, pytest-asyncio, respx, hypothesis | 9.1 / 1.4 / — / — | Тесты | — | 0014 |
| Frontend tests | vitest, Testing Library | 5.0.x | Unit/component | — | 0014 |
| E2E | Playwright | 1.63.x | E2E | — | 0014 |
| Lint/format (Py) | ruff, mypy | 0.16 / 2.3 | Качество | — | 0002 |
| Containers | Docker Compose | v5.5.1 (CLI) | Production deploy | Engine на dev недоступен | 0013 |
| CI | GitLab CI | — | Проверки | Когда появится репозиторий | 0013 |
| VCS | Git | 2.55.0 | Контроль версий | — | — |

**Сознательно не используются:** Redis, Celery, RabbitMQ, Kafka, Elasticsearch, ClickHouse, Kubernetes, LangChain, Prometheus/Grafana (обоснования — в соответствующих ADR).
