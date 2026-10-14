# AI PIPELINE

_Реализация — Phase 3 (анализ и lead scoring: 3.1–3.7 готовы) и Phase 5 (генерация)._

Спецификация модулей, схем вывода и моделей — [AI_ARCHITECTURE.md](AI_ARCHITECTURE.md), решения —
[ADR-0007](decisions/ADR-0007-ai-provider-architecture.md). Этот документ описывает **поток данных**: какие
задачи в каком порядке запускаются, что пишут в БД и где стоят проверки стоимости и человека.

## Текущее состояние (Phase 3.1–3.7, 2026-10-14)

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
| Vision-аудит превью (`thumbnail_analyses`, `thumbnail_analysis/v1`, кэш SHA-256/pHash, `overall_score` кодом по `scoring_profiles`, запуск по `confirm_token`, UI результатов) | ✅ 3.5 (mock и тесты; реальные вызовы vibecode для этого промпта — не проверены) |
| Сравнительный аудит канала (`channel_analyses`, `channel_analysis/v1`, 3–12 превью в одном vision-вызове, кэш по входным данным, агрегаты кодом, запуск по `confirm_token`, UI в проекте и на карточке канала) | ✅ 3.6 (mock и тесты; реальные вызовы vibecode для этого промпта — не проверены) |
| Лиды и Lead Score (`leads`, `lead_scores`, `lead_status_history`, `ideal_lead_profiles`; детерминированный объяснимый балл и Priority Score по `scoring_profiles` `lead`/`priority`, автосоздание `DISCOVERED`/`ANALYZED`, пересчёт после задач проекта, UI лидов с фильтрами §9 и редактором весов) | ✅ 3.7 (без AI; Contactability — вес 0 до Phase 6; `lead_explanation/v1` не сделан) |
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

### AI-аудит превью (Phase 3.5, `app/domains/analysis/pipeline.py`)

1. Оценка (`…/thumbnail-analysis/estimate`) → пользователь подтверждает → `POST …/thumbnail-analysis` с
   `confirm_token` (оценка пересчитывается; изменилась — 409, ничего не запускается).
2. Задача `thumbnail_analysis` по каждому уникальному изображению: аудит этого изображения той же версией промпта
   и моделью маршрута уже есть → reuse; иначе аудит визуально идентичного изображения (pHash ≤
   `YTL_ANALYSIS_PHASH_MAX_DISTANCE`, по умолчанию 6) → копия с `reused_from_id`; иначе вызов vision-модели.
3. Модели отправляется превью без чёрных полос 4:3 (≤ 1280 px, JPEG), название видео, канал, ниши проекта и
   детерминированные метрики как объективный контекст. Промпт запрещает прогнозы CTR и требует тексты на русском.
4. Ответ — `ThumbnailAudit` (11 оценок 1–10, наблюдения, `problems[]` из фиксированного словаря кодов,
   `recommendations[]`, `visual_summary`, `improvement_opportunity`); общий балл модель не возвращает.
5. `overall_score` = Σ wᵢ·sᵢ / Σ wᵢ по активному профилю весов workspace; считается при чтении, поэтому смена
   весов сразу меняет баллы без повторного анализа.
6. Ошибка одного изображения (невалидный вывод после repair, отказ, нет файла) не останавливает задачу; бюджет или
   ошибка провайдера останавливают, сделанное сохраняется, повтор задачи продолжает с кэша.

### Аудит канала (Phase 3.6, `app/domains/analysis/channel_pipeline.py`)

1. Вход канала — новейшие `videos_per_channel` (3–12, по умолчанию 8) видео без Shorts с текущим аудитом превью;
   меньше 3 — канал пропускается (`not_enough`). Оценка → подтверждение → задача `channel_analysis` по `confirm_token`.
2. Кэш: аудит канала с тем же `inputs_digest` (видео, их изображения, id их аудитов превью — в порядке показа),
   версией промпта и моделью маршрута → делается текущим, без вызова.
3. Иначе один vision-вызов: все превью (без полос, JPEG, `detail` из запроса) + список «Video N» с названием, датой,
   просмотрами, баллом и проблемами индивидуального аудита. Балл в промпте — по встроенным весам: аудит канала
   глобальный и не должен зависеть от весов одного workspace.
4. Ответ — `ChannelVisualAudit`: единство стиля, повторяющийся шаблон, устаревание, дизайн-система, паттерны
   (только «встречается у видео с бо́льшими просмотрами», не причина), повторяющиеся проблемы из того же словаря кодов,
   номера самых слабых видео и кандидатов на редизайн (код переводит номера в id и отбрасывает несуществующие),
   `visual_opportunity`, `summary`.
5. Средний/медианный балл, доли слабых (< 5) / средних / сильных (≥ 7), лучшее/худшее видео считает SQL при чтении по
   текущим аудитам превью и весам workspace.
6. Ошибки — как у аудита превью: отдельный канал (невалидный вывод, отказ, нет файла) не останавливает задачу.

### Лиды и Lead Score (Phase 3.7, `app/domains/leads/`)

1. После задач проекта (discovery, `thumbnail_analysis`, `channel_analysis`) воркер в отдельной транзакции вызывает
   `sync_leads` для этого проекта: каждый канал проекта получает лид workspace (`DISCOVERED`); при хотя бы одном
   текущем аудите превью — переход `DISCOVERED → ANALYZED` (оба — в `lead_status_history`, автор NULL). Итог — в
   `job_runs.result.leads`; ошибка пересчёта не отменяет работу задачи.
2. Балл (`scoring.py`, без AI) — 8 компонентов, каждый даёт долю 0–1 и причину: Thumbnail Opportunity, Channel Fit
   (профиль идеального лида), Audience, Views, Activity, Growth, Contactability (до Phase 6 — «нет данных», вес 0),
   Niche Value. Нет данных — 0 баллов. Lead Score = Σ баллов / Σ максимумов · 100; Priority Score — те же доли с
   отдельными весами.
3. Пересчёт также при `PUT /api/scoring-profiles/{thumbnail,lead,priority}`, изменении профиля идеального лида и
   `POST /api/leads/sync`. Новая строка `lead_scores` пишется, только если результат изменился.

## Поток (целевой)

```
discovery (YouTube API)          → channels, videos, thumbnails(pending)
  └─ thumbnail_download          → image_assets (sha256, phash)                    [есть]
       └─ image_metrics          → детерминированные метрики, стоимость ≈ 0
            └─ prefilter         → фильтры проекта; дальше идут только прошедшие
                 └─ budget gate  → оценка стоимости → подтверждение пользователя
                      └─ thumbnail_audit (vision)  → thumbnail_analyses (subscores, problems[])
                           └─ channel_audit        → channel_analyses
                                └─ lead_score (код) → lead_scores (total, priority_score, разбор)  [есть]
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
