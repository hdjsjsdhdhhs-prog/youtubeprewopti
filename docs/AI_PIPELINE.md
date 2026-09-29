# AI PIPELINE

_Каркас. Реализация — Phase 3 (анализ) и Phase 5 (генерация). В Phase 1 AI-кода нет._

Спецификация модулей, схем вывода и моделей — [AI_ARCHITECTURE.md](AI_ARCHITECTURE.md), решения —
[ADR-0007](decisions/ADR-0007-ai-provider-architecture.md). Этот документ описывает **поток данных**: какие
задачи в каком порядке запускаются, что пишут в БД и где стоят проверки стоимости и человека.

## Текущее состояние (Phase 1)

| Этап | Статус |
|---|---|
| Превью в хранилище (`thumbnail_download`, SHA-256-дедупликация, pHash) | ✅ готово — вход для анализа |
| Очередь задач, retry, отмена, прогресс (`job_runs`) | ✅ готово |
| AI-провайдеры, model registry, `ai_calls`, бюджеты | ⏳ Phase 3 |
| Детерминированные метрики изображения | ⏳ Phase 3 |
| Vision-аудит превью и канала, lead scoring | ⏳ Phase 3 |
| Генерация превью | ⏳ Phase 5 |
| Офферы | ⏳ Phase 6+ |

Блокер для реальных вызовов: нет `OPENAI_API_KEY` и не проверены ID/цены моделей (Q-004). До этого
все AI-этапы работают через mock-провайдер с пометкой в UI.

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
