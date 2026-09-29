# DEPENDENCIES

Ключевые библиотеки. Правило: не добавлять зависимость для задачи, решаемой несколькими десятками строк кода. Точные версии фиксируются в `backend/pyproject.toml` + lock и `frontend/package-lock.json` при scaffold.

## Backend (Python)

| Library | Version | Purpose | Why | Criticality | Alternatives | Compatibility notes |
|---|---|---|---|---|---|---|
| fastapi | 0.141.x | HTTP API | Pydantic-native | High | Litestar, Django Ninja | Следить за breaking changes в minor-версиях (0.x) |
| uvicorn[standard] | 0.53.x | ASGI server | Стандарт | High | Hypercorn, Granian | На Windows без uvloop (не поддерживается) — стандартный loop |
| pydantic | 2.13.x | Schemas | API + AI validation | High | msgspec, attrs | — |
| pydantic-settings | 2.15.x | Config | Типизированный env | Medium | dynaconf | — |
| sqlalchemy | 2.0.x | ORM | Зрелость | High | SQLModel | — |
| alembic | 1.20.x | Migrations | Стандарт | High | — | — |
| psycopg[binary,pool] | 3.3.x | PG driver | sync+async, нужен Procrastinate | High | asyncpg | Async на Windows требует `WindowsSelectorEventLoopPolicy` (проверено) |
| procrastinate | 3.10.x | Job queue | PG-based | High | Dramatiq, Celery | Требует `lc_messages='C'` на сервере (проверено); signal handling ограничен на Windows |
| httpx | 0.28.x | HTTP client | async, тестируемость | High | aiohttp | Прокси берётся из `HTTP(S)_PROXY` |
| openai | 3.19.x | OpenAI SDK | Официальный | High | raw httpx | Только внутри `providers/ai/openai_*` |
| pillow | 12.3.x | Images | Метрики, ресайз, валидация загрузок | High | pyvips | — |
| numpy | 2.5.x | Image metrics | Векторные вычисления | Medium | — | — |
| imagehash | 4.3.x | pHash | Дедупликация | Medium | своя реализация pHash (~40 строк) | Тянет scipy/PyWavelets — при избыточности заменить своей реализацией DCT-pHash |
| jinja2 | 3.x | Prompt templates | Шаблоны | Medium | string.Template | — |
| argon2-cffi | 25.1.x | Passwords | Argon2id | High | bcrypt | — |
| cryptography | 50.0.x | AES-GCM | Секреты | High | PyNaCl | — |
| structlog | 26.1.x | Logging | JSON logs | Medium | stdlib logging + json formatter | — |
| aiosmtplib | 5.1.x | SMTP | async email (Phase 8) | Medium | smtplib в to_thread | — |
| boto3 (optional `[s3]`) | 1.43.x | S3 storage | Только при S3 | Low | aioboto3, minio | Большой пакет — поэтому опционально |
| telethon (optional) | 1.45.x | Telegram MTProto | Только после Q-001 | Low | Pyrogram (не поддерживается активно) | Риски ToS — ADR-0010 |
| opencv-python-headless (deferred) | 5.0.x | Лица/салиентность | Только если понадобится (Q-009) | Low | mediapipe | ~50 МБ — не добавлять без необходимости |

Dev: pytest 9.1, pytest-asyncio 1.4, respx, hypothesis, ruff 0.16, mypy 2.3.

**Отвергнуты:** google-api-python-client (тяжёлый/синхронный для 4 эндпоинтов), celery/rq/arq/dramatiq (ADR-0003), langchain (ADR-0007), python-statemachine (ADR-0011), redis.

## Frontend (npm)

| Library | Version | Purpose | Criticality | Alternatives | Notes |
|---|---|---|---|---|---|
| next | 16.3.x | Framework | High | Vite + React Router | Память при build на 4 ГБ |
| react / react-dom | 19.3.x | UI | High | — | — |
| typescript | по совместимости с Next | Types | High | — | latest 7.0.2 (Q-011) |
| @tanstack/react-query | 5.103.x | Server state | High | SWR | — |
| @tanstack/react-table | 9.2.x | Tables | High | AG Grid | v9 — проверить API при scaffold |
| @tanstack/react-virtual | 3.14.x | Virtualization | Medium | react-window | — |
| openapi-fetch / openapi-typescript | 7.13.x | Typed API client | High | orval | — |
| tailwindcss | 4.3.x | Styles | Medium | CSS modules | — |
| radix-ui + shadcn/ui | copy-in | Components | Medium | MUI | — |
| zod | 4.6.x | Form validation | Medium | valibot | — |
| vitest, @testing-library/react | 5.0.x | Tests | Medium | jest | — |
| @playwright/test | 1.63.x | E2E | Medium | Cypress | Браузеры скачиваются отдельно (~300 МБ) |
