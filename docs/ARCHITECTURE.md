# ARCHITECTURE

AI YouTube Thumbnail Lead Intelligence & Outreach Platform — архитектурный обзор. Детали решений — `docs/decisions/ADR-*.md`.

## 1. Контекст

```
            ┌──────────────────────── Operator (browser) ────────────────────────┐
            │                                                                    │
            ▼                                                                    │
   ┌─────────────────┐   /api/* (same-origin proxy)   ┌──────────────────────┐   │
   │  web (Next.js)  │ ─────────────────────────────▶ │   api (FastAPI)      │   │
   └─────────────────┘                                │  auth · RBAC · REST  │   │
                                                      └─────────┬────────────┘   │
                                                   SQL + defer  │                │
                                                      ┌─────────▼────────────┐   │
                                                      │ PostgreSQL 16        │   │
                                                      │ data · queue · cache │   │
                                                      └─────────▲────────────┘   │
                                                   fetch jobs   │                │
                                                      ┌─────────┴────────────┐   │
                                                      │ worker (Procrastinate)│  │
                                                      └──┬───┬───┬───┬───┬───┘   │
                          ┌──────────────────────────────┘   │   │   │   └──────┐│
                          ▼                ▼                  ▼   ▼            ▼│
                 YouTube Data API   OpenAI (vision/text/   Storage  SMTP/Telegram/VK
                 + i.ytimg.com       image)                (FS/S3)  (after approval)
                                                                   Telegram Bot → owner
```

## 2. Pipeline

Discovery → Enrichment → Filtering → Thumbnail Analysis → Lead Scoring → Human Selection → Thumbnail Generation → Offer Generation → Human Approval → Outreach → Tracking → Analytics.

| Этап | Job type(s) | Очередь | AI | Человек |
|---|---|---|---|---|
| Discovery | `channel_discovery` | bulk | — | задаёт запросы |
| Enrichment | `channel_enrichment`, `video_discovery`, `channel_classification` | bulk | text-bulk (классификация) | — |
| Filtering | — (SQL) | — | — | фильтры |
| Thumbnails | `thumbnail_download`, `image_metrics` | bulk | — | — |
| Analysis | `thumbnail_analysis`, `channel_analysis` | bulk / interactive | vision | подтверждает бюджет |
| Scoring | `lead_scoring` | bulk | — (детерминированно) | веса, overrides |
| Selection | — | — | — | shortlist/select |
| Generation | `thumbnail_generation` | interactive | image | инструкции, референсы, approve/reject |
| Contacts | `contact_enrichment` | bulk | опционально | правка |
| Offer | `offer_generation` | interactive | text-premium | редактирование |
| Approval | — | — | — | **Approve обязателен** |
| Outreach | `message_sending` | outreach | — | schedule |
| Tracking | `reply_polling`, `analytics_processing` | monitoring | классификация ответов | — |

## 3. Слои backend

```
api/ (routers, thin) ─┐
workers/ (tasks, thin)┴─▶ domains/*/service.py ─▶ domains/*/repository (SQLAlchemy)
                                   │
                                   └─▶ providers/base (Protocols) ◀── providers/{youtube,ai,email,telegram,vk,storage,mock}
core/: config · db · security · secrets · state_machine · errors · logging
```

- Композиция провайдеров — в `app/core/container.py` по конфигурации; mock включается `APP_MODE=demo` или отсутствием ключей (с явной маркировкой).
- Все переходы статусов — через state machine сервисы (ADR-0011).
- Все внешние ошибки → `IntegrationError(code, human_message, retryable)` (ADR-0012).

## 4. Ключевые инварианты
1. Никакое сообщение не отправляется без `approvals` записи, чей `content_hash` совпадает с текущим содержимым оффера.
2. Suppression list и статус контакта проверяются в транзакции отправки.
3. Один канал = одна запись `channels`; принадлежность проектам — `project_channels` / `channel_discoveries`.
4. Одно изображение (SHA-256) анализируется один раз на (prompt version, model).
5. `overall_score` вычисляется кодом из subscores и версионированных весов; AI-значения не перезаписываются человеческими.
6. Невалидный AI-вывод не сохраняется как результат.
7. Каждая AI-операция имеет запись стоимости; bulk-операции требуют подтверждения оценки.
8. Секреты — только в `secrets` (AES-GCM), никогда не возвращаются API.

## 5. Структура репозитория (план)

```
C:\youtubesistemprew
├─ backend/            Python package `app`, alembic/, tests/, pyproject.toml
├─ frontend/           Next.js app
├─ infrastructure/     Dockerfiles, docker-compose.yml, postgres init
├─ scripts/            setup.ps1, dev.ps1, eval scripts
├─ storage/            локальное хранилище (gitignored)
├─ docs/               база знаний (этот каталог)
├─ .env.example
└─ README.md
```
Отдельная папка `workers/` не создаётся: воркер — часть backend-пакета (общие домены), запуск `python -m app.workers`. Миграции — `backend/alembic/`.

## 6. Фазы
См. `ENGINEERING_SUMMARY.md` → First implementation phase и §79–87 ТЗ.
