from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from app.core.db import Base, IdMixin, TimestampMixin
from app.core.types import pg_enum
from app.domains.ai.models import COST

MAX_VARIANTS = 6


class StyleReference(IdMixin, TimestampMixin, Base):
    """A reference image the owner uploaded (ТЗ §43): sent to the image model as a style example."""

    __tablename__ = "style_references"
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    image_asset_id: Mapped[int] = mapped_column(
        ForeignKey("image_assets.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    style: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    category: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String(60)), nullable=False, default=list, server_default="{}"
    )
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class GenerationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"  # every variant finished (ready, failed or refused)
    FAILED = "failed"  # the job stopped (budget, provider error); finished variants are kept


class GenerationRequest(IdMixin, TimestampMixin, Base):
    """One confirmed generation run for a lead: the brief built from the analysis + the owner's
    instructions, the model and settings, the estimate the owner confirmed (``estimated_cost_usd`` is also
    the job's hard cost cap)."""

    __tablename__ = "generation_requests"
    __table_args__ = (
        Index("ix_generation_requests_lead", "lead_id", "created_at"),
        CheckConstraint(f"variant_count BETWEEN 1 AND {MAX_VARIANTS}", name="variant_count_range"),
    )
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("search_projects.id", ondelete="SET NULL"))
    source_video_id: Mapped[int | None] = mapped_column(ForeignKey("videos.id", ondelete="SET NULL"))
    parent_variant_id: Mapped[int | None] = mapped_column(
        BigInteger
    )  # «create more like this» (no FK: cycle)
    instructions: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    reference_ids: Mapped[list[int]] = mapped_column(
        ARRAY(BigInteger), nullable=False, default=list, server_default="{}"
    )
    variant_count: Mapped[int] = mapped_column(Integer, nullable=False)
    model_key: Mapped[str] = mapped_column(String(60), nullable=False)
    size: Mapped[str | None] = mapped_column(String(20))
    quality: Mapped[str | None] = mapped_column(String(20))
    brief: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)  # what the prompt was built from
    prompt_template_id: Mapped[int | None] = mapped_column(
        ForeignKey("prompt_templates.id", ondelete="SET NULL")
    )
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(COST)
    status: Mapped[GenerationStatus] = mapped_column(
        pg_enum(GenerationStatus, "generation_status"),
        nullable=False,
        default=GenerationStatus.QUEUED,
        server_default=GenerationStatus.QUEUED.value,
    )
    error: Mapped[str | None] = mapped_column(Text)
    job_run_id: Mapped[int | None] = mapped_column(ForeignKey("job_runs.id", ondelete="SET NULL"))
    requested_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class VariantStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"  # generated, waiting for a decision
    FAILED = "failed"
    REFUSED = "refused"  # blocked by the provider's moderation (not an infrastructure failure)
    APPROVED = "approved"
    REJECTED = "rejected"
    FINAL = "final"  # «use as final free sample»: at most one per lead


DECIDABLE = frozenset(
    {VariantStatus.READY, VariantStatus.APPROVED, VariantStatus.REJECTED, VariantStatus.FINAL}
)


class GeneratedThumbnail(IdMixin, TimestampMixin, Base):
    """One variant (A, B, C …): the exact prompt, model, settings and source images it was made from, its
    cost and status (ТЗ §13)."""

    __tablename__ = "generated_thumbnails"
    __table_args__ = (
        UniqueConstraint("generation_request_id", "label"),
        Index("ix_generated_thumbnails_status", "status"),
    )
    generation_request_id: Mapped[int] = mapped_column(
        ForeignKey("generation_requests.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("generated_thumbnails.id", ondelete="SET NULL"))
    label: Mapped[str] = mapped_column(String(2), nullable=False)
    strategy: Mapped[str] = mapped_column(String(40), nullable=False)
    image_asset_id: Mapped[int | None] = mapped_column(ForeignKey("image_assets.id", ondelete="SET NULL"))
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    model_key: Mapped[str] = mapped_column(String(60), nullable=False)
    api_model_id: Mapped[str | None] = mapped_column(String(120))
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    source_image_ids: Mapped[list[int]] = mapped_column(
        ARRAY(BigInteger), nullable=False, default=list, server_default="{}"
    )  # image assets sent to the model (current thumbnail / parent variant, then references)
    ai_call_id: Mapped[int | None] = mapped_column(ForeignKey("ai_calls.id", ondelete="SET NULL"))
    cost_usd: Mapped[Decimal | None] = mapped_column(COST)
    status: Mapped[VariantStatus] = mapped_column(
        pg_enum(VariantStatus, "variant_status"),
        nullable=False,
        default=VariantStatus.PENDING,
        server_default=VariantStatus.PENDING.value,
    )
    error: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
