# ADR-0010: Outreach architecture & compliance guardrails

## Status
Proposed — архитектура принята; способ Telegram-outreach и целевые юрисдикции ожидают решения владельца (Q-001, Q-002).

## Context
§10, §22–28, §59: отправка только после ручного approve; адаптеры Email/Telegram/VK; rate limit, cooldown, квоты, suppression list, duplicate-send protection, opt-out, audit trail.

Технические ограничения платформ:
- **Telegram Bot API**: бот не может первым написать пользователю, который не начал с ним диалог. Для уведомлений владельцу (§57) — подходит идеально.
- **Telegram user-account (MTProto)**: технически позволяет писать первым, но массовые сообщения незнакомым людям противоречат правилам Telegram против спама и ведут к ограничениям/бану аккаунта.
- **VK API**: сообщество может писать только пользователям, разрешившим сообщения; личные сообщения незнакомым от user-token ограничены.
- **Email**: реалистичный канал, но холодные коммерческие письма регулируются (РФ — 38-ФЗ «О рекламе», 152-ФЗ; ЕС — GDPR; США — CAN-SPAM).

## Decision
1. Единый протокол `MessagingProvider`: `send_message(msg) -> SendResult`, `check_status(provider_msg_id)`, `handle_reply(event)`, `classify_error(exc) -> IntegrationError(code, human_message, retryable)`, `capabilities` (может ли писать первым, вложения, лимиты).
2. Реализации по фазам: `SmtpEmailProvider` (aiosmtplib) → `TelegramBotNotifier` (только для владельца) → `ManualSendProvider` (формирует готовое сообщение + вложение, оператор отправляет сам и отмечает «sent») → `TelegramUserProvider` (Telethon, **только после явного решения владельца**) → `VkProvider`. Mock для всех.
3. **Жёсткие guardrails в домене `outreach`, а не в адаптерах** (адаптер нельзя «забыть» настроить):
   - `Message` создаётся только из `Offer` в статусе `APPROVED` с записью `approvals` (проверка в сервисе + FK).
   - Перед отправкой в транзакции: проверка `suppression_list`, статуса контакта (`opted_out`, `invalid`), per-account daily quota, cooldown, campaign status.
   - `messages.idempotency_key` UNIQUE = hash(offer_variant_id, contact_id, channel) — повтор задачи не создаёт второго письма.
   - UNIQUE partial index: не более одного `sent` сообщения на (workspace, contact_value_normalized) в пределах кампании, если не разрешено явно.
   - Автопауза аккаунта/кампании при аномальной доле ошибок (порог настраиваемый).
   - Reply с признаками отказа (классификация AI + ключевые слова) → предложение перевести в `OPTED_OUT`; явный «unsubscribe» → автоматически в suppression list.
4. В каждом email — opt-out текст/ссылка (конфигурируемый шаблон).
5. Credentials — только через `SecretStore` (ADR-0009).

## Alternatives
- Отправка из адаптеров без доменного контроля — отвергнуто (легко обойти guardrails).
- Только ручная отправка — безопасно, но теряется автоматизация; сохраняется как `ManualSendProvider`.

## Why
Human-in-the-Loop и compliance — не функции UI, а инварианты данных.

## Trade-offs
+ Невозможно отправить без approve даже при ошибке в UI или скрипте.
− Больше проверок при отправке (незначительная стоимость).

## Risks
- Бан Telegram-аккаунтов при MTProto-outreach — решение владельца.
- Правовые риски холодных рассылок — зона ответственности владельца; система даёт инструменты (opt-out, suppression, audit), но не юридическую гарантию.

## Consequences
Phase 8 начинается с Email + Manual; Telegram user-account — отдельный decision gate.

## Dependencies
aiosmtplib; telethon (только если Q-001 решён в пользу MTProto); httpx (Bot API, VK).

## Migration
Новый канал = новый адаптер. Низкая сложность.

## Date
2026-09-24
