# ADR-0009: Authentication, authorization, secrets

## Status
Accepted

## Context
§58: secrets encrypted at rest, ключи только server-side, audit log, RBAC, session security, CSRF, rate limiting, input validation, безопасная загрузка файлов. Система self-hosted, первично — один владелец, но модель данных содержит Workspace (§30).

## Decision
**Аутентификация**
- Email + пароль, хэш **Argon2id** (argon2-cffi).
- Серверные сессии: таблица `sessions` (хранится SHA-256 от токена), cookie `HttpOnly; Secure (prod); SameSite=Lax`, idle timeout + absolute timeout, ротация при логине.
- Первый пользователь создаётся CLI-командой `python -m app.cli create-owner` (нет публичной регистрации).

**Авторизация (RBAC)**
- Роли на уровне workspace: `owner`, `admin`, `operator`, `viewer`. Проверка через FastAPI dependency `require(permission)`; все запросы к данным фильтруются по `workspace_id`.
- Действия отправки (approve/send) требуют роли ≥ `operator` и логируются.

**CSRF**
- Same-origin (Next проксирует `/api`), `SameSite=Lax` + double-submit CSRF token в заголовке для всех мутаций.

**Секреты**
- `APP_MASTER_KEY` (32 байта, base64) — только в окружении/файле вне репозитория.
- Таблица `secrets`: `ciphertext`, `nonce`, `key_version`; шифрование **AES-256-GCM** (`cryptography`), associated data = `secret_id` + назначение. Поддержка ротации ключа (`key_version`).
- Credentials интеграций (SMTP пароль, Telegram session, VK token, OpenAI key при вводе через UI) хранятся только там; API никогда не возвращает значение секрета — только `configured: true` и маску.

**Прочее**
- Rate limiting: login (по IP + по email), дорогие эндпоинты — простая реализация на PostgreSQL/in-memory, без Redis.
- Audit log: все мутации значимых сущностей (approve, send, изменение интеграций, смена весов скоринга, ручные override) → `audit_logs` (actor, action, entity, before/after diff, ip).
- Валидация входа — Pydantic; загрузка файлов — ADR-0006.

## Alternatives
| Вариант | Почему не выбран |
|---|---|
| JWT в localStorage | Уязвимо к XSS, сложная отзывность. |
| Внешний IdP (Keycloak, Auth0) | Лишний сервис/зависимость для self-hosted single-owner. Можно добавить OIDC позже. |
| NextAuth/Auth.js | Размещает auth во фронтенде; источник истины должен быть backend. |
| Fernet | Подходит, но AES-GCM с явным key_version и AAD даёт больше контроля. |
| HashiCorp Vault | Избыточен сейчас; интерфейс `SecretStore` позволяет подключить позже. |

## Why
Минимум зависимостей, стандартные, проверенные примитивы.

## Trade-offs
+ Нет внешних сервисов, секреты не покидают backend.
− Master key в окружении: компрометация хоста = компрометация секретов (приемлемо для self-hosted; задокументировано).

## Risks
- Потеря `APP_MASTER_KEY` = потеря всех сохранённых credentials (нужно переподключить интеграции). Документируется в DEPLOYMENT.md.

## Consequences
`SecretStore` интерфейс в `app/core/secrets.py`.

## Dependencies
argon2-cffi, cryptography.

## Migration
OIDC/Vault добавляются как дополнительные реализации. Низкая сложность.

## Date
2026-09-24
