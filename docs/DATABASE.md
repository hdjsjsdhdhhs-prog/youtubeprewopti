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
| `search_projects` | workspace_id, name, description, language, region_code, results_per_query, search_depth, published_after, videos_to_analyze, filter_settings JSONB, ideal_lead_profile_id, status | UNIQUE(workspace_id, name) |
| `project_niches` | project_id, taxonomy_node_id | PK(project_id, taxonomy_node_id) |
| `search_queries` | project_id, text, taxonomy_node_id NULL, status, last_run_at, results_count | UNIQUE(project_id, lower(text)) |
| `discovery_runs` | project_id, job_run_id, queries_total, quota_estimated, quota_used, channels_found, channels_new | — |
| `project_channels` | project_id, channel_id, first_discovered_at, last_discovered_at, discovery_count | PK(project_id, channel_id) |
| `channel_discoveries` | project_id, channel_id, search_query_id NULL, source_video_id NULL, method ENUM(keyword_search, channel_search, related_video, manual_import, monitoring), discovered_at | UNIQUE(project_id, channel_id, search_query_id, source_video_id) NULLS NOT DISTINCT |

### YouTube data — global (Phase 1–2)
| Таблица | Ключевые колонки | Ограничения / индексы |
|---|---|---|
| `channels` | youtube_channel_id, handle (citext), title, description, custom_url, avatar_url, country, default_language, published_at, uploads_playlist_id, subscriber_count, subscribers_hidden, view_count, video_count, topic_categories TEXT[], keywords TEXT[], last_fetched_at, raw JSONB | UNIQUE(youtube_channel_id); idx(subscriber_count); trgm(title) |
| `channel_stats_snapshots` | channel_id, captured_on DATE, subscriber_count, view_count, video_count | UNIQUE(channel_id, captured_on) |
| `videos` | channel_id, youtube_video_id, title, description, published_at, duration_seconds, is_short, category_id, tags TEXT[], view_count, like_count, comment_count, last_fetched_at, raw JSONB | UNIQUE(youtube_video_id); idx(channel_id, published_at DESC) |
| `video_stats_snapshots` | video_id, captured_on, view_count, like_count, comment_count | UNIQUE(video_id, captured_on) |
| `channel_metrics` | channel_id (PK), computed_at, window_videos, avg_views, median_views, last_video_views, avg_views_last_n, views_to_subs_ratio, median_views_to_subs_ratio, videos_7d, videos_30d, videos_90d, avg_days_between_uploads, days_since_last_upload, first_video_at, last_video_at, upload_consistency (0..1), views_trend (slope), recent_views_velocity | индексы на все фильтруемые колонки |
| `channel_classifications` | channel_id, taxonomy_node_id, level, confidence, source ENUM(ai, manual, youtube_topic), ai_call_id, is_primary | UNIQUE(channel_id, taxonomy_node_id, source) |

Фильтры §3 выражаются как SQL над `channels ⨝ channel_metrics ⨝ channel_classifications` (+ `lead_scores`, `channel_analyses` для §9), без материализации «FILTERED».

### Media (Phase 1, 3)
| Таблица | Ключевые колонки | Ограничения / индексы |
|---|---|---|
| `image_assets` | sha256, phash (BIGINT), storage_key, mime, width, height, bytes, source ENUM(youtube_thumbnail, generated, reference, upload) | UNIQUE(sha256); idx(phash) |
| `thumbnails` | video_id, image_asset_id, original_url, variant (maxres/high/medium), fetch_status, fetched_at, error | UNIQUE(video_id) |
| `image_metrics` | image_asset_id (PK), luminance_mean, contrast_rms, colorfulness, sharpness_laplacian, edge_density, saliency_center_ratio, text_area_ratio NULL, face_count NULL, dominant_colors JSONB, algo_version | детерминированные («объективные») метрики §6 |

### AI & analysis (Phase 3)
| Таблица | Ключевые колонки | Ограничения / индексы |
|---|---|---|
| `ai_models` | key, provider, api_model_id, capabilities TEXT[], price_input_per_1m, price_output_per_1m, price_per_image JSONB, pricing_verified_at, enabled | UNIQUE(key) |
| `prompt_templates` | name, version, content_hash, body, output_schema JSONB, created_at | UNIQUE(name, version) |
| `ai_calls` | workspace_id NULL, job_run_id NULL, task, provider, model_key, prompt_template_id, input_ref JSONB, output JSONB, status ENUM(ok, invalid_output, repaired, refused, error), validation_errors JSONB, input_tokens, output_tokens, image_inputs, image_outputs, estimated_cost_usd, actual_cost_usd, duration_ms, attempt | idx(task, created_at), idx(job_run_id) |
| `scoring_profiles` | workspace_id, kind ENUM(thumbnail, lead, priority, contactability), version, weights JSONB, is_active | UNIQUE(workspace_id, kind, version) |
| `thumbnail_analyses` | image_asset_id, prompt_template_id, model_key, ai_call_id, composition, contrast, text, hierarchy, subject, color, mobile_readability, topic_relevance, visual_impact, professionalism, redesign_potential (все NUMERIC 1–10), overall_score (детерминированный), scoring_profile_id, problems JSONB, recommendations JSONB, visual_summary TEXT, improvement_opportunity ENUM(low, medium, high), is_current | UNIQUE(image_asset_id, prompt_template_id, model_key); idx(overall_score) |
| `channel_analyses` | channel_id, workspace_id NULL, ai_call_id, thumbnails_analyzed, avg_score, median_score, best_thumbnail_id, worst_thumbnail_id, pct_weak, pct_medium, pct_strong, visual_consistency (1–10), visual_opportunity ENUM(low, medium, high), repeated_template BOOL, outdated_style_score, patterns JSONB, recurring_problems JSONB, weakest_video_ids BIGINT[], summary, is_current | idx(channel_id, is_current) |

Кэш анализа (§5, §34): перед вызовом AI ищется `thumbnail_analyses` по `image_asset_id` (точное совпадение SHA-256) → затем по `phash` с расстоянием Хэмминга ≤ порога → reuse. Новый анализ создаётся только при новой версии промпта/модели или явном «re-analyze».

### Leads & human workflow (Phase 3–4)
| Таблица | Ключевые колонки | Ограничения / индексы |
|---|---|---|
| `leads` | workspace_id, channel_id, status ENUM(ADR-0011), priority_score, is_favorite, rejected_reason, created_from_project_id | UNIQUE(workspace_id, channel_id); idx(workspace_id, status) |
| `lead_scores` | lead_id, scoring_profile_id, total (0–100), thumbnail_opportunity, channel_fit, audience, activity, growth, contactability, niche_value, components JSONB (объяснение), is_current, computed_at | idx(lead_id, is_current); idx(total) |
| `lead_status_history` | lead_id, from_status, to_status, actor_user_id NULL, reason, created_at | — |
| `ideal_lead_profiles` | workspace_id, name, criteria JSONB (валидируется Pydantic), weights JSONB | — |
| `score_overrides` | workspace_id, target_type ENUM(thumbnail_analysis, lead_score, channel_analysis), target_id, field, ai_value, human_value, user_id, reason | UNIQUE(workspace_id, target_type, target_id, field) |
| `tags` | workspace_id, name, color, is_system | UNIQUE(workspace_id, name) |
| `lead_tags` | lead_id, tag_id, source ENUM(user, ai), created_by | PK(lead_id, tag_id) |
| `favorites` | workspace_id, user_id, entity_type ENUM(channel, thumbnail, generated_thumbnail), entity_id | UNIQUE(user_id, entity_type, entity_id) |
| `feedback_events` | workspace_id, user_id, event_type (approve_style, reject_variant, edit_offer, override_score, mark_irrelevant, …), entity_type, entity_id, ai_value JSONB, human_value JSONB, context JSONB | idx(event_type, created_at) |

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
| `templates` | workspace_id, kind ENUM(offer, email_subject, opt_out_footer), name, body, version | UNIQUE(workspace_id, kind, name, version) |
| `offers` | workspace_id, lead_id, campaign_id NULL, status (ADR-0011), contact_id, channel_type, final_thumbnail_id NULL, personalization_score, personalization_sources JSONB, manually_edited BOOL, scheduled_at | idx(workspace_id, status) |
| `offer_variants` | offer_id, label, style (professional/short/free-sample…), subject, body, cta, ai_call_id, edited_by, edited_at, is_selected | UNIQUE(offer_id, label) |
| `approvals` | offer_id, offer_variant_id, user_id, decision ENUM(approved, rejected, skipped), content_hash, created_at | content_hash = hash текста/вложения/контакта на момент approve: если оффер изменён после approve, approval недействителен |

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
| `integrations` | workspace_id, type ENUM(youtube, openai, smtp, email_api, telegram_bot, telegram_user, vk), name, status ENUM(not_configured, active, error, paused), config JSONB (несекретное), secret_id, last_error_code, last_error_message, last_checked_at | — |
| `email_accounts` | integration_id, sender_email, sender_name, domain, daily_limit, campaign_limit, sent_today, bounce_count, reply_count, unsubscribe_count | — |
| `telegram_accounts` | integration_id, account_name, account_type ENUM(bot, user), status, connection_status, session_secret_id, daily_limit, cooldown_seconds, schedule JSONB, sent_count, failed_count, health_score, paused_reason | — |
| `messages` | workspace_id, offer_id, offer_variant_id, contact_id, channel_type, account_ref, idempotency_key, status (ADR-0011), provider_message_id, attempts, scheduled_at, sent_at, error_code, error_message | UNIQUE(idempotency_key) |
| `message_events` | message_id, type ENUM(sent, delivered, opened, replied, positive_reply, negative_reply, bounced, opt_out, failed), payload JSONB, occurred_at | idx(message_id, occurred_at) |

### Operations (Phase 1+)
| Таблица | Ключевые колонки | Ограничения / индексы |
|---|---|---|
| `job_runs` | workspace_id, type, fingerprint, status, priority, queue, params JSONB, progress_total, progress_done, budget_usd, estimated_cost_usd, actual_cost_usd, procrastinate_job_id, parent_id, error_code, error_human, started_at, finished_at | partial UNIQUE(fingerprint) WHERE status IN (queued, running, retrying) |
| `budgets` | workspace_id, scope ENUM(global, project, job_type), scope_ref, period ENUM(day, month, total), limit_usd, max_ai_operations | — |
| `youtube_quota_ledger` | integration_id, quota_day DATE (Pacific), units_used, units_limit | UNIQUE(integration_id, quota_day) |
| `provider_cache` | provider, cache_key, response JSONB, expires_at | UNIQUE(provider, cache_key) |
| `integration_events` | integration_id, operation, http_status, error_code, latency_ms, created_at | idx(integration_id, created_at) |
| `notifications` | workspace_id, user_id, kind, payload JSONB, channel, sent_at, read_at | — |

## Соответствие сущностям ТЗ §30
User→`users`, Workspace→`workspaces`, SearchProject→`search_projects`, SearchQuery→`search_queries`, Niche→`taxonomy_nodes`, Channel→`channels`, Video→`videos`, Thumbnail→`thumbnails`+`image_assets`, ThumbnailAnalysis→`thumbnail_analyses`, ChannelAnalysis→`channel_analyses`, Lead→`leads`, LeadScore→`lead_scores`, Contact→`contacts`, GeneratedThumbnail→`generated_thumbnails`, GenerationJob→`generation_requests`+`job_runs`, Offer→`offers`, OfferVariant→`offer_variants`, Campaign→`campaigns`, CampaignLead→`campaign_leads`, Approval→`approvals`, Message→`messages`, MessageEvent→`message_events`, Integration→`integrations`, TelegramAccount→`telegram_accounts`, EmailAccount→`email_accounts`, Template→`templates`, ABTest→`ab_tests`, AuditLog→`audit_logs`.

## Роли БД
- `ytlead_owner` — владелец схемы (миграции).
- `ytlead_app` — DML для api/worker (без DDL).
- Суперпользователь `postgres` используется только для первичной настройки.
