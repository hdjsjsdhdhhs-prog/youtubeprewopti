# ADR-0007: AI provider architecture — capability-based providers, model registry, schema-validated outputs

## Status
Accepted

## Context
AI используется в 7+ задачах с разными требованиями (vision-анализ, классификация, скоринг-объяснения, копирайтинг, генерация изображений). ТЗ: модель задаётся конфигурацией (§15, §71), провайдер заменяем (§72), analysis и generation разделены (§14, §16), strict JSON + repair (§45), prompt versioning (§44), cost tracking (§33, §75), fallback при недоступности возможности модели (§15).

Проверено по официальной документации OpenAI (2026-09-24):
- Image: `gpt-image-2.5-flare` — «fast, high-quality everyday image generation»; `gpt-image-2.5-sunburst` — «where editing precision matters most». Обе принимают референсные изображения, quality `low|medium|high|xhigh|max`.
- Text/reasoning flagship-линейка: GPT-6 Astra (complex reasoning), GPT-6 Sol (balance intelligence/cost), GPT-6 Luna (cost-sensitive, high-volume). Точные API ID и поддержка image input для каждой — подтвердить вызовом `GET /v1/models` после получения ключа (Q-004).

## Decision
1. **Протоколы** в `app/providers/ai/base.py`:
   - `TextProvider.generate_structured(prompt, schema, …) -> StructuredResult`
   - `VisionProvider.analyze_images(prompt, images, schema, …) -> StructuredResult`
   - `ImageProvider.generate(prompt, refs, settings) -> list[GeneratedImage]` / `edit(...)`
   Каждый результат несёт `usage` (tokens, images, cost_estimate, duration, provider, model).
2. **Model registry** (таблица `ai_models` + seed из конфигурации): `key`, `provider`, `api_model_id`, `capabilities` (`text`, `vision`, `json_schema`, `image_generate`, `image_edit`), цены, `enabled`.
3. **Task routing** (таблица/конфиг `ai_task_routes`): задача (`thumbnail_analysis`, `channel_analysis`, `classification`, `lead_explanation`, `offer_generation`, `thumbnail_generation_draft`, `thumbnail_generation_final`) → упорядоченный список моделей. Роутер выбирает первую модель, у которой есть нужные capabilities и которая `enabled`. Если у модели нет нужной возможности (например, нет vision) или провайдер вернул «unsupported», берётся следующая — это и есть fallback без поломки pipeline.
4. **Schema-first вывод**: Pydantic-модель → JSON Schema → structured outputs провайдера. Ответ всегда повторно валидируется Pydantic (диапазоны 1–10, обязательные поля). При ошибке — 1 repair-запрос с текстом ошибки, затем failure. Невалидный результат **не сохраняется как валидный** (статус `invalid_output`, raw сохраняется в `ai_calls` для отладки).
5. **Prompt templates** — файлы в `backend/app/prompts/{name}/v{N}.md` (Jinja2), регистрируются в `prompt_templates` с content hash. Каждый `ai_calls` ссылается на версию промпта и модель.
6. **Детерминированный скоринг**: AI возвращает только subscores; `overall_score` = взвешенная сумма в коде по `scoring_profiles` (версионируемые веса). AI-оценка и детерминированные метрики изображения хранятся отдельно.
7. **Mock-провайдеры** (`MockVisionProvider`, `MockTextProvider`, `MockImageProvider`) — детерминированные результаты, явная маркировка `provider='mock'` в данных и UI.
8. **Официальный SDK `openai`** (Python) внутри `OpenAIProvider`; SDK не импортируется вне `providers/ai/openai_*.py`.

## Alternatives
| Вариант | Почему не выбран |
|---|---|
| LangChain / LlamaIndex | Большой слой абстракций и зависимостей для задач, которые решаются тонким адаптером; усложняет отладку и контроль стоимости. |
| LiteLLM как прокси | Полезен при многих провайдерах; сейчас один. Может быть добавлен как одна из реализаций протокола позже. |
| Одна модель на всё | Запрещено ТЗ; дорого для bulk. |
| Хранить AI-вывод текстом | Запрещено ТЗ (§7). |

## Why
Бизнес-логика зависит от задач и capabilities, а не от названий моделей; замена модели — запись в конфигурации.

## Trade-offs
+ Заменяемость, наблюдаемость стоимости, воспроизводимость (prompt version + model).
− Больше кода в адаптерах вместо готового фреймворка.

## Risks
- Модели и цены меняются: цены хранятся в registry, помечаются `pricing_verified_at`; оценки стоимости — «estimated», фактическая стоимость — из usage.
- Image-модели могут отказывать в редактировании фото реальных людей (moderation) — статус `refused` обрабатывается штатно, не как сбой.

## Consequences
См. `AI_ARCHITECTURE.md` для деталей по каждому AI-модулю.

## Dependencies
openai (официальный SDK), jinja2, pydantic.

## Migration
Новый провайдер (Gemini, Claude и др.) = новый класс адаптера + записи в registry. Низкая сложность.

## Date
2026-09-24
