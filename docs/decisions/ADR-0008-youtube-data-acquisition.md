# ADR-0008: YouTube data acquisition — official YouTube Data API v3, quota-aware

## Status
Accepted

## Context
Discovery — фундамент продукта (§2). Пользователь хочет сотни/тысячи запросов. Проверено по официальной документации Google (2026-09-24):
- квота по умолчанию: **10 000 units/день** на проект;
- `search.list` = **100 units**; `channels.list`, `videos.list`, `playlistItems.list` = **1 unit** (до 50 ID в одном запросе для channels/videos).

Следствие: ≈100 поисковых запросов в день на проект при пустой квоте на остальное. Наивная схема «1 запрос = 1 search» упирается в квоту.

## Decision
1. Интерфейс `VideoDiscoveryProvider` (`search_channels`, `search_videos`, `get_channels`, `get_uploads`, `get_videos`). Реализации: `YouTubeDataApiProvider` (httpx), `MockYouTubeProvider`.
2. **Quota-aware pipeline**:
   - `search.list` — только как seed (type=video с фильтрами `publishedAfter`, `relevanceLanguage`, `regionCode` или type=channel), страницы по 50;
   - все каналы из результатов → `channels.list` батчами по 50 (1 unit/50 каналов);
   - последние N видео → плейлист `uploads` через `playlistItems.list` (1 unit), статистика → `videos.list` батчами по 50;
   - превью скачиваются с `i.ytimg.com` (квоту не расходуют).
   Итог: на 1 поисковую страницу (100 units) приходится ~50 видео → до ~50 каналов, дальнейшее обогащение ≈ 1–3 units на канал.
3. **Quota ledger** (`youtube_quota_ledger`): учёт units по дню (сброс — полночь по Pacific Time, как у Google), оценка стоимости discovery-запуска до старта, стоп при исчерпании с понятной ошибкой и автопродолжением на следующий день.
4. **Кэш ответов** (`provider_cache`, TTL): повторные search в пределах TTL не тратят квоту.
5. Поддержка нескольких API-ключей **в рамках одного GCP-проекта не увеличивает квоту**; увеличение — только официальным запросом квоты у Google (Q-003).
6. **Скрапинг YouTube HTML не реализуется** по умолчанию (ToS, хрупкость, anti-bot — §18 запрещает обход защиты). Интерфейс оставляет возможность подключить иной легальный источник позже.
7. Ошибки: `403 quotaExceeded` → стоп очереди discovery до сброса; `403 forbidden/keyInvalid` → интеграция `error` с человекочитаемым сообщением; `5xx`/timeout → retry с экспоненциальным backoff.

## Alternatives
| Вариант | Почему не выбран |
|---|---|
| google-api-python-client | Официальный, но синхронный, тяжёлый (discovery docs), а нужно 4 эндпоинта; httpx-адаптер проще тестировать (respx). Протокол позволяет заменить. |
| Скрапинг (yt-dlp, HTML) | Нарушение ToS, нестабильность, риск бана IP; противоречит §18. |
| Платные сторонние датасеты | Возможное будущее расширение через тот же интерфейс. |

## Why
Единственный официальный, стабильный источник; архитектура делает квоту управляемым ресурсом, а не сюрпризом.

## Trade-offs
+ Легальность, стабильность, предсказуемость.
− Масштаб discovery ограничен квотой (~5 000 новых каналов/день при оптимальной схеме — оценка, подлежит проверке на реальных данных).

## Risks
- Квота может быть недостаточна для «100 ниш за раз» — discovery растягивается на несколько дней (UI показывает прогноз).

## Consequences
Discovery-задачи в очереди `bulk`, лимитируются ledger'ом.

## Dependencies
httpx; API-ключ YouTube Data API (`YOUTUBE_API_KEY`).

## Migration
Новый источник = новая реализация `VideoDiscoveryProvider`. Низкая сложность.

## Date
2026-09-24
