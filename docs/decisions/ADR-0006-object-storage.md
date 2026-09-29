# ADR-0006: Storage — StorageBackend abstraction, local FS by default, S3-compatible optional

## Status
Accepted

## Context
Хранятся: исходные превью YouTube (тысячи, ~50–150 КБ каждое), сгенерированные варианты (1–3 МБ), пользовательские референсы, вложения к офферам. Требования: локальный FS для self-hosted, S3-compatible опционально (§63), приватность (§58), дедупликация (§5, §34), безопасная загрузка файлов.

## Decision
- Интерфейс `StorageBackend`: `put(bytes, content_type) -> key`, `get(key)`, `open_stream(key)`, `delete(key)`, `exists(key)`, `presigned_url(key, ttl)` (опционально).
- Реализации: `LocalFSStorage` (по умолчанию, `STORAGE_URL=file:///C:/youtubesistemprew/storage`) и `S3Storage` (`STORAGE_URL=s3://bucket/prefix` + endpoint — MinIO/AWS/любой S3-compatible).
- **Content-addressed ключи**: `images/{sha256[:2]}/{sha256}.{ext}`. Одинаковые байты сохраняются один раз; таблица `image_assets` (sha256 UNIQUE, phash, размеры) — единая точка дедупликации для превью, генераций и референсов.
- Файлы **никогда не отдаются статикой напрямую**: только через API-эндпоинт с проверкой доступа к workspace (или короткоживущий подписанный URL).
- Загрузка пользовательских файлов: whitelist MIME по сигнатуре (Pillow `Image.open().verify()`), лимит размера, перекодирование в PNG/WebP (снимает EXIF и потенциально вредный payload), имя файла пользователя не используется в пути.
- Каталог хранения — вне web root и вне git (`storage/` в `.gitignore`).

## Alternatives
| Вариант | Почему не выбран |
|---|---|
| Всегда MinIO | +1 сервис; на dev-машине без Docker — нативный бинарник, лишняя сложность сейчас. Остаётся опцией через S3Storage. |
| BYTEA в PostgreSQL | Раздувает БД и бэкапы; плохо для генераций в МБ. |
| Хранение по video_id | Нет дедупликации одинаковых изображений. |

## Why
Минимум инфраструктуры сейчас, переход на S3 — сменой конфигурации.

## Trade-offs
+ Простота, дедупликация на уровне хранилища.
− Локальный FS не реплицируется — бэкап каталога `storage/` обязателен (DEPLOYMENT.md).

## Risks
- Рост объёма: 50 000 превью × ~100 КБ ≈ 5 ГБ; на dev-машине 19 ГБ свободно — мониторить. Опция: хранить превью в `mqdefault` вместо `maxresdefault` для bulk-анализа.

## Consequences
Обработка изображений (Pillow) работает с байтами, не с путями.

## Dependencies
Pillow; boto3 — только при включении S3 (опциональная зависимость `[s3]`).

## Migration
Скрипт копирования ключей между backend'ами; ключи одинаковые. Низкая сложность.

## Date
2026-09-24
