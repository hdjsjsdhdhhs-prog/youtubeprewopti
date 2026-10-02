# AI PIPELINE

_Реализация — Phase 3 (анализ: 3.1–3.4 готовы, 3.5+ дальше) и Phase 5 (генерация)._

Спецификация модулей, схем вывода и моделей — [AI_ARCHITECTURE.md](AI_ARCHITECTURE.md), решения —
[ADR-0007](decisions/ADR-0007-ai-provider-architecture.md). Этот документ описывает **поток данных**: какие
задачи в каком порядке запускаются, что пишут в БД и где стоят проверки стоимости и человека.

## Текущее состояние (Phase 3.1–3.4, 2026-10-02)

| Этап | Статус |
|---|---|
| Превью в хранилище (`thumbnail_download`, SHA-256-дедупликация, pHash) | ✅ Phase 1 |
| Очередь задач, retry, отмена, прогресс (`job_runs`) | ✅ Phase 1 |
| Массовая загрузка превью проекта (одна задача, новые видео первыми) | ✅ 3.1 |
| Детерминированные метрики изображения (`image_metrics`, в той же задаче) | ✅ 3.2 (без OCR/лиц — Q-009) |
| Prefilter (настройки проекта, предпросмотр отбора) | ✅ 3.2 |
| AI-провайдеры (vibecode.moe + OpenAI через один Responses-адаптер, mock), реестр, маршруты задач, версии промптов, `ai_calls`, repair, fallback | ✅ 3.3 (vibecode — проверен реальными вызовами 2026-10-01; прямой OpenAI — только HTTP-тесты) |
| Транспорт генерации/правки изображений (`generate_images`: `/images/generations`, `/images/edits`), 6 image-моделей vibecode в реестре | ✅ транспорт; учёт в `ai_calls`/бюджетах и задачи генерации — Phase 5 |
| Budget gate: оценка, `confirm_token`, `budgets` (по умолчанию без лимита), стоп перед превышением | ✅ 3.4 (запуск анализа — 3.5) |
| Vision-аудит превью (`thumbnail_analyses`, `thumbnail_analysis/v1`, кэш SHA-256/pHash) | ⏳ 3.5 |
| Аудит канала, lead scoring, leads | ⏳ 3.6–3.7 |
| Генерация превью | ⏳ Phase 5 |
| Офферы | ⏳ Phase 6+ |

Провайдер реальных вызовов — **vibecode.moe** (Q-016): ключ `YTL_VIBECODE_API_KEY` в приватном конфиге
(`%USERPROFILE%\.ytlead-secrets\backend.conf`), провайдер выбирается автоматически или `YTL_AI_PROVIDER=vibecode`.
Маршруты: анализ превью/канала — `vc-vision-standard` (`gpt-6-sol`) → `vc-vision-premium` (`gpt-6-astra`);
bulk-текст — `vc-text-bulk` (`gpt-6-luna`). Image-модели (`vc-gpt-image-2`, `vc-gpt-image-2.5`,
`vc-gpt-image-2-vip`, `vc-nano-banana-2`, `vc-nano-banana-2-lite`, `vc-nano-banana-pro`) работают только через
`/images/*` и в текстовые маршруты не входят. Без ключа — mock (`YTL_AI_PROVIDER=mock`) с пометкой в UI.

### Как устроен вызов (`app/domains/ai/runner.py`)

1. Маршрут задачи → первая включённая модель активного провайдера с нужными возможностями.
2. Промпт `app/prompts/{name}/v{N}.md` регистрируется в `prompt_templates` по SHA-256; изменённый файл
   использованной версии отклоняется (`prompt_changed`) — нужна новая версия.
3. **Резервирование**: под advisory-lock workspace проверяются бюджеты и лимит задачи (`job_runs.budget_usd`),
   затем вставляется `ai_calls(status=pending, estimated_cost_usd)` отдельной закоммиченной транзакцией.
4. Вызов провайдера → валидация Pydantic → при ошибке 1 repair-запрос с текстом ошибок → `ok`/`repaired`, иначе
   `invalid_output` (сырой текст в `raw_output`, результатом не считается).
5. `ai_calls` дополняется токенами, фактической стоимостью и длительностью; `job_runs.actual_cost_usd` растёт.
   «Модель недоступна» → следующая модель маршрута; отказ модели → `refused`; прочие ошибки → `error` и наверх
   (временные — повтор задачи).

## Поток (целевой)

```
discovery (YouTube API)          → channels, videos, thumbnails(pending)
  └─ thumbnail_download          → image_assets (sha256, phash)                    [есть]
       └─ image_metrics          → детерминированные метрики, стоимость ≈ 0
            └─ prefilter         → фильтры проекта; дальше идут только прошедшие
                 └─ budget gate  → оценка стоимости → подтверждение пользователя
                      └─ thumbnail_audit (vision)  → thumbnail_analyses (subscores, problems[])
                           └─ channel_audit        → channel_analyses
                                └─ lead_score (код) → leads.priority_score + разбор по компонентам
                                     └─ оператор выбирает лидов (SELECTED)          [человек]
                                          └─ thumbnail_generation (brief из problems[]) → generated_thumbnails
                                               └─ оператор выбирает вариант                [человек]
                                                    └─ offer_generation → offers (PENDING_APPROVAL)
                                                         └─ approve → send                  [человек]
```

## Сквозные правила

- **Кэш до вызова**: ключ `(sha256 изображения | pHash-сосед, версия промпта, модель)` — повторный анализ того же
  изображения не оплачивается.
- **Каждый вызов — строка `ai_calls`**: модель, версия промпта, токены/изображения, оценочная и фактическая
  стоимость, длительность, статус.
- **Числа считает код**: `overall_score`, lead score, агрегаты канала — не AI (AI_ARCHITECTURE §2–4).
- **Невалидный вывод** (после одной repair-попытки) не сохраняется как результат — только как `ai_calls.status`.
- **Ни одна отправка без человека**: переходы в `SENT` возможны только из `APPROVED`/`SCHEDULED`
  (закреплено в `LEAD_MACHINE`, покрыто `tests/unit/test_state_machine.py`).

## Открытые вопросы

Q-004 (модели и цены), Q-008 (лицо автора в генерации), Q-009 (OCR/детекция лиц в метриках),
Q-010 (стоимость массового анализа), Q-014 (пропускная способность) — см. [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md).
