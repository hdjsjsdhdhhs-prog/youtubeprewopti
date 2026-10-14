from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, IdMixin, TimestampMixin
from app.core.types import pg_enum
from app.domains.analysis.audit import SUBSCORES

SCORE = Numeric(4, 2)  # 1.00 … 10.00 (DATABASE.md principles)


class ScoringKind(StrEnum):
    THUMBNAIL = "thumbnail"
    LEAD = "lead"
    PRIORITY = "priority"
    CONTACTABILITY = "contactability"


class AILevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ScoringProfile(IdMixin, TimestampMixin, Base):
    """Configurable weights of a deterministic score (ТЗ §7 «все веса конфигурируемые»). Saving new
    weights adds a version and makes it the active one; no row = the built-in defaults."""

    __tablename__ = "scoring_profiles"
    __table_args__ = (
        UniqueConstraint("workspace_id", "kind", "version"),
        Index(
            "uq_scoring_profiles_active", "workspace_id", "kind", unique=True,
            postgresql_where=text("is_active"),
        ),
        CheckConstraint("version >= 1", name="version_positive"),
    )
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[ScoringKind] = mapped_column(pg_enum(ScoringKind, "scoring_kind"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    weights: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class ThumbnailAnalysis(IdMixin, TimestampMixin, Base):
    """AI audit of one content-addressed image (global, like ``image_assets``: §49–50).

    Unique per (image, prompt version, model) — the analysis cache key (§34). A pHash near-duplicate
    reuses an existing audit: the row is a copy with ``reused_from_id`` set and no AI call of its own.
    ``overall_score`` is not stored: it depends on the workspace's scoring weights and is computed by code.
    """

    __tablename__ = "thumbnail_analyses"
    __table_args__ = (
        # explicit name: the conventional one (all three columns) exceeds PostgreSQL's 63 characters
        UniqueConstraint(
            "image_asset_id", "prompt_template_id", "model_key", name="uq_thumbnail_analyses_cache_key"
        ),
        Index(
            "uq_thumbnail_analyses_current", "image_asset_id", unique=True,
            postgresql_where=text("is_current"),
        ),
        Index("ix_thumbnail_analyses_problem_codes", "problem_codes", postgresql_using="gin"),
        *(CheckConstraint(f"{s} BETWEEN 1 AND 10", name=f"{s}_range") for s in SUBSCORES),
        CheckConstraint("text_word_count_est >= 0", name="word_count_nonneg"),
    )
    image_asset_id: Mapped[int] = mapped_column(
        ForeignKey("image_assets.id", ondelete="CASCADE"), nullable=False
    )
    prompt_template_id: Mapped[int] = mapped_column(ForeignKey("prompt_templates.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)  # "mock" => synthetic, labelled in UI
    model_key: Mapped[str] = mapped_column(String(60), nullable=False)
    ai_call_id: Mapped[int | None] = mapped_column(ForeignKey("ai_calls.id", ondelete="SET NULL"))
    reused_from_id: Mapped[int | None] = mapped_column(
        ForeignKey("thumbnail_analyses.id", ondelete="SET NULL")
    )
    phash_distance: Mapped[int | None] = mapped_column(Integer)  # Hamming distance to the reused source
    detail: Mapped[str] = mapped_column(String(10), nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    composition_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    contrast_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    text_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    hierarchy_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    subject_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    color_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    mobile_readability: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    topic_relevance: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    visual_impact: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    professionalism: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    redesign_potential: Mapped[Decimal] = mapped_column(SCORE, nullable=False)

    has_face: Mapped[bool] = mapped_column(Boolean, nullable=False)
    face_emotion: Mapped[str | None] = mapped_column(String(60))
    text_present: Mapped[bool] = mapped_column(Boolean, nullable=False)
    text_word_count_est: Mapped[int] = mapped_column(Integer, nullable=False)
    focal_point: Mapped[str] = mapped_column(Text, nullable=False)
    background_separation: Mapped[AILevel] = mapped_column(pg_enum(AILevel, "ai_level"), nullable=False)
    clutter_level: Mapped[AILevel] = mapped_column(pg_enum(AILevel, "ai_level"), nullable=False)
    improvement_opportunity: Mapped[AILevel] = mapped_column(
        pg_enum(AILevel, "ai_level"), nullable=False, index=True
    )
    problems: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    recommendations: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    problem_codes: Mapped[list[str]] = mapped_column(ARRAY(String(40)), nullable=False)  # for filters
    visual_summary: Mapped[str] = mapped_column(Text, nullable=False)


class ChannelAnalysis(IdMixin, TimestampMixin, Base):
    """Comparative AI audit of a channel's recent thumbnails (Phase 3.6, ТЗ §17). Global like
    ``thumbnail_analyses``.

    Cache key: (channel, ``inputs_digest``, prompt version, model). The digest covers the shown videos, their
    images and the thumbnail audits given to the model, so a new video, a changed thumbnail or a new
    individual audit means a new channel audit. Aggregates (avg / median / weak-medium-strong, best / worst)
    are not stored: they depend on the workspace's thumbnail weights and are computed on read.
    """

    __tablename__ = "channel_analyses"
    __table_args__ = (
        UniqueConstraint(
            "channel_id", "inputs_digest", "prompt_template_id", "model_key",
            name="uq_channel_analyses_cache_key",
        ),
        Index("uq_channel_analyses_current", "channel_id", unique=True, postgresql_where=text("is_current")),
        Index("ix_channel_analyses_problem_codes", "recurring_problem_codes", postgresql_using="gin"),
        CheckConstraint("visual_consistency BETWEEN 1 AND 10", name="visual_consistency_range"),
        CheckConstraint("outdated_style_score BETWEEN 1 AND 10", name="outdated_style_score_range"),
        CheckConstraint("cardinality(video_ids) >= 1", name="video_ids_not_empty"),
    )
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), nullable=False)
    prompt_template_id: Mapped[int] = mapped_column(ForeignKey("prompt_templates.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)  # "mock" => synthetic, labelled in UI
    model_key: Mapped[str] = mapped_column(String(60), nullable=False)
    ai_call_id: Mapped[int | None] = mapped_column(ForeignKey("ai_calls.id", ondelete="SET NULL"))
    detail: Mapped[str] = mapped_column(String(10), nullable=False)
    inputs_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # shown to the model in this order (prompt "Video 1" = video_ids[0]); no FK: videos may be deleted
    video_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)
    thumbnail_analysis_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)

    visual_consistency: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    repeated_template: Mapped[bool] = mapped_column(Boolean, nullable=False)
    outdated_style_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)  # higher = more outdated
    has_design_system: Mapped[bool] = mapped_column(Boolean, nullable=False)
    visual_opportunity: Mapped[AILevel] = mapped_column(
        pg_enum(AILevel, "ai_level"), nullable=False, index=True
    )
    patterns: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)  # better_performing_patterns
    recurring_problems: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    recurring_problem_codes: Mapped[list[str]] = mapped_column(ARRAY(String(40)), nullable=False)
    weakest_video_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)
    redesign_candidates: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)  # [{video_id, why}]
    summary: Mapped[str] = mapped_column(Text, nullable=False)
