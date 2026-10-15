# DATABASE

PostgreSQL 16, SQLAlchemy 2.0, Alembic. Решения — ADR-0004, ADR-0011.
Статус документа: **проект схемы** (реализуется по фазам; колонка «Фаза» — когда таблица появляется в миграциях).

## Принципы
- PK: `id BIGINT GENERATED ALWAYS AS IDENTITY`; внешние идентификаторы (YouTube ID) — отдельные UNIQUE-колонки. Публичные ID в API — те же bigint (self-hosted, перебор ID не является угрозой при проверке workspace).
- Все таблицы с `created_at`, `updated_at` (`timestamptz`, UTC).
- Workspace-изоляция: каждая пользовательская сущность имеет `workspace_id`; **публичные данные YouTube** (`channels`, `videos`, снапшоты, `image_assets`, `thumbnails`, `thumbnail_analyses`) — **глобальные**, общие для всех проектов и workspace (§49–50): один канал = одна запись, принадлежность к проектам — в связующих таблицах.
- Поля для фильтров/сортировки — колонки с индексами; `JSONB` — только raw AI output, provider-specific данные, настройки.
- Score-колонки: `NUMERIC(4,2)` с `CHECK (x BETWEEN 1 AND 10)`; lead-scores `NUMERIC(5,2)` `0..100`.
- AI-значения и человеческие правки хранятся раздельно; AI-значения никогда не перезаписываются (§47).
- Enum'ы — PostgreSQL `ENUM` через Alembic (или `TEXT + CHECK` там, где набор часто меняется).

## ER-обзор

```
workspaces ─┬─ workspace_members ── users ── sessions
            ├─ search_projects ─┬─ search_queries
            │                   ├─ project_niches ── taxonomy_nodes (niche > topic > subtopic)
            │                   ├─ discovery_runs
            │                   └─ project_channels ──┐
            ├─ leads ────────────────────────────────┼── channels (global) ─┬─ channel_stats_snapshots
            │   ├─ lead_scores                       │                      ├─ channel_metrics
            │   ├─ lead_status_history               │                      ├─ channel_classifications ── taxonomy_nodes
            │   ├─ lead_tags ── tags                 │                      ├─ channel_analyses
            │   ├─ generation_requests ── generated_thumbnails ── image_assets
            │   └─ offers ── offer_variants ── messages ── message_events
            ├─ campaigns ── campaign_leads, ab_tests ── ab_test_variants
            ├─ contacts* (channel-level, see below) , suppression_list
            ├─ style_references ── image_assets
            ├─ integrations ── secrets ; email_accounts ; telegram_accounts
            └─ job_runs, ai_calls, audit_logs, feedback_events, score_overrides, favorites
channels ── videos ─┬─ video_stats_snapshots
                    └─ thumbnails ── image_assets ─┬─ image_metrics
                                                   └─ thumbnail_analyses ── prompt_templates, ai_calls
```

## Таблицы

### Identity & access (Phase 1)
| Таблица | Ключевые колонки | Ограничения / индексы |
|---|---|---|
| `users` | email (citext), password_hash, display_name, is_active, last_login_at | UNIQUE(email) |
| `workspaces` | name, slug, settings JSONB | UNIQUE(slug) |
| `workspace_members` | workspace_id, user_id, role ENUM(owner, admin, operator, viewer) | UNIQUE(workspace_id, user_id) |
| `sessions` | user_id, token_hash, csrf_token_hash, ip, user_agent, expires_at, last_seen_at, revoked_at | UNIQUE(token_hash) |
| `idempotency_keys` | workspace_id, key, request_hash, response_status, response_body JSONB, expires_at | UNIQUE(workspace_id, key) |
| `audit_logs` | workspace_id, actor_user_id, action, entity_type, entity_id, diff JSONB, ip, created_at | idx(workspace_id, created_at), idx(entity_type, entity_id) |
| `secrets` | workspace_id, purpose, ciphertext BYTEA, nonce BYTEA, key_version | — |

### Projects & taxonomy (Phase 1–2)
| Таблица | Ключевые колонки | Ограничения / индексы |
|---|---|---|
| `taxonomy_nodes` | parent_id, level ENUM(niche, topic, subtopic), name, slug | UNIQUE(parent_id, slug) |
| `search_projects` | workspace_id, name, description, language, region_code, results_per_query, search_depth, published_after, videos_to_analyze, filter_settings JSONB, prefilter_settings JSONB (Phase 3, `ThumbnailPrefilter`; `{}` = по умолчанию), ideal_lead_profile_id, status | UNIQUE(workspace_id, name) |
| `project_niches` | project_id, taxonomy_node_id | PK(project_id, taxonomy_node_id) |
| `search_queries` | project_id, text, text_normalized, taxonomy_node_id NULL, search_type ENUM(video, channel) (Phase 2), status, last_run_at, results_count | UNIQUE(project_id, text_normalized) |
| `discovery_runs` | **не создана** (Phase 2): история запусков — это `job_runs` с `type='discovery'`, `params.project_id`, итоги в `result` (фильтр `GET /api/jobs?project_id=`) | — |
| `project_channels` | project_id, channel_id, first_discovered_at, last_discovered_at, discovery_count | PK(project_id, channel_id) |
| `channel_discoveries` | project_id, channel_id, search_query_id NULL, source_video_id NULL, method ENUM(keyword_search, channel_search, related_video, manual_import, monitoring), discovered_at | UNIQUE(project_id, channel_id, search_query_id, source_video_id) NULLS NOT DISTINCT |

### YouTube data — global (Phase 1–2)
| Таблица | Ключевые колонки | Ограничения / индексы |
|---|---|---|
| `channels` | youtube_channel_id, handle (citext), title, description, custom_url, avatar_url, country, default_language, published_at, uploads_playlist_id, subscriber_count, subscribers_hidden, view_count, video_count, topic_categories TEXT[], keywords TEXT[], last_fetched_at, raw JSONB | UNIQUE(youtube_channel_id); idx(subscriber_count); trgm(title) |
| `channel_stats_snapshots` | channel_id, captured_on DATE, subscriber_count, view_count, video_count | UNIQUE(channel_id, captured_on) |
| `videos` | channel_id, youtube_video_id, title, description, published_at, duration_seconds, is_short, category_id, tags TEXT[], view_count, like_count, comment_count, last_fetched_at, raw JSONB | UNIQUE(youtube_video_id); idx(channel_id, published_at DESC) |
| `video_stats_snapshots` | video_id, captured_on, view_count, like_count, comment_count | UNIQUE(video_id, captured_on) |
| `channel_metrics` (Phase 2) | channel_id (PK), computed_at, window_videos (≤ 50 последних сохранённых видео), avg_views, median_views, last_video_views, avg_views_recent (последние 10), views_to_subs_ratio, median_views_to_subs_ratio, videos_7d, videos_30d, videos_90d, avg_days_between_uploads, last_video_at, oldest_window_video_at, upload_consistency (0..1), views_trend (новая половина окна к старой, +0.5 = +50 %), recent_views_velocity (просмотры/день видео за 30 дней) | CHECK(upload_consistency 0..1); idx(avg_views), idx(median_views), idx(last_video_at), idx(videos_30d), idx(views_to_subs_ratio). «Дней с последнего видео» не хранится — считается от `last_video_at`; «дата первого видео» недоступна без обхода всего плейлиста (квота), поэтому хранится только начало окна |
| `channel_classifications` | channel_id, taxonomy_node_id, level, confidence, source ENUM(ai, manual, youtube_topic), ai_call_id, is_primary | UNIQUE(channel_id, taxonomy_node_id, source) |

Фильтры §3 выражаются как SQL над `channels ⨝ channel_metrics ⨝ channel_classifications` (+ `lead_scores`, `channel_analyses` для §9), без материализации «FILTERED».

### Media (Phase 1, 3)
| Таблица | Ключевые колонки | Ограничения / индексы |
|---|---|---|
| `image_assets` | sha256, phash (BIGINT), storage_key, mime, width, height, bytes, source ENUM(youtube_thumbnail, generated, reference, upload) | UNIQUE(sha256); idx(phash) |
| `thumbnails` | video_id, image_asset_id, original_url, variant (maxres/high/medium), fetch_status, fetched_at, error | UNIQUE(video_id) |
| `image_metrics` (Phase 3) | image_asset_id (PK, CASCADE), algo_version, computed_at, work_width, work_height, letterbox_cropped, luminance_mean, contrast_rms, colorfulness, sharpness_laplacian, edge_density, saliency_center_ratio NULL, dominant_colors JSONB | CHECK(luminance 0..1, edge_density 0..1, saliency 0..1); idx(luminance_mean), idx(contrast_rms), idx(colorfulness). Детерминированные метрики §6 на рабочей ширине 320 px после обрезки чёрных полос; формулы — `app/domains/media/imaging.py`. Строки старой `algo_version` пересчитываются следующей загрузкой. `text_area_ratio`/`face_count` не созданы (Q-009) |

### AI & analysis (Phase 3)
| Таблица | Ключевые колонки | Ограничения / индексы |
|---|---|---|
| `ai_models` ✅ | key, provider, api_model_id, capabilities VARCHAR[], price_input_per_1m, price_output_per_1m (NUMERIC(12,6), USD / 1M токенов, NULL = неизвестна; 6 знаков — миграция `0d773a65d5b6`), price_per_image JSONB (`{"default": "<usd>"}` — плоская цена за картинку), pricing_verified_at, enabled, notes | UNIQUE(key). Глобальный реестр; строки по умолчанию вставляются при первом обращении (`on conflict do nothing`) |
| `prompt_templates` ✅ | name, version, content_hash (SHA-256 файла), body, output_schema JSONB, created_at | UNIQUE(name, version) |
| `ai_calls` ✅ | workspace_id NULL (CASCADE), project_id NULL, job_run_id NULL, task, provider, model_key, api_model_id, prompt_template_id, attempt (2 = repair), status ENUM(pending, ok, repaired, invalid_output, refused, error), input_ref JSONB, output JSONB (только валидный), raw_output (для отладки невалидного), validation_errors JSONB, error_code, error_message, input_tokens, output_tokens, image_inputs, image_outputs, estimated_cost_usd, actual_cost_usd NUMERIC(14,6), duration_ms | idx(task, created_at), idx(workspace_id, created_at), idx(job_run_id); CHECK(attempt ≥ 1). Строка вставляется `pending` **до** вызова (резерв в бюджете) и дополняется после |
| `scoring_profiles` ✅ (3.5) | workspace_id (CASCADE), kind ENUM(thumbnail, lead, priority, contactability), version (≥ 1), weights JSONB (валидируется `ThumbnailWeights` / `LeadWeights` (максимумы компонентов + `niche_values`) / `PriorityWeights`), is_active, created_by | UNIQUE(workspace_id, kind, version); partial UNIQUE(workspace_id, kind) WHERE is_active. Нет строки — встроенные веса (version 0). Используются `thumbnail` (3.5), `lead` и `priority` (3.7); `contactability` — Phase 6 |
| `thumbnail_analyses` ✅ (3.5) | image_asset_id (CASCADE), prompt_template_id, provider, model_key, ai_call_id NULL, reused_from_id NULL (копия аудита визуально идентичного изображения), phash_distance, detail, is_current, composition_score, contrast_score, text_score, hierarchy_score, subject_score, color_score, mobile_readability, topic_relevance, visual_impact, professionalism, redesign_potential (все NUMERIC(4,2), CHECK 1–10), has_face, face_emotion, text_present, text_word_count_est, focal_point, background_separation / clutter_level / improvement_opportunity ENUM `ai_level`(low, medium, high), problems JSONB, recommendations JSONB, problem_codes VARCHAR[] (для фильтров), visual_summary | UNIQUE(image_asset_id, prompt_template_id, model_key) — `uq_thumbnail_analyses_cache_key`; partial UNIQUE(image_asset_id) WHERE is_current; GIN(problem_codes); idx(improvement_opportunity). **Отклонение от проекта схемы:** `overall_score` и `scoring_profile_id` не хранятся — таблица глобальная, а веса у каждого workspace свои, поэтому балл считается в SQL при чтении. «Текущий» аудит изображения — результат последнего запуска, который его коснулся |
| `channel_analyses` ✅ (3.6) | channel_id (CASCADE), prompt_template_id, provider, model_key, ai_call_id NULL, detail, inputs_digest (SHA-256 видео + их изображений + id их аудитов превью, в порядке показа модели), is_current, video_ids BIGINT[] (сравнённые видео, новейшие первыми; «Video N» в промпте = video_ids[N-1]), thumbnail_analysis_ids BIGINT[], visual_consistency, outdated_style_score (NUMERIC(4,2), CHECK 1–10; выше = более устаревший), repeated_template, has_design_system, visual_opportunity `ai_level`, patterns JSONB, recurring_problems JSONB, recurring_problem_codes VARCHAR[], weakest_video_ids BIGINT[], redesign_candidates JSONB `[{video_id, why}]`, summary | UNIQUE(channel_id, inputs_digest, prompt_template_id, model_key) — `uq_channel_analyses_cache_key`; partial UNIQUE(channel_id) WHERE is_current; GIN(recurring_problem_codes); idx(visual_opportunity); CHECK cardinality(video_ids) ≥ 1. **Отклонение от проекта схемы:** нет `workspace_id` и агрегатов (avg/median/pct/best/worst) — таблица глобальная, агрегаты зависят от весов workspace и считаются в SQL при чтении из текущих `thumbnail_analyses` сравнённых видео (`channel_service.aggregates_subquery`) |

Кэш анализа (§5, §34): перед вызовом AI ищется `thumbnail_analyses` по `image_asset_id` (точное совпадение SHA-256) → затем по `phash` с расстоянием Хэмминга ≤ порога → reuse. Новый анализ создаётся только при новой версии промпта/модели или явном «re-analyze».

### Leads & human workflow (Phase 3–4)
| Таблица | Ключевые колонки | Ограничения / индексы |
|---|---|---|
| `leads` ✅ (3.7) | workspace_id (CASCADE), channel_id (CASCADE), status ENUM `lead_status` (ADR-0011, 16 значений), status_changed_at, created_from_project_id NULL (SET NULL) | UNIQUE(workspace_id, channel_id); idx(workspace_id, status); idx(channel_id). Создаётся автоматически для каждого канала проектов workspace. **Отклонение:** `priority_score` — в `lead_scores`; `is_favorite` не хранится в `leads` — избранное личное (`favorites`, по пользователю); `rejected_reason` — Phase 4 (ниже) |
| `lead_scores` ✅ (3.7) | lead_id (CASCADE), is_current, lead_profile_version / priority_profile_version / thumbnail_profile_version (0 = встроенные веса), ideal_lead_profile_id NULL (SET NULL), total, priority_score, data_coverage, баллы компонентов thumbnail_opportunity, channel_fit, audience, views, activity, growth, contactability, niche_value (все NUMERIC(5,2), CHECK 0–100), avg_thumbnail_score NUMERIC(4,2) NULL (CHECK 1–10; для фильтров §9), components JSONB (по компоненту: points, max, fraction, has_data, reason, inputs), created_at = момент расчёта | partial UNIQUE(lead_id) WHERE is_current; idx(total), idx(priority_score), idx(avg_thumbnail_score). Новая строка — только если результат изменился (прежняя остаётся историей). **Отклонение:** версии весов вместо `scoring_profile_id` (версия 0 строки не имеет), добавлены `views`, `priority_score`, `data_coverage`, `avg_thumbnail_score` |
| `lead_status_history` ✅ (3.7) | lead_id (CASCADE), from_status NULL (NULL = создание), to_status, actor_user_id NULL (NULL = автоматически), reason, created_at | idx(lead_id) |
| `ideal_lead_profiles` ✅ (3.7) | workspace_id (CASCADE), name, criteria JSONB (`IdealLeadCriteria`), is_default, created_by | UNIQUE(workspace_id, name); partial UNIQUE(workspace_id) WHERE is_default. **Отклонение:** нет `weights` (критерии равноценны); Channel Fit берёт профиль `is_default` workspace, `search_projects.ideal_lead_profile_id` не создан |
| `leads.rejected_reason` ✅ (4) | TEXT NULL | Причина ручного отклонения; задаётся при переходе в `REJECTED`, очищается при выходе из него |
| `score_overrides` ✅ (4) | workspace_id (CASCADE), target_type ENUM `score_target`(thumbnail_analysis, channel_analysis, lead), target_id (без FK — несколько типов), field, ai_value, human_value NUMERIC(6,2), reason, user_id (SET NULL), created_at, updated_at | UNIQUE(workspace_id, target_type, target_id, field); idx(target_type, target_id). AI-значение не перезаписывается (ТЗ §47); `ai_value` — значение AI/кода на момент (последней) правки. Поля: превью — `overall_score` + 11 подоценок (1–10), аудит канала — `visual_consistency`, `outdated_style_score` (1–10), лид — `total`, `priority_score` (0–100). Ручные `overall_score`/`redesign_potential` превью заменяют AI-значения во входах Lead Score; ручные `total`/`priority_score` лида — в сортировке, фильтрах и системных тегах. **Отклонение:** `lead` вместо `lead_score` (правка относится к лиду и переживает пересчёт) |
| `tags` ✅ (4) | workspace_id (CASCADE), name (≤ 60), name_key (casefold + схлопнутые пробелы), color `#rrggbb` NULL, system_key NULL, created_by (SET NULL) | UNIQUE(workspace_id, name_key); UNIQUE(workspace_id, system_key). **Отклонение:** `system_key` вместо `is_system` — системные теги (`weak_thumbnails`, `high_potential`, `hot_lead`, `visual_opportunity`) создаются по требованию, их имена зарезервированы |
| `lead_tags` ✅ (4) | lead_id (CASCADE), tag_id (CASCADE), source ENUM `lead_tag_source`(user, system), created_by (SET NULL), created_at | PK(lead_id, tag_id); idx(tag_id). **Отклонение:** `system` вместо `ai` — системные теги назначаются детерминированными правилами при каждом пересчёте (без AI) и снимаются, когда правило перестаёт выполняться |
| `favorites` ✅ (4) | workspace_id (CASCADE), user_id (CASCADE), entity_type ENUM `favorite_entity`(channel, thumbnail), entity_id (без FK), created_at | UNIQUE(workspace_id, user_id, entity_type, entity_id); idx(entity_type, entity_id). `thumbnail` → `entity_id` = `videos.id`. Добавить можно только видимую workspace сущность; невидимые (проект удалён) не показываются. `generated_thumbnail` — Phase 5 |
| `feedback_events` ✅ (4) | workspace_id (CASCADE), user_id (SET NULL), kind ENUM `feedback_kind`(score_override, score_override_removed, lead_status), target_type, target_id, field NULL, ai_value / human_value NUMERIC(6,2) NULL, payload JSONB, created_at | idx(workspace_id, kind, created_at). Append-only. `lead_status`: `ai_value` — Lead Score кода в момент решения, `human_value` — ручной Lead Score, `payload` = {from, to, lead_score, human_lead_score, reason}. **Отклонение:** числовые `ai_value`/`human_value` вместо JSONB (нужна агрегация «AI vs человек»); события стиля/офферов добавятся с Phase 5/7 |

### Contacts (Phase 6)
| Таблица | Ключевые колонки | Ограничения / индексы |
|---|---|---|
| `contacts` | channel_id, type ENUM(email, telegram, vk, instagram, website, other), value, value_normalized, source ENUM(youtube_about, youtube_description, video_description, website, linked_profile, manual), source_url, confidence, status ENUM(public, verified, unverified, invalid, opted_out, not_found), discovered_at, verified_at | UNIQUE(channel_id, type, value_normalized) |
| `suppression_list` | workspace_id, type, value_normalized, reason ENUM(opt_out, bounce, manual, complaint), source_message_id | UNIQUE(workspace_id, type, value_normalized) |

Контакты — публичные данные канала (глобальные), статус `opted_out` дублируется в workspace-уровневом `suppression_list`, который проверяется при каждой отправке.

### Generation (Phase 5)
| Таблица | Ключевые колонки | Ограничения / индексы |
|---|---|---|
| `style_references` | workspace_id, image_asset_id, title, description, style, category, tags TEXT[] | — (не `references`: зарезервированное слово SQL) |
| `generation_requests` | workspace_id, lead_id, source_video_id NULL, instructions TEXT, reference_ids BIGINT[], channel_analysis_id, variant_count, model_key, quality, settings JSONB, estimated_cost_usd, status, job_run_id, requested_by | — |
| `generated_thumbnails` | generation_request_id, parent_id NULL («create more like this»), label (A/B/C…), image_asset_id, prompt TEXT, prompt_template_id, model_key, settings JSONB, source_image_ids BIGINT[], ai_call_id, cost_usd, status ENUM(pending, ready, failed, refused, approved, rejected, final), error | idx(generation_request_id) |

### Offers & approvals (Phase 7)
| Таблица | Ключевые колонки | Ограничения / индексы |
|---|---|---|
| `offer_templates` ✅ (7) | workspace_id (CASCADE), key (UUID версий одного шаблона), version, name, body, is_archived, created_by | UNIQUE(workspace_id, key, version). **Отклонение:** вместо общей `templates` — только шаблоны офферов (email_subject / opt_out_footer — Phase 8) |
| `offers` ✅ (7) | workspace_id, lead_id (CASCADE), project_id, status ENUM `offer_status`(draft, pending_approval, approved, scheduled, rejected, failed; `sent` — с Phase 8, финальный), round, variant_count, language, instructions, template_id (SET NULL), inputs JSONB (снимок фактов раунда), contact_id (SET NULL), channel ENUM `outreach_channel`(email, telegram, vk, manual), attachment_variant_id → generated_thumbnails (SET NULL), email_account_id → email_accounts (SET NULL; Phase 8: отправитель, NULL = первый активный), manually_edited, scheduled_at, skipped_at, model_key, prompt_template_id, estimated_cost_usd, job_run_id, error, created_by | idx(workspace_id, status), idx(lead_id), UNIQUE partial (lead_id) WHERE status открытый (один открытый оффер на лид), CHECK round ≥ 1, variant_count 1–3. **Отклонения:** `channel` вместо `channel_type`, `attachment_variant_id` вместо `final_thumbnail_id`, personalization — в варианте, `campaign_id` — Phase 8 |
| `offer_variants` ✅ (7) | offer_id (CASCADE), round, label (A/B/C), style ENUM `offer_style`(professional, short, free_sample), status ENUM `offer_variant_status`(pending, ready, failed, refused), is_selected, subject, body, cta, draft JSONB (ответ модели: structure, facts_used, claims), guard_issues JSONB, personalization_score (CHECK 0–100), personalization_sources JSONB, model_key, ai_call_id, cost_usd, error, edited_by, edited_at | UNIQUE(offer_id, round, label), UNIQUE partial (offer_id) WHERE is_selected |
| `offer_status_history` ✅ (7) | offer_id (CASCADE), from_status, to_status, actor_user_id (NULL = система), reason, created_at | idx(offer_id) |
| `approvals` ✅ (4, модель и проверка) | workspace_id (CASCADE), target_type ENUM `approval_target`(offer), target_id (без FK до Phase 7), decision ENUM `approval_decision`(approved, rejected, skipped), content_hash (CHECK длина 64), comment, user_id (SET NULL), created_at | idx(workspace_id, target_type, target_id, created_at). content_hash = SHA-256 канонического JSON текста/вложения/контакта на момент решения: если оффер изменён после approve, approval недействителен (`approvals.service.require_approval` → 409 `approval_outdated`); действует последнее решение. **Отклонение:** `target_type`/`target_id` вместо `offer_id`/`offer_variant_id` — таблица создана до офферов |

### Campaigns & A/B (Phase 8–9)
| Таблица | Ключевые колонки |
|---|---|
| `campaigns` | workspace_id, search_project_id NULL, name, target_filters JSONB, offer_template_id, channels TEXT[], schedule JSONB, limits JSONB, status |
| `campaign_leads` | campaign_id, lead_id, assigned_variant_label, status — PK(campaign_id, lead_id) |
| `ab_tests` | campaign_id, name, dimension ENUM(subject, body, cta, attachment), status |
| `ab_test_variants` | ab_test_id, label, template_id / content JSONB, weight |

### Outreach (Phase 8)
| Таблица | Ключевые колонки | Ограничения / индексы |
|---|---|---|
| `integrations` | workspace_id, type ENUM(youtube, openai, smtp, email_api, telegram_bot, telegram_user, vk), name, status ENUM(not_configured, active, error, paused), config JSONB (несекретное), secret_id, last_error_code, last_error_message, last_checked_at | — **не создана (8.1):** SMTP-настройки и статус хранятся прямо в `email_accounts` |
| `email_accounts` ✅ (8.1) | workspace_id (CASCADE), name, from_email, from_name, reply_to, smtp_host, smtp_port, smtp_security ENUM `smtp_security`(starttls, ssl, none), smtp_username, secret_id → secrets (SET NULL; пароль только там, ADR-0009), status ENUM `email_account_status`(active, paused, error), paused_reason, daily_limit (= 20), cooldown_min_seconds / cooldown_max_seconds (= 60 / 180, случайная пауза между письмами), opt_out_text (добавляется в конец каждого письма), consecutive_failures, next_send_at (резерв следующего слота), last_sent_at, last_error_code, last_error_message, last_checked_at, created_by | idx(workspace_id); CHECK daily_limit 1–500, 0 ≤ cooldown_min ≤ cooldown_max, smtp_port 1–65535. **Отклонения:** без `integration_id` / `domain` / `campaign_limit`; `sent_today`, bounces, replies, opt-outs не хранятся — считаются из `messages` / `message_events` (UTC-сутки) |
| `telegram_accounts` | integration_id, account_name, account_type ENUM(bot, user), status, connection_status, session_secret_id, daily_limit, cooldown_seconds, schedule JSONB, sent_count, failed_count, health_score, paused_reason | — **не создана (8.1):** Telegram / VK — только ручная отправка до решения Q-001 |
| `messages` ✅ (8.1) | workspace_id (CASCADE), lead_id (CASCADE), offer_id (CASCADE), offer_variant_id, contact_id, approval_id, email_account_id, attachment_asset_id → image_assets, job_run_id (все SET NULL), channel `outreach_channel`, contact_type, to_address, subject, body (ровно отправляемый текст со строкой отказа), content_hash (одобренное содержимое), idempotency_key = SHA-256(вариант, контакт, канал), status ENUM `message_status`(queued, sending, sent, failed, cancelled, manual; `MESSAGE_MACHINE`), attempts, scheduled_at, next_attempt_at (backoff), sent_at, provider_message_id (Message-ID), error_code, error_message, created_by | UNIQUE partial (idempotency_key) WHERE status IN (queued, sending, sent, manual) — второй живой отправки того же текста тому же контакту не бывает, отменённые / неудачные не мешают повтору; idx(status, scheduled_at) (диспетчер), idx(workspace_id, created_at), idx(lead_id), idx(offer_id); CHECK length(content_hash) = 64, attempts ≥ 0. **Отклонения:** `channel` вместо `channel_type`, `email_account_id` вместо `account_ref`, снимок получателя / текста хранится в сообщении |
| `message_events` ✅ (8.1) | message_id (CASCADE), type ENUM `message_event_type`(sent, delivered, opened, replied, positive_reply, negative_reply, bounced, opt_out, failed, cancelled), note, payload JSONB, user_id (SET NULL; NULL = система), occurred_at | idx(message_id, occurred_at). События после отправки записывает оператор (SMTP их не сообщает); `bounced` / `opt_out` заполняют `suppression_list.source_message_id` |

### Operations (Phase 1+)
| Таблица | Ключевые колонки | Ограничения / индексы |
|---|---|---|
| `job_runs` | workspace_id, type, fingerprint, status, priority, queue, params JSONB, progress_total, progress_done, budget_usd, estimated_cost_usd, actual_cost_usd (NUMERIC(14,6) с Phase 3 — суммы долей цента не теряются), procrastinate_job_id, parent_id, error_code, error_human, started_at, finished_at | partial UNIQUE(fingerprint) WHERE status IN (queued, running, retrying) |
| `budgets` (Phase 3) | workspace_id (CASCADE), scope ENUM(global, project, task), scope_ref (id проекта / AI-задача; NULL для global), period ENUM(day, month, total — UTC), limit_usd NULL, max_ai_operations NULL, is_active | UNIQUE(workspace_id, scope, scope_ref, period) NULLS NOT DISTINCT; CHECK(хотя бы один лимит; лимиты ≥ 0; scope_ref задан ⇔ scope ≠ global). Нет строк — нет лимита. Scope `task` вместо задуманного `job_type`: лимит относится к AI-задаче, а не к типу фоновой задачи |
| `youtube_quota_ledger` (Phase 2) | provider (`youtube_api` \| `youtube_mock`; таблицы `integrations` пока нет), quota_day DATE (Pacific, считается PostgreSQL: `(now() AT TIME ZONE 'America/Los_Angeles')::date`), units_used, units_limit, updated_at | PK(provider, quota_day); резервирование — атомарный upsert с `WHERE units_used + n <= limit` |
| `provider_cache` | provider, cache_key, response JSONB, expires_at | UNIQUE(provider, cache_key) — **не создана**: вместо кэша ответов discovery не перезапрашивает каналы, обновлённые < `YTL_YOUTUBE_CHANNEL_REFRESH_HOURS` назад |
| `integration_events` | integration_id, operation, http_status, error_code, latency_ms, created_at | idx(integration_id, created_at) |
| `notifications` | workspace_id, user_id, kind, payload JSONB, channel, sent_at, read_at | — |

## Соответствие сущностям ТЗ §30
User→`users`, Workspace→`workspaces`, SearchProject→`search_projects`, SearchQuery→`search_queries`, Niche→`taxonomy_nodes`, Channel→`channels`, Video→`videos`, Thumbnail→`thumbnails`+`image_assets`, ThumbnailAnalysis→`thumbnail_analyses`, ChannelAnalysis→`channel_analyses`, Lead→`leads`, LeadScore→`lead_scores`, Contact→`contacts`, GeneratedThumbnail→`generated_thumbnails`, GenerationJob→`generation_requests`+`job_runs`, Offer→`offers`, OfferVariant→`offer_variants`, Campaign→`campaigns`, CampaignLead→`campaign_leads`, Approval→`approvals`, Message→`messages`, MessageEvent→`message_events`, Integration→`integrations`, TelegramAccount→`telegram_accounts`, EmailAccount→`email_accounts`, Template→`templates`, ABTest→`ab_tests`, AuditLog→`audit_logs`.

## Роли БД
- `ytlead_owner` — владелец схемы (миграции).
- `ytlead_app` — DML для api/worker (без DDL).
- Суперпользователь `postgres` используется только для первичной настройки.
