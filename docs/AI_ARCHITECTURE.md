# AI ARCHITECTURE

Общая архитектура — ADR-0007. Здесь — спецификация каждого AI-модуля.

## Проверенные факты (официальная документация OpenAI, 2026-09-24)
| Модель | Назначение по документации |
|---|---|
| `gpt-image-2.5-flare` | fast, high-quality everyday image generation; quality `low…max` |
| `gpt-image-2.5-sunburst` | когда важна точность редактирования; референсы через `image[]`, маски |
| GPT-6 Astra | flagship, complex reasoning |
| GPT-6 Sol | баланс интеллекта и стоимости |
| GPT-6 Luna | cost-sensitive, high-volume |

**Не проверено** (нужен API-ключ, Q-004): точные API ID моделей GPT-6, поддержка image input у каждой, актуальные цены за токен/изображение. До проверки все стоимости в UI помечаются `estimated (pricing unverified)`.

Названия ниже — **ключи model registry по умолчанию**, а не код. Меняются в конфигурации без изменения бизнес-логики.

## Общие механизмы
| Механизм | Реализация |
|---|---|
| Structured output | Pydantic → JSON Schema → structured outputs; повторная валидация Pydantic; 1 repair-попытка; иначе `invalid_output` (не сохраняется как результат) |
| Prompt versioning | `backend/app/prompts/{name}/v{N}.md` + `prompt_templates(content_hash)`; каждый `ai_calls` ссылается на версию |
| Retry | транспорт: 429/5xx/timeout → экспоненциальный backoff (учёт `retry-after`), max 5; логика: invalid JSON → repair 1 раз |
| Fallback | task route = список моделей; пропуск модели без нужной capability или при `unsupported`/`model_not_found` |
| Cost | `ai_calls` usage → estimated/actual cost; агрегаты per lead / per thumbnail / per generation |
| Budget gate | перед bulk-задачей: оценка стоимости → подтверждение пользователем (§76); в процессе — стоп при превышении `budget_usd` |
| Mock | `provider=mock`, детерминированный вывод по seed(sha256), маркировка в UI |

## 1. Discovery AI (semantic classification, §55)
- **Model:** `text-bulk` → GPT-6 Luna (fallback: GPT-6 Sol).
- **Input:** title, description, keywords, названия 10 последних видео, YouTube topicCategories.
- **Output schema `ChannelClassification`:** `niche`, `topic`, `subtopic`, `confidence` (0–1), `language`, `tags[]` (≤10), `is_kids_content`, `reasoning_short`.
- **Prompt:** `channel_classification/v1`.
- **Latency:** ~1–3 с; батч 10–20 каналов на вызов для экономии.
- **Caching:** по hash входных данных канала; переклассификация только при изменении описания/заметном изменении видео.

## 2. Thumbnail Vision Analysis (§6–7)
Двухступенчатый pipeline:
1. **Deterministic metrics** (без AI, Pillow/NumPy): яркость, RMS-контраст, colorfulness, резкость (Laplacian variance), edge density, доминирующие цвета, центральность салиентности; опционально OCR-доля текста и число лиц (Q-009). → `image_metrics`. Стоимость ≈ 0.
2. **AI vision audit** — только для превью, прошедших фильтры проекта (не для всех найденных).
- **Model:** `vision-standard` → GPT-6 Sol (fallback: GPT-6 Astra; если у модели нет vision — следующая в route).
- **Input:** изображение (detail по настройке: low для bulk / high для интерактивного), video title, niche, deterministic metrics (как объективный контекст), опционально 3–5 превью конкурентов ниши.
- **Output schema `ThumbnailAudit`:** 11 subscores 1–10 (`composition, contrast, text, hierarchy, subject, color, mobile_readability, topic_relevance, visual_impact, professionalism, redesign_potential`), `has_face`, `face_emotion`, `text_present`, `text_word_count_est`, `focal_point`, `background_separation`, `clutter_level`, `problems[] {code, severity, description}`, `recommendations[] {code, action, rationale}`, `visual_summary`, `improvement_opportunity (low|medium|high)`.
  `overall_score` **не запрашивается у AI** — считается в коде: `Σ w_i·s_i / Σ w_i` по активному `scoring_profiles(kind=thumbnail)`.
  Реализация (3.5): веса по умолчанию — 1 для десяти оценок качества и 0 для `redesign_potential` (он высокий у
  слабых превью — это сигнал возможности для lead score, а не качества); балл не хранится, считается при чтении.
  Словарь кодов проблем — `ProblemCode` в `app/domains/analysis/audit.py`.
  Коды проблем — фиксированный словарь (`weak_hierarchy`, `low_subject_separation`, `small_text`, `too_much_text`, `low_contrast`, `cluttered`, `no_focal_point`, `off_topic`, …) → машинно-фильтруемо, используется генератором.
- **Запрещено в промпте:** обещания CTR в процентах; формулировки «вероятный визуальный потенциал улучшения».
- **Prompt:** `thumbnail_analysis/v1`.
- **Caching:** SHA-256 → pHash (Hamming ≤ 6, порог конфигурируем) → reuse. Ключ уникальности: (image, prompt version, model).
- **Cost control:** bulk — `detail=low`, батч; estimated cost показывается до запуска.

## 3. Channel Analysis (comparative, §17)
- **Model:** `vision-reasoning` → GPT-6 Sol (fallback Astra).
- **Input:** 6–12 последних превью канала (low detail) + их индивидуальные аудиты + метрики просмотров по каждому видео.
- **Output schema `ChannelVisualAudit`:** `visual_consistency` 1–10, `repeated_template` bool, `outdated_style` 1–10, `has_design_system` bool, `better_performing_patterns[]` (только корреляция с просмотрами, помечено как наблюдение, не причинность), `recurring_problems[]` (коды), `weakest_video_ids[]`, `best_redesign_candidates[] {video_id, why}`, `visual_opportunity (low|medium|high)`, `summary`.
- Агрегаты (avg/median/pct weak/medium/strong, best/worst) — **считаются в коде**, не AI.
- **Prompt:** `channel_analysis/v1`. **Cache:** по набору image hashes + prompt version.
  Реализация (3.6): видео в промпте нумеруются («Video N» = image N), модель возвращает номера
  (`weakest_video_indexes`, `best_redesign_candidates[].video_index`), код переводит их в id и отбрасывает
  несуществующие; `outdated_style` — выше = более устаревший. Ключ кэша — (канал, `inputs_digest`, версия промпта,
  модель), digest включает и id аудитов превью: новый индивидуальный аудит ⇒ новый аудит канала. Агрегаты не хранятся —
  считаются при чтении по весам workspace (как `overall_score`). Схема — `app/domains/analysis/channel_audit.py`.

## 4. Lead Scoring (§8, §52–53)
- **Детерминированный** (без AI): компоненты по `scoring_profiles(kind=lead)`:
  Thumbnail Opportunity (из avg score, visual_opportunity, redesign_potential), Channel Fit (Ideal Lead Profile), Audience, Views, Activity, Growth Signals (снапшоты), Contactability, Niche Value (настраиваемая таблица ценности ниш).
  Каждый компонент хранит «баллы/максимум» и причину → объяснимость (`Thumbnail Opportunity: 23/25`).
- **AI (опционально):** `lead_explanation/v1` на `text-bulk` — короткое текстовое резюме «почему стоит/не стоит писать», **не влияет на число**.
- Priority Score (§53) — отдельный профиль весов над теми же компонентами.
- Реализация (3.7, `app/domains/leads/scoring.py`): каждый компонент даёт долю 0–1 и причину (или «нет данных» →
  0 баллов, лид не поднимается из-за неизвестности); баллы = вес · доля, Lead Score = Σ баллов / Σ весов · 100,
  `data_coverage` — доля максимума, по которой есть данные. Формулы:
  Thumbnail Opportunity — разрыв качества `(10 − avg)/9` и потенциал редизайна `(r − 1)/9` по текущим аудитам превью
  канала (веса превью workspace), с аудитом канала `0.5·разрыв + 0.25·потенциал + 0.25·visual_opportunity`
  (low 0.2 / medium 0.6 / high 1), без него `0.6·разрыв + 0.4·потенциал`; Audience — подписчики, log-шкала 1 тыс. → 1
  млн; Views — медиана просмотров, log-шкала 500 → 500 тыс.; Activity — `0.6·давность + 0.4·частота` (давность
  ≤ 7 дн. 1, ≤ 14 — 0.85, ≤ 30 — 0.6, ≤ 60 — 0.3, ≤ 90 — 0.15; частота = видео за 30 дн. / 4); Growth — среднее из
  `0.5 + views_trend` и `0.5 + 10·рост подписчиков за 30 дн.` (снапшоты за 90 дн., интервал ≥ 7 дн.); Channel Fit —
  доля заданных критериев профиля идеального лида (по умолчанию workspace), критерий без данных — не совпал,
  контакты — с Phase 6; Niche Value — лучшая оценка 0–10 среди ниш канала (теги запросов, нашедших канал, с
  предками) из таблицы `niche_values` профиля `lead`; Contactability — «нет данных» до Phase 6.
  Веса по умолчанию — Lead: 25 / 20 / 15 / 10 / 15 / 5 / 0 / 10 (в порядке выше: превью, fit, аудитория, просмотры,
  активность, рост, контакты, ниша); Priority: 30 / 20 / 0 / 20 / 20 / 0 / 0 / 10. Уровень активности для фильтров
  (§9, §52): high — последнее видео ≤ 14 дн. и ≥ 4 видео за 30 дн., medium — ≤ 30 дн., иначе low.
  `lead_explanation/v1` пока не реализован.

## 5. Contact Intelligence (§18–19)
- В основном **без AI**: regex/парсинг описаний и ссылок, нормализация (email lower/IDNA, `t.me/x` → `@x`, `vk.com/id…`), проверка формата, DNS MX для email (опционально).
- **AI (опционально):** `contact_extraction/v1` на `text-bulk` — для неструктурированных описаний («пишите в телегу …»), результат с `confidence`, всегда `unverified` до проверки.
- Contactability Score — детерминированный по `scoring_profiles(kind=contactability)`.

## 6. Offer Generation (§20–21)
- **Model:** `text-premium` → GPT-6 Astra (fallback Sol).
- **Input:** channel profile, creator name (если публично указано), 2–3 конкретных видео, коды проблем + формулировки из аудита, выбранный sample и его «что исправлено», инструкции пользователя, стиль варианта (A professional / B short / C free-sample focus), язык, feedback-примеры одобренных/отредактированных офферов (§46).
- **Output schema `OfferDraft`:** `subject`, `body`, `cta`, `structure {observation, problem, insight, value, free_sample, cta}`, `facts_used[] {field, value}`, `claims[] {text, source_field}`.
- **Validation guard:** все числа в тексте должны присутствовать во входных данных; фразы про CTR/проценты без источника → repair/reject (§20 «не придумывать цифры»).
- **Personalization score** — детерминированный: доля/вес использованных конкретных фактов (`facts_used`) + уникальность относительно других офферов (n-gram overlap) → 0–100 + список использованных данных.
- **Prompt:** `offer_generation/v1`, `personalization/v1`.
- **Реализация (Phase 7, 2026-10-15):** вход — пронумерованные факты F1…Fn, собранные кодом (`offers/service.py`);
  `facts_used` и `claims[].fact_ids` — номера фактов (а не `{field, value}`). Проверка текста: числа только из
  фактов / инструкций / шаблона (счётные ≤ 10 — можно), CTR / конверсия / проценты / гарантии — замечание; замечания
  блокируют Approve, repair-запрос не делается (текст правит человек или «Перегенерировать»). Personalization —
  70 % виды фактов (канал / видео — только если названы в тексте) + 30 % уникальность по 3-граммам; отдельного
  промпта `personalization/v1` нет. Feedback-примеры (§46) в промпт пока не передаются.

## 7. Thumbnail Generation (§12–14)
- **Models:** draft/variants → `image-fast` = `gpt-image-2.5-flare` (quality `medium`); final/точные правки → `image-precise` = `gpt-image-2.5-sunburst` (quality `high`). Обе — через registry.
- **Input builder** (связь с анализом): из `thumbnail_analyses.problems/recommendations` + `channel_analyses` формируется «brief»: *исправить: low_subject_separation, small_text → сделать: foreground subject 40–60% кадра, отделённый фон, ≤3 слова крупным шрифтом, высокий контраст*; + video title, niche, стиль канала (палитра из `image_metrics`), пользовательские инструкции, выбранные референсы, исходное превью (как reference image).
- **Prompt:** `thumbnail_generation/v1` (шаблон brief → prompt; итоговый prompt сохраняется в `generated_thumbnails.prompt`).
- **Output:** N изображений (N настраиваемо), 1536×864/16:9 или ближайший поддерживаемый размер → ресайз до 1280×720.
- **Refusal handling:** модерационный отказ → статус `refused` + причина; не считается сбоем инфраструктуры.
- **Post-check (опционально):** быстрый vision-аудит сгенерированного варианта тем же `ThumbnailAudit` → «Before/After» сравнение на одной шкале.
- **Cost:** оценка до запуска = N × цена(quality, size); подтверждение для массовой генерации.

## 8. Human Feedback (§46–47)
- События в `feedback_events`: approve/reject variant, выбор final sample, edit offer (diff), override score, mark irrelevant.
- Использование: (a) аналитика AI vs human (средняя дельта по subscores, по нишам); (b) few-shot контекст — последние K одобренных/отклонённых примеров в промпты генерации и офферов; (c) калибровка весов scoring profile (ручная, с подсказками).

## 9. AI Evaluation
- Golden set: 50–100 превью с ручными оценками владельца (`score_overrides`) → при смене промпта/модели прогон `scripts/eval_thumbnail_prompt.py`: MAE по subscores, корреляция, доля invalid output, стоимость.
- Сравнение prompt версий по `ai_calls` (status, cost, latency) и по human overrides.

## 10. Cost Control (§33, §75–76)
- Каждая операция → `ai_calls` (tokens, images, estimated/actual cost, duration).
- Pre-flight estimate для bulk-задач; бюджеты (`budgets`) на global/project/job type; hard-stop задачи при превышении; отмена очереди.
- Экономия: deterministic prefilter → AI только для прошедших фильтры; `detail=low` для bulk; кэш по SHA-256/pHash; батчинг классификации.

## Сводная таблица
| Модуль | Model key (default) | Provider | Schema | Prompt | Latency (оценка) | Fallback | Cache |
|---|---|---|---|---|---|---|---|
| Classification | text-bulk (GPT-6 Luna) | OpenAI | ChannelClassification | channel_classification/v1 | 1–3 с | GPT-6 Sol | hash входа |
| Thumbnail audit | vision-standard (GPT-6 Sol) | OpenAI | ThumbnailAudit | thumbnail_analysis/v1 | 3–10 с | GPT-6 Astra | SHA-256/pHash |
| Channel audit | vision-reasoning (GPT-6 Sol) | OpenAI | ChannelVisualAudit | channel_analysis/v1 | 10–30 с | GPT-6 Astra | набор hashes |
| Lead score | — (детерминированный) | — | LeadScoreBreakdown | — | мс | — | пересчёт по событию |
| Lead explanation | text-bulk | OpenAI | LeadExplanation | lead_explanation/v1 | 1–3 с | Sol | per lead+score version |
| Contacts | — / text-bulk | OpenAI (опц.) | ContactExtraction | contact_extraction/v1 | 1–3 с | regex only | per description hash |
| Offer | text-premium (GPT-6 Astra) | OpenAI | OfferDraft | offer_generation/v1 | 5–20 с | GPT-6 Sol | нет (уникальность) |
| Image draft | image-fast (gpt-image-2.5-flare) | OpenAI | — | thumbnail_generation/v1 | 10–60 с | sunburst | нет |
| Image final | image-precise (gpt-image-2.5-sunburst) | OpenAI | — | thumbnail_generation/v1 | 20–90 с | flare | нет |

Латентности и стоимости — оценки до реальных замеров; обновляются по данным `ai_calls`.
