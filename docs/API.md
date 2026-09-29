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
| `DELETE /api/projects/{id}/queries/{query_id}` | write | 204 | Удаление запроса |
| `GET /api/channels` | read | 200 `Page[ChannelOut]` | Каналы проектов workspace (фильтры ниже) |
| `GET /api/channels/{id}` | read | 200 `ChannelDetail` | С проектами и числом сохранённых видео |
| `GET /api/channels/{id}/videos` | read | 200 `Page[VideoOut]` | Видео канала с состоянием превью |
| `GET /api/videos/{id}` | read | 200 `VideoDetail` | Видео с описанием и тегами |
| `POST /api/channels/{id}/thumbnails/download` | write | 202 `EnqueueResult` | Поставить загрузку недостающих превью |
| `GET /api/images/{asset_id}` | read | 200 image/* | Отдача изображения с проверкой доступа |
| `GET /api/jobs` | read | 200 `Page[JobRunOut]` | Фильтры `status`, `type` |
| `GET /api/jobs/{id}` | read | 200 `JobRunDetail` | С `params` и `result` |
| `POST /api/jobs/{id}/cancel` | write | 200 `JobRunOut` | Отмена задачи |

### Проекты

Поля `ProjectCreate`/`ProjectUpdate` (лишние поля отклоняются): `name` (1–200, уникально в workspace),
`description` (≤ 5000), `language` (`ru`, `en-US`, …), `region_code` (ISO-3166 alpha-2, приводится к верхнему
регистру), `results_per_query` (1–500), `search_depth` (1–10), `published_after` (дата), `videos_to_analyze` (1–50),
`filter_settings` (объект). Диапазоны дополнительно защищены CHECK-ограничениями в БД.

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

### Каналы: фильтры и сортировка

`GET /api/channels` параметры: `project_id`, `q` (поиск по названию, ≤ 200 символов, trigram-индекс),
`min_subscribers`, `max_subscribers`, `country` (2 буквы), `sort` = `subscribers` (по умолчанию) \| `views` \|
`videos` \| `title` \| `published` \| `discovered`, `order` = `desc` (по умолчанию) \| `asc`.

Каналы глобальны (один YouTube-канал — одна строка), но видны только если привязаны к проекту текущего workspace.
`is_demo=true` — синтетический демо-канал (ID `demo-…`), внешней ссылки на YouTube у него нет.

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
но в Phase 1 ни к одному эндпоинту не подключён. Он понадобится для дорогих операций (discovery, генерация,
одобрение и отправка), которые появятся в следующих фазах.
