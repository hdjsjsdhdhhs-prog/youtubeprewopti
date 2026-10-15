from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from app.core.db import Base, IdMixin, TimestampMixin
from app.core.state_machine import LeadStatus
from app.core.types import pg_enum
from app.domains.leads.scoring import COMPONENTS

LEAD_SCORE = Numeric(5, 2)  # 0.00 … 100.00 (DATABASE.md principles)


class IdealLeadProfile(IdMixin, TimestampMixin, Base):
    """Configurable Ideal Lead Profile (ТЗ §52); the workspace's default one drives Channel Fit."""

    __tablename__ = "ideal_lead_profiles"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name"),
        Index(
            "uq_ideal_lead_profiles_default",
            "workspace_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    criteria: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)  # ``IdealLeadCriteria``
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class Lead(IdMixin, TimestampMixin, Base):
    """A channel as a sales prospect of one workspace (one lead per channel and workspace). Created
    automatically when a channel joins one of the workspace's projects; status follows ``LEAD_MACHINE``."""

    __tablename__ = "leads"
    __table_args__ = (
        UniqueConstraint("workspace_id", "channel_id"),
        Index("ix_leads_workspace_status", "workspace_id", "status"),
    )
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[LeadStatus] = mapped_column(
        pg_enum(LeadStatus, "lead_status"),
        nullable=False,
        default=LeadStatus.DISCOVERED,
        server_default=LeadStatus.DISCOVERED.value,
    )
    status_changed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    created_from_project_id: Mapped[int | None] = mapped_column(
        ForeignKey("search_projects.id", ondelete="SET NULL")
    )
    # set when an operator rejects the lead (Phase 4), cleared when it leaves REJECTED
    rejected_reason: Mapped[str | None] = mapped_column(Text)


class LeadScore(IdMixin, TimestampMixin, Base):
    """One computation of a lead's scores. A new row is added only when the result changes (the previous
    one stays as history); exactly one row per lead is current. Points per component are columns (filters,
    sorting); the explanation (fraction, reason, inputs per component) is ``components``."""

    __tablename__ = "lead_scores"
    __table_args__ = (
        Index("uq_lead_scores_current", "lead_id", unique=True, postgresql_where=text("is_current")),
        Index("ix_lead_scores_total", "total"),
        Index("ix_lead_scores_priority_score", "priority_score"),
        CheckConstraint("total BETWEEN 0 AND 100", name="total_range"),
        CheckConstraint("priority_score BETWEEN 0 AND 100", name="priority_score_range"),
        CheckConstraint("data_coverage BETWEEN 0 AND 100", name="data_coverage_range"),
        CheckConstraint("avg_thumbnail_score BETWEEN 1 AND 10", name="avg_thumbnail_score_range"),
        CheckConstraint("contactability_score BETWEEN 0 AND 100", name="contactability_score_range"),
        *(CheckConstraint(f"{c} BETWEEN 0 AND 100", name=f"{c}_range") for c in COMPONENTS),
    )
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # weights the result was computed with (0 = built-in defaults); ILP NULL = none was set
    lead_profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    priority_profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    thumbnail_profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    contactability_profile_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    ideal_lead_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("ideal_lead_profiles.id", ondelete="SET NULL")
    )
    total: Mapped[Decimal] = mapped_column(LEAD_SCORE, nullable=False)
    priority_score: Mapped[Decimal] = mapped_column(LEAD_SCORE, nullable=False)
    data_coverage: Mapped[Decimal] = mapped_column(LEAD_SCORE, nullable=False)
    thumbnail_opportunity: Mapped[Decimal] = mapped_column(LEAD_SCORE, nullable=False)
    channel_fit: Mapped[Decimal] = mapped_column(LEAD_SCORE, nullable=False)
    audience: Mapped[Decimal] = mapped_column(LEAD_SCORE, nullable=False)
    views: Mapped[Decimal] = mapped_column(LEAD_SCORE, nullable=False)
    activity: Mapped[Decimal] = mapped_column(LEAD_SCORE, nullable=False)
    growth: Mapped[Decimal] = mapped_column(LEAD_SCORE, nullable=False)
    contactability: Mapped[Decimal] = mapped_column(LEAD_SCORE, nullable=False)
    niche_value: Mapped[Decimal] = mapped_column(LEAD_SCORE, nullable=False)
    avg_thumbnail_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), index=True)
    # ТЗ §19 Contactability Score 0–100 (workspace contactability weights); NULL = contacts never scanned
    contactability_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), index=True)
    components: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)


class LeadStatusHistory(IdMixin, Base):
    """Every status change (ADR-0011); ``actor_user_id`` NULL = automatic (``reason`` says why)."""

    __tablename__ = "lead_status_history"
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_status: Mapped[LeadStatus | None] = mapped_column(pg_enum(LeadStatus, "lead_status"))
    to_status: Mapped[LeadStatus] = mapped_column(pg_enum(LeadStatus, "lead_status"), nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# Tags (ТЗ §36, Phase 4)
# ---------------------------------------------------------------------------
class TagSource(StrEnum):
    USER = "user"  # assigned by an operator
    SYSTEM = "system"  # assigned by a deterministic rule on every rescore (``tags.SYSTEM_TAGS``)


class Tag(IdMixin, TimestampMixin, Base):
    """Workspace tag. ``name_key`` (casefolded name) is unique per workspace. System tags (``system_key``
    set) are created on demand by the rescore and are assigned only by their rules."""

    __tablename__ = "tags"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name_key"),
        UniqueConstraint("workspace_id", "system_key"),
    )
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    name_key: Mapped[str] = mapped_column(String(60), nullable=False)
    color: Mapped[str | None] = mapped_column(String(7))  # "#rrggbb"
    system_key: Mapped[str | None] = mapped_column(String(40))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class LeadTag(Base):
    __tablename__ = "lead_tags"
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    source: Mapped[TagSource] = mapped_column(pg_enum(TagSource, "lead_tag_source"), nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
