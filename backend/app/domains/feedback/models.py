from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from app.core.db import Base, IdMixin, TimestampMixin
from app.core.types import pg_enum

SCORE_VALUE = Numeric(6, 2)  # covers 1–10 (thumbnail / channel audits) and 0–100 (lead scores)


class ScoreTarget(StrEnum):
    THUMBNAIL_ANALYSIS = "thumbnail_analysis"  # a stored AI thumbnail audit (``thumbnail_analyses.id``)
    CHANNEL_ANALYSIS = "channel_analysis"  # a stored AI channel audit (``channel_analyses.id``)
    LEAD = "lead"  # the lead's current Lead / Priority Score (``leads.id``; survives rescoring)


class ScoreOverride(IdMixin, TimestampMixin, Base):
    """«AI: 6.8, me: 5.5» (ТЗ §47): the human value next to the AI / code value, which is never
    overwritten. ``ai_value`` is the value at the moment of the (last) correction."""

    __tablename__ = "score_overrides"
    __table_args__ = (
        UniqueConstraint("workspace_id", "target_type", "target_id", "field"),
        Index("ix_score_overrides_target", "target_type", "target_id"),
    )
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    target_type: Mapped[ScoreTarget] = mapped_column(pg_enum(ScoreTarget, "score_target"), nullable=False)
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    field: Mapped[str] = mapped_column(String(40), nullable=False)
    ai_value: Mapped[Decimal] = mapped_column(SCORE_VALUE, nullable=False)
    human_value: Mapped[Decimal] = mapped_column(SCORE_VALUE, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class FeedbackKind(StrEnum):
    SCORE_OVERRIDE = "score_override"  # a human value set or changed
    SCORE_OVERRIDE_REMOVED = "score_override_removed"  # back to the AI value
    LEAD_STATUS = "lead_status"  # a manual lead decision (shortlist / select / reject …)
    GENERATION_DECISION = "generation_decision"  # approve / reject / final of a generated variant (Phase 5)


class FeedbackEvent(IdMixin, Base):
    """Append-only log of human decisions next to what the AI / code recommended (ТЗ §46): the base of the
    «AI vs human» analytics and, later, of feedback given to the models."""

    __tablename__ = "feedback_events"
    __table_args__ = (Index("ix_feedback_events_workspace_kind", "workspace_id", "kind", "created_at"),)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    kind: Mapped[FeedbackKind] = mapped_column(pg_enum(FeedbackKind, "feedback_kind"), nullable=False)
    target_type: Mapped[str] = mapped_column(String(40), nullable=False)
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    field: Mapped[str | None] = mapped_column(String(40))
    ai_value: Mapped[Decimal | None] = mapped_column(SCORE_VALUE)
    human_value: Mapped[Decimal | None] = mapped_column(SCORE_VALUE)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
