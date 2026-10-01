# OPEN QUESTIONS & RISKS

Статусы: OPEN · INVESTIGATING · DECIDED · BLOCKED

| ID | Вопрос | Статус | Влияет на | Заметки / следующий шаг |
|---|---|---|---|---|
| Q-001 | Telegram-outreach: ручная отправка из черновика или MTProto user-аккаунты с принятием риска ограничений? | OPEN (решение владельца) | Phase 8, ADR-0010 | Bot API не может писать первым. До решения — только Bot для уведомлений + ManualSendProvider. |
| Q-002 | Целевые рынки/юрисдикции (RU / EN / оба) | OPEN (решение владельца) | Промпты, язык офферов, compliance (38-ФЗ/152-ФЗ, GDPR, CAN-SPAM) | Архитектура поддерживает мультиязычность; тексты opt-out и шаблоны зависят от ответа. |
| Q-003 | Достаточно ли квоты YouTube (10 000 units/день) для планируемых объёмов? | OPEN | Phase 2 | Оценка: ~100 search-страниц/день → до ~5 000 каналов/день (оценка). Phase 2: ledger, оценка до запуска, стоп и продолжение после сброса, пропуск свежих каналов. На mock-данных 3 запроса по 20 результатов ≈ 342 units (≈ 114 на запрос). Реальный расход — после ключа (`YTL_YOUTUBE_API_KEY`); при нехватке — официальный запрос квоты. |
| Q-004 | Точные API ID моделей GPT-6 Astra/Sol/Luna, поддержка vision и structured outputs, цены | OPEN | Phase 3, AI_ARCHITECTURE | Проверить `GET /v1/models` + pricing после получения `OPENAI_API_KEY`. До этого — mock-провайдер и пометка `pricing unverified`. |
| Q-005 | Docker Engine на dev-машине | BLOCKED — MANUAL ACTION REQUIRED | ADR-0013 | Нет VT-x/SLAT в ВМ (перепроверено 2026-10-01: `VirtualizationFirmwareEnabled=False`, `SLAT=False`, `VMMonitorModeExtensions=False`). Изнутри ВМ не решается: включить nested virtualization на хосте или использовать удалённый Docker-хост. Сборка образов — в GitHub Actions (`build-images`). |
| Q-006 | Email-провайдер: собственный SMTP или API-сервис? | OPEN | Phase 8 | Адаптер SMTP в любом случае; API-провайдер — отдельный адаптер. |
| Q-007 | Один пользователь или команда? | DECIDED (для Phase 1) | ADR-0009 | Модель данных с workspace + RBAC; UI и onboarding — под одного owner. Расширение без миграции данных. |
| Q-008 | Использование лица автора канала в сгенерированных превью | OPEN (решение владельца) | Phase 5 | Права на изображение, модерация image-моделей (возможны отказы). По умолчанию — генерация без точного воспроизведения лица, если не указано иначе. |
| Q-009 | Нужны ли OCR (доля текста) и детекция лиц в детерминированных метриках? | OPEN | Phase 3 | Даёт объективные признаки без AI, но +зависимости (opencv ~50 МБ / tesseract). Решить после замеров качества AI-аудита. |
| Q-010 | Стоимость массовой vision-аналитики | INVESTIGATING | Phase 3, cost control | Двухступенчатая схема (prefilter + AI для отфильтрованных), `detail=low`, кэш. Реальные цифры — после Q-004. |
| Q-011 | Версия TypeScript: npm latest = 7.0.2 (нативный компилятор) — совместимость с Next 16 | DECIDED | Phase 1 frontend scaffold | 2026-09-27: `create-next-app@16.3.6` ставит `typescript ^5` (5.9.3); `tsc --noEmit` и `next build` проходят. TS 7 — пересмотреть, когда Next объявит поддержку. |
| Q-012 | Производительность `next build` на 2 vCPU / 4 ГБ | DECIDED | Phase 1 | 2026-09-27: `next build` (Turbopack) ≈ 31 с полный цикл, компиляция 7–20 с. Приемлемо — ADR-0005 остаётся, Vite не нужен. |
| Q-013 | Политика обновления/удаления данных по условиям YouTube API Services | OPEN | Phase 2, monitoring | Проверить актуальные Developer Policies; заложить периодический refresh и удаление устаревших данных. |
| Q-014 | Масштабирование thumbnail analysis / generation throughput | OPEN | Phase 3, 5 | Лимиты OpenAI зависят от tier; конкуррентность воркера настраиваемая; очереди разделены. |
| Q-015 | Git-репозиторий: локальный или проект на GitLab; identity для коммитов | DECIDED | Весь проект, CI | 2026-10-01: репозиторий — GitHub (`hdjsjsdhdhhs-prog/youtubeprewopti`, `main`), CI только GitHub Actions (`.github/workflows/ci.yml`), GitLab CI удалён (ADR-0013). |

## Зафиксированные риски
| Риск | Вероятность | Влияние | Митигирование |
|---|---|---|---|
| Квота YouTube ограничивает discovery | Высокая | Среднее | Quota ledger, кэш, растягивание на дни, запрос квоты |
| Правовые ограничения холодных рассылок | Средняя | Высокое | Ручной approve, opt-out, suppression list, audit; решение владельца по юрисдикциям |
| Бан Telegram-аккаунтов | Высокая (при MTProto-outreach) | Среднее | Q-001; лимиты, cooldown, автопауза |
| Рост стоимости AI | Средняя | Среднее | Бюджеты, pre-flight оценки, кэш, prefilter |
| Расхождение dev (Windows) / prod (Linux) | Средняя | Среднее | CI на Linux, pathlib, UTF-8, `lc_messages=C` |
| Нехватка ресурсов dev-машины (4 ГБ) | Средняя | Низкое/Среднее | Ограничение конкуррентности воркера, при необходимости — Vite |
