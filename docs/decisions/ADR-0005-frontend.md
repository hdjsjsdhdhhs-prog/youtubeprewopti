# ADR-0005: Frontend — Next.js + TypeScript + TanStack

## Status
Accepted

## Context
UI — профессиональный SaaS-дашборд для работы с большими массивами (тысячи каналов, галереи превью), быстрые фильтры, bulk selection, виртуализация, keyboard shortcuts (§62), чёткая архитектура data fetching / mutation / cache invalidation, обязательные loading/empty/error состояния (§65). Секреты не должны попадать во фронтенд (§58).

## Decision
- **Next.js 16 (App Router) + React 19 + TypeScript**. Страницы — преимущественно client components внутри аутентифицированного layout (это приложение-инструмент, SEO не нужен). Next используется как хост SPA-подобного UI и **reverse proxy**: `/api/*` → FastAPI (rewrites), чтобы cookie-сессия была same-origin (проще CSRF, нет CORS).
- **TanStack Query** — весь серверный state (кэш, инвалидация по query keys, оптимистичные мутации для статусов/тегов).
- **TanStack Table + TanStack Virtual** — таблицы лидов/каналов с виртуализацией.
- **openapi-typescript + openapi-fetch** — типизированный клиент, генерируемый из OpenAPI FastAPI (одна схема → нет рассинхрона).
- **Tailwind CSS 4** + **shadcn/ui** (компоненты копируются в репозиторий, не runtime-зависимость) на базе Radix.
- Состояние фильтров — в URL search params (шаринг, back/forward); локальный UI state — React state; глобальный клиентский store не вводится, пока не появится реальная необходимость.
- **zod** — валидация форм.

## Alternatives
| Вариант | Почему не выбран |
|---|---|
| Vite + React SPA | Легче и быстрее собирается (важно при 4 ГБ RAM); реальная альтернатива. Next выбран из-за встроенного routing/proxy/layout и соответствия ТЗ; если сборка Next окажется неприемлемо медленной на dev-машине — пересмотреть (OPEN_QUESTIONS Q-012). |
| Remix / React Router 7 | Хорош, но меньше экосистема shadcn/примеров под App Router. |
| SvelteKit / Vue | Команда/ТЗ ориентированы на React. |
| Redux / Zustand | Серверный state покрывает TanStack Query; глобальный клиентский store не нужен. |
| MUI / Ant Design | Тяжелее, сложнее кастомизировать под плотный дашборд. |

## Why
Зрелый стек для data-heavy дашбордов; типобезопасность от БД до UI через OpenAPI.

## Trade-offs
+ Типизированный end-to-end контракт, быстрая разработка UI.
− Next.js тяжелее Vite по сборке и памяти.
− TypeScript 7.0 (нативный компилятор) — latest в npm; совместимость с Next 16 проверяется при scaffold, версия TS фиксируется той, что поддерживает Next (Q-011).

## Risks
- Память при `next build` на 4 ГБ RAM.

## Consequences
`frontend/` с `npm run gen:api` (генерация типов из `http://127.0.0.1:8000/api/openapi.json`). Адрес API для прокси — `YTL_API_ORIGIN`, вычисляется при `next build` (для деплоя задаётся на этапе сборки).

## Dependencies
next, react, @tanstack/react-query, @tanstack/react-table, @tanstack/react-virtual, openapi-fetch, openapi-typescript (dev), tailwindcss, zod, vitest, @playwright/test.

## Migration
Бизнес-логика на backend; фронтенд заменим (API-first). Средняя сложность.

## Date
2026-09-24
