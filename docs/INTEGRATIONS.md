# INTEGRATIONS

Исследование внешних API (§77G). Статус «verified» — проверено по официальной документации в этой сессии; «to verify» — требует ключа или дополнительной проверки.

## YouTube Data API v3
| Аспект | Значение | Статус |
|---|---|---|
| SDK | Официальный google-api-python-client существует; используется httpx-адаптер (ADR-0008) | — |
| Auth | API key (публичные данные) | verified |
| Квота | 10 000 units/день на GCP-проект | verified |
| Стоимость | `search.list` 100; `channels.list` 1; `videos.list` 1; `playlistItems.list` 1 | verified |
| Батчи | до 50 ID на `channels.list`/`videos.list`; `maxResults` ≤ 50 | verified (стандарт API) |
| Пагинация | `pageToken` / `nextPageToken` | — |
| Ошибки | 403 `quotaExceeded`, 403 `forbidden`, 400 `keyInvalid`, 5xx | обработка — ADR-0008 |
| Сброс квоты | полночь Pacific Time | to verify в консоли |
| Увеличение квоты | официальная форма аудита/запроса квоты Google | Q-003 |
| Превью | `i.ytimg.com/vi/{id}/{maxresdefault|hqdefault|mqdefault}.jpg`, без квоты | — |
| Ограничения | Email в «About» закрыт капчей — не извлекается (§18) | — |
| Data retention | Условия YouTube API Services требуют периодического обновления/удаления сохранённых данных API — учесть политику обновления (Q-013) | to verify |
| Поиск «связанных видео» | `search.list?relatedToVideoId` удалён из API (revision history, 2023-08) — способ `related_video` из ТЗ §2 через официальный API недоступен; используются поиск видео по ключевым словам и поиск каналов по тематике | to verify при наличии ключа |
| Реализация (Phase 2) | `app/providers/youtube/http.py` (httpx, `fields` для экономии трафика), `mock.py` (офлайн, `demo-yt-…`); ошибки классифицируются по `error.errors[].reason`: `quotaExceeded`/`dailyLimitExceeded` → стоп без повторов, `rateLimitExceeded` → повтор, `keyInvalid` → «ключ недействителен», `accessNotConfigured` → «API не включён в GCP-проекте» | проверено тестами на respx; живые вызовы — **не проверены** (нет ключа) |

## OpenAI
| Аспект | Значение | Статус |
|---|---|---|
| SDK | `openai` (Python), 3.19.x | verified (PyPI) |
| Auth | `OPENAI_API_KEY` (server-side only) | — |
| Image models | `gpt-image-2.5-flare`, `gpt-image-2.5-sunburst`; quality `low…max`; reference images, masks, streaming partial images | verified (docs) |
| Text/vision | GPT-6 Astra / Sol / Luna | verified (docs), API ID — to verify |
| Structured outputs | JSON Schema | to verify для выбранных моделей |
| Rate limits | зависят от tier аккаунта; заголовки `x-ratelimit-*`, 429 + retry-after | to verify с ключом |
| Цены | на страницах pricing; хранятся в `ai_models` с `pricing_verified_at` | to verify |
| Модерация | отказ → ошибка запроса с кодом модерации; обрабатывается как `refused` | verified (пример в docs) |

## Telegram
| Аспект | Значение |
|---|---|
| Bot API (httpx) | Уведомления владельцу (§57), inline-кнопки. Бот не может первым написать пользователю, не начавшему диалог. |
| MTProto (Telethon) | Пользовательский аккаунт; требует `api_id/api_hash` (my.telegram.org), вход по коду; session — секрет (ADR-0009). Массовые сообщения незнакомым людям — риск ограничений аккаунта. Решение — Q-001. |
| Ошибки | `FloodWaitError` (обязательная пауза N секунд), `PeerFloodError` (аккаунт ограничен — автопауза), `AuthKeyUnregistered`/`SessionRevoked` («session expired, re-authentication required»), `UserPrivacyRestricted`. |

## Email
| Аспект | Значение |
|---|---|
| SMTP | aiosmtplib; TLS/STARTTLS; учётные данные — секрет |
| API-провайдеры | Адаптер-интерфейс позволяет добавить (Postmark, SES, Mailgun, Unisender и др.) — выбор Q-006 |
| Deliverability | SPF/DKIM/DMARC домена отправителя — ответственность владельца, чек-лист в DEPLOYMENT.md |
| Bounces/replies | IMAP-поллинг ящика ответов или webhook провайдера (Phase 8) |
| Compliance | opt-out в каждом письме; suppression list (ADR-0010) |

## VK
| Аспект | Значение |
|---|---|
| API | `api.vk.com/method/*`, версия `v=5.x`, httpx |
| Ограничения | Сообщество пишет только пользователям, разрешившим сообщения; ЛС незнакомым от user-token ограничены. Реалистично — ручная отправка или сообщения от сообщества. |

## Storage
| Провайдер | Статус |
|---|---|
| Local FS | по умолчанию |
| S3-compatible (AWS S3, MinIO, Yandex Object Storage, …) | через `S3Storage`, опционально |

## Статусы интеграций в UI
`not_configured` (нет credentials) · `active` · `error` (с кодом и человекочитаемым сообщением) · `paused` · `mock` (явная маркировка Demo/Mock, §77).
