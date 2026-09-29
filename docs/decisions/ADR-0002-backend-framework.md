# ADR-0002: Backend — Python 3.12 + FastAPI + Pydantic v2

## Status
Accepted

## Context
Backend должен: обслуживать REST API для UI; выполнять обработку изображений (метрики контраста, pHash, ресайз); работать с OpenAI и YouTube API; валидировать структурированный JSON от AI по строгим схемам (§45); иметь хорошую экосистему тестирования.

## Decision
- **Python 3.12**, **FastAPI 0.141**, **Pydantic 2.13**, **pydantic-settings** для конфигурации, **Uvicorn** как ASGI-сервер.
- Async-обработчики для I/O (БД, HTTP к провайдерам). CPU-тяжёлая обработка изображений — только в воркере (`asyncio.to_thread`).
- OpenAPI-схема FastAPI — источник истины для типов фронтенда (ADR-0005).

## Alternatives
| Вариант | Почему не выбран |
|---|---|
| Django + DRF / Django Ninja | Сильная админка и ORM, но: синхронное ядро, собственный ORM вместо SQLAlchemy/Alembic, менее естественная интеграция Pydantic-схем для AI-валидации. Разумная альтернатива, но FastAPI + Pydantic лучше ложится на schema-first AI pipeline. |
| Litestar | Технически сильный, но меньше экосистема и сообщество. |
| Node.js (NestJS/Fastify) | Единый язык с фронтом, но слабее экосистема обработки изображений/ML (Pillow, NumPy, OpenCV, imagehash). |
| Go | Производительность не является узким местом (узкое место — внешние API), экосистема AI/изображений беднее. |

## Why
Python — де-факто стандарт для AI/изображений; Pydantic одновременно даёт валидацию API, валидацию AI-вывода и JSON Schema для structured outputs OpenAI — одна модель → три применения.

## Trade-offs
+ Одна схема Pydantic = API контракт + AI-схема + документация.
− GIL: CPU-задачи параллелятся процессами воркера, а не потоками.
− Python 3.12, не 3.13 — ради гарантированной доступности бинарных wheels на Windows.

## Risks
- Смешение sync/async кода в воркере. Митигировать: сервисы async; блокирующее — через `to_thread`.

## Consequences
`backend/pyproject.toml`, ruff (lint+format), mypy (strict на `domains` и `providers`).

## Dependencies
fastapi, uvicorn, pydantic, pydantic-settings, httpx.

## Migration
Смена веб-фреймворка затрагивает только слой `api/` (тонкие роутеры) — низкая/средняя сложность.

## Date
2026-09-24
