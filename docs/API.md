# API

REST API на FastAPI. Базовый префикс — `/api`. Машиночитаемая схема: `GET /api/openapi.json`, Swagger UI:
`/api/docs`. Типы фронтенда генерируются из схемы (`npm run gen:api`), поэтому источник истины — код и OpenAPI;
этот документ описывает соглашения и поведение, которое из схемы не видно.

## Авторизация и сессии (ADR-0009)

1. `POST /api/auth/login` `{"email", "password"}` → ставит две cookie:
   - `ytl_session` — HttpOnly, токен сессии (в БД хранится только SHA-256);
   - `ytl_csrf` — читаемая JS, CSRF-токен, привязанный к сессии.
   Обе: `SameSite=Lax`, `Path=/`, `Secure` при `YTL_COOKIE_SECURE=true`.
2. Каждый небезопасный запрос (`POST`/`PATCH`/`PUT`/`DELETE`) должен повторить значение `ytl_csrf` в заголовке
   `X-CSRF-Token` (double-submit). Иначе — `403 csrf_failed`.
3. Сессия истекает после `YTL_SESSION_IDLE_MINUTES` бездействия или `YTL_SESSION_ABSOLUTE_HOURS` с момента входа.
4. Rate limit входа: `YTL_LOGIN_RATE_LIMIT_ATTEMPTS` неудачных попыток за `YTL_LOGIN_RATE_LIMIT_WINDOW_SECONDS`
   по email и по IP → `429 rate_limited`.

Браузер работает через прокси Next (same-origin), CORS не настроен и не нужен.

## Роли и права

Пользователь работает в одном workspace (из сессии). Все данные фильтруются по нему.

| Право | Минимальная роль | Где используется |
|---|---|---|
| `read` | viewer | все `GET` |
| `write` | operator | создание/изменение/удаление проектов и запросов, постановка и отмена задач |
| `outreach_send` | operator | одобрение/отправка (следующие фазы) |
| `manage_integrations`, `manage_members` | admin | следующие фазы |

Порядок ролей: `viewer < operator < admin < owner`. Недостаточно прав → `403 forbidden`.

**Чужие сущности** (другого workspace) возвращают `404`, а не `403`: существование чужих данных не раскрывается.

## Формат ошибок

Все ошибки — единый JSON:

```json
{"error": {"code": "not_found", "message": "Project not found", "details": {}}}
```

| HTTP | `code` | Когда |
|---|---|---|
| 401 | `unauthenticated`, `invalid_credentials` | нет/истекла сессия; неверный email или пароль (ответ одинаковый для несуществующего пользователя) |
| 403 | `forbidden`, `csrf_failed` | нет права, нет/неверный CSRF |
| 404 | `not_found`, `image_missing` | нет сущности или она чужая; файл превью отсутствует в хранилище |
| 409 | `conflict`, `invalid_transition`, … | нарушение уникальности, недопустимый переход состояния |
| 422 | `validation_error` | тело/параметры не прошли валидацию; `details` — список `{loc, msg, type}` |
| 429 | `rate_limited` | превышен лимит попыток входа |
| 500 | `internal_error` | непредвиденная ошибка (подробности только в логе сервера) |

## Пагинация

Списки принимают `limit` (1–500, по умолчанию 50) и `offset` (≥ 0) и возвращают:

```json
{"items": [...], "total": 1234, "limit": 50, "offset": 0}
```

## Эндпоинты

| Метод и путь | Право | Ответ | Описание |
|---|---|---|---|
| `GET /api/health` | — | 200 | Проверка API и БД (`SELECT 1`) |
| `POST /api/auth/login` | — | 200 `MeResponse` | Вход, ставит cookie |
| `POST /api/auth/logout` | сессия + CSRF | 204 | Отзывает сессию, удаляет cookie |
| `GET /api/auth/me` | сессия | 200 `MeResponse` | Пользователь, workspace, роль |
| `GET /api/projects` | read | 200 `Page[ProjectOut]` | Фильтр `status=active\|archived` |
| `POST /api/projects` | write | 201 `ProjectOut` | Создание проекта |
| `GET /api/projects/{id}` | read | 200 `ProjectOut` | С `queries_count`, `channels_count` |
| `PATCH /api/projects/{id}` | write | 200 `ProjectOut` | Частичное изменение, в т. ч. `status` (архив) |
| `DELETE /api/projects/{id}` | write | 204 | Удаляет проект, его запросы и привязки каналов; сами каналы глобальны и остаются |
| `GET /api/projects/{id}/queries` | read | 200 `Page[QueryOut]` | Фильтр `status=pending\|running\|done\|failed` |
| `POST /api/projects/{id}/queries/bulk` | write | 200 `QueryBulkResult` | Массовый импорт (см. ниже) |
| `PATCH /api/projects/{id}/queries/{query_id}` | write | 200 `QueryOut` | Ниша (`taxonomy_node_id`, `null` — снять) и `search_type` запроса |
| `DELETE /api/projects/{id}/queries/{query_id}` | write | 204 | Удаление запроса |
| `GET /api/projects/{id}/niches` | read | 200 `TaxonomyNodeOut[]` | Ниши проекта |
| `PUT /api/projects/{id}/niches` | write | 200 `TaxonomyNodeOut[]` | Заменить список ниш проекта `{"taxonomy_node_ids": [...]}` |
| `POST /api/projects/{id}/discovery` | write | 202 `DiscoveryStartResult` | Запустить поиск каналов (см. «Discovery») |
| `GET /api/youtube/quota` | read | 200 `QuotaOut` | Квота YouTube на текущие сутки (Pacific Time) |
| `GET /api/taxonomy` | read | 200 `TaxonomyNodeOut[]` | Справочник ниша › тема › подтема (плоский, дерево по `parent_id`) |
| `POST /api/taxonomy/bulk` | write | 200 `TaxonomyBulkResult` | Массовое создание узлов под `parent_id` |
| `GET /api/channels` | read | 200 `Page[ChannelOut]` | Каналы проектов workspace с метриками (фильтры ниже) |
| `GET /api/channels/{id}` | read | 200 `ChannelDetail` | С проектами, метриками, нишами и источниками обнаружения |
| `GET /api/channels/{id}/videos` | read | 200 `Page[VideoOut]` | Видео канала с состоянием превью |
| `GET /api/videos/{id}` | read | 200 `VideoDetail` | Видео с описанием и тегами |
| `POST /api/channels/{id}/thumbnails/download` | write | 202 `EnqueueResult` | Поставить загрузку недостающих превью |
| `GET /api/images/{asset_id}` | read | 200 image/* | Отдача изображения с проверкой доступа |
| `GET /api/jobs` | read | 200 `Page[JobRunOut]` | Фильтры `status`, `type`, `project_id`; элемент содержит `result` |
| `GET /api/jobs/{id}` | read | 200 `JobRunDetail` | Дополнительно `params` |
| `POST /api/jobs/{id}/cancel` | write | 200 `JobRunOut` | Отмена задачи |

### Проекты

Поля `ProjectCreate`/`ProjectUpdate` (лишние поля отклоняются): `name` (1–200, уникально в workspace),
`description` (≤ 5000), `language` (`ru`, `en-US`, …), `region_code` (ISO-3166 alpha-2, приводится к верхнему
регистру), `results_per_query` (1–500), `search_depth` (1–10), `published_after` (дата), `videos_to_analyze` (1–50),
`filter_settings` (сохранённые фильтры каналов проекта — тот же набор полей, что у `GET /api/channels`, см. ниже;
неизвестные ключи → 422). Диапазоны дополнительно защищены CHECK-ограничениями в БД.

### Массовый импорт запросов

Тело — ровно одно из полей:

```json
{"text": "обзор смартфонов\nраспаковка ноутбука"}
{"queries": ["обзор смартфонов", "распаковка ноутбука"]}
```

До 5000 строк. Каждая строка приводится к NFKC со схлопнутыми пробелами; пустые строки молча пропускаются,
строки длиннее 300 символов попадают в `rejected`. Дубликаты ищутся по нормализованному тексту (дополнительно
casefold, т. е. без учёта регистра) — внутри запроса и среди уже сохранённых. Повторный импорт того же списка
ничего не создаёт.
Ответ: `{"created": N, "duplicates": N, "rejected": [{"line", "value", "reason"}], "items": [...]}`
(`line` — номер строки с 1).

Необязательные поля импорта: `taxonomy_node_id` — пометить все *новые* запросы нишей/темой (по ней потом
фильтруются найденные каналы); `search_type` = `video` (по умолчанию: поиск видео по ключевым словам → их каналы)
\| `channel` (поиск каналов по тематике). Несуществующий узел → 404.

### Ниши (taxonomy)

Три уровня: `niche` › `topic` › `subtopic` (§54). Узлы глобальны (общий словарь), выбор ниш проекта и пометки
запросов — внутри workspace. `POST /api/taxonomy/bulk` `{"parent_id": null, "text": "Finance\nGaming"}`: уровень
определяется родителем, у `subtopic` детей нет (`400 too_deep`); существующие имена (без учёта регистра) не
дублируются и возвращаются в `items` вместе с новыми.

### Discovery (поиск каналов, ADR-0008)

`POST /api/projects/{id}/discovery` `{"query_ids": [..]?, "include_done": false}` ставит задачу `discovery`
(очередь `bulk`). Без `query_ids` берутся запросы в статусе `pending`/`failed` (с `include_done=true` — и `done`);
`running` не берутся никогда. Не больше 1000 запросов за задачу. Ответ:
`{"job", "created", "queries", "quota_search_units", "quota_max_units", "quota": QuotaOut}`;
`job=null` — запускать нечего; `created=false` — такая же задача уже активна (идемпотентно по fingerprint).

| Ошибка | Когда |
|---|---|
| `409 youtube_not_configured` | нет `YTL_YOUTUBE_API_KEY` и не включён mock |
| `409 project_archived` | проект в архиве |
| `400 invalid_queries` | часть `query_ids` не принадлежит проекту или выполняется |

Что делает задача для каждого запроса: страницы `search.list` (100 units, до `search_depth` страниц и
`results_per_query` результатов) → `channels.list` пачками по 50 (1 unit; каналы, обновлённые менее
`YTL_YOUTUBE_CHANNEL_REFRESH_HOURS` назад, повторно не запрашиваются) → последние `videos_to_analyze` видео
каждого канала (`playlistItems.list`, 1 unit) и исходные видео из выдачи → `videos.list` пачками по 50 (1 unit) →
каналы, видео, превью (`pending`), суточные снимки статистики, метрики канала, принадлежность проекту и источник
обнаружения (проект, запрос, исходное видео, способ). Один YouTube-канал — одна строка, сколько бы запросов и
проектов его ни нашли (§50).

Квота резервируется в `youtube_quota_ledger` **до** каждого вызова. Если её не хватает (или YouTube ответил
`quotaExceeded`), задача завершается `failed` с `error_code=quota_exceeded`, текущий запрос возвращается в
`pending`, уже выполненные остаются `done` — повторный запуск после сброса (полночь по Pacific Time) продолжит с
оставшихся. Временные ошибки (5xx, таймаут, rate limit) — повтор задачи с backoff; неверный ключ / API не включён —
`failed` без повторов; прочие постоянные ошибки помечают только сам запрос `failed`.

`result` завершённой задачи: `queries_done`, `queries_failed`, `queries_skipped`, `hits`, `channels_found`
(уникальных), `channels_new` (новых в базе), `channels_new_in_project`, `channels_fetched`,
`channels_fresh_skipped`, `videos_upserted`, `quota_units_spent`, `provider`, `failures` (≤ 50).

Mock-провайдер (`YTL_YOUTUBE_PROVIDER=mock`, по умолчанию в demo-режиме) отдаёт детерминированные синтетические
каналы (`demo-yt-…`, `is_demo=true`) и ведёт отдельный счётчик квоты `youtube_mock`.

### Каналы: фильтры и сортировка

`GET /api/channels` параметры (все необязательны, объединяются по И):

| Группа | Параметры |
|---|---|
| Область | `project_id`, `q` (название/handle/ID, ≤ 200 символов, trigram-индекс) |
| Подписчики, видео | `min_subscribers`, `max_subscribers`, `min_videos`, `max_videos` |
| Просмотры | `min_avg_views`, `max_avg_views`, `min_median_views`, `min_last_video_views`, `min_avg_views_recent` (последние 10), `min_views_to_subs` (средние просмотры / подписчики) |
| Активность | `min_videos_7d`, `min_videos_30d`, `min_videos_90d`, `max_avg_upload_gap_days`, `max_days_since_last_upload`, `min_upload_consistency` (0–1) |
| Рынок | `country` (2 буквы), `language` (`ru` совпадает и с `ru-RU`) |
| Ниши | `niche`, `exclude_niche` — можно повторять; узел включает все вложенные темы |

Фильтры по метрикам исключают каналы без рассчитанных метрик. Метрики (`ChannelOut.metrics`) считаются по
последним ≤ 50 сохранённым видео при каждом обновлении канала discovery; счётчики `videos_7d/30d/90d` — на
момент `computed_at`, а `max_days_since_last_upload` сравнивает `last_video_at` с текущим временем.
Ниша канала — ниши запросов, которыми он найден в этом workspace (с `project_id` — в этом проекте); AI-классификация
добавится в Phase 3.

`sort` = `subscribers` (по умолчанию) \| `views` \| `videos` \| `title` \| `published` \| `discovered` \|
`avg_views` \| `median_views` \| `last_video` \| `views_ratio` \| `videos_30d`, `order` = `desc` (по умолчанию) \|
`asc`; пустые значения всегда в конце.

Каналы глобальны (один YouTube-канал — одна строка), но видны только если привязаны к проекту текущего workspace.
`is_demo=true` — синтетический демо-канал (ID `demo-…`), внешней ссылки на YouTube у него нет.
`ChannelDetail.discoveries` — до 100 последних источников обнаружения (`discoveries_total` — всего).

### Превью и изображения

- `VideoOut.thumbnail.image_url` — ссылка вида `/api/images/{asset_id}`. Путь к файлу в хранилище наружу не отдаётся.
- `GET /api/images/{asset_id}` проверяет, что изображение принадлежит видео канала из проекта текущего workspace,
  иначе 404. Заголовки: `ETag` (SHA-256), `Cache-Control: private, max-age=31536000, immutable`; при совпадении
  `If-None-Match` → `304`.
- `POST /api/channels/{id}/thumbnails/download` ставит задачу `thumbnail_download` для превью, которых ещё нет.
  Ответ `{"job": JobRunOut | null, "created": bool, "items": N}`: `job=null` — загружать нечего; `created=false` —
  такая же задача уже активна, возвращается она (идемпотентно по fingerprint).

### Фоновые задачи

Статусы: `queued → running → completed | failed | retrying | cancelled`, `retrying → running`.
Прогресс — `progress_done` из `progress_total`; при ошибке — `error_code` и человекочитаемый `error_human`.

`POST /api/jobs/{id}/cancel` переводит задачу в `cancelled` сразу (для `queued`, `running`, `retrying`).
Запись в очереди Procrastinate для ожидающей задачи отменяется, а выполняющейся отправляется запрос на прерывание
(воркер отменяет корутину). Для завершённых задач — `409 invalid_transition`. Действие пишется в аудит.

## Idempotency-Key

Механизм хранения ответов по заголовку `Idempotency-Key` реализован (`app/core/idempotency.py`, ADR-0011),
но ни к одному эндпоинту не подключён. Повторный `POST …/discovery` защищён дедупликацией активной задачи по
fingerprint; заголовок понадобится для генерации, одобрения и отправки (следующие фазы).
