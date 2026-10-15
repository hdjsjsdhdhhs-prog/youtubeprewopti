"""Offers (ТЗ §20–23, §28; DATABASE.md «Offers & approvals»; ADR-0011 offer lifecycle).

* ``offers`` — one per lead and outreach attempt: status (``OFFER_MACHINE``), chosen contact and channel,
  attached free sample, the facts snapshot the drafts were written from (``inputs``: the guard and the
  personalization score are checked against exactly these), cost and job of the last generation round.
  At most one *open* offer per lead (partial unique index); «Regenerate» adds a new round to it.
* ``offer_variants`` — A / B / C drafts of a round (professional / short / free-sample focus), the AI output
  as returned, the guard issues and the personalization score; exactly one variant of an offer is selected.
* ``offer_status_history`` — every status change with the actor.
* ``offer_templates`` — the owner's offer templates (versions: editing adds a version, offers keep theirs).

Nothing here sends anything: sending is Phase 8 and requires the approval (``approvals``) to match the
current content hash.
"""

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
from app.core.state_machine import StateMachine
from app.core.types import pg_enum
from app.domains.ai.models import COST

MAX_OFFER_VARIANTS = 3


class OfferStatus(StrEnum):
    DRAFT = "draft"  # drafts are being generated
    PENDING_APPROVAL = "pending_approval"  # in the Approval Queue
    APPROVED = "approved"  # a human approved exactly the current content
    SCHEDULED = "scheduled"  # approved + a message queued for a send time (or waiting for the operator)
    REJECTED = "rejected"  # the owner rejected the offer («Regenerate» reopens it)
    FAILED = "failed"  # no draft could be generated («Regenerate» reopens it)
    SENT = "sent"  # the message went out (Phase 8): final


_O = OfferStatus
OPEN_STATUSES = (_O.DRAFT, _O.PENDING_APPROVAL, _O.APPROVED, _O.SCHEDULED)
OFFER_MACHINE: StateMachine[OfferStatus] = StateMachine(
    "offer",
    {
        _O.DRAFT: {_O.PENDING_APPROVAL, _O.FAILED},
        _O.PENDING_APPROVAL: {_O.APPROVED, _O.REJECTED, _O.DRAFT},
        # editing approved content invalidates the approval => back to the queue
        _O.APPROVED: {_O.SCHEDULED, _O.PENDING_APPROVAL, _O.REJECTED, _O.DRAFT, _O.SENT},
        # SCHEDULED → APPROVED: the queued message was cancelled or failed (send again later)
        _O.SCHEDULED: {_O.APPROVED, _O.PENDING_APPROVAL, _O.REJECTED, _O.DRAFT, _O.SENT},
        _O.REJECTED: {_O.DRAFT},
        _O.FAILED: {_O.DRAFT},
        _O.SENT: set(),
    },
)


class OutreachChannel(StrEnum):
    """How the offer will be sent (ТЗ §23 «канал отправки»). ``manual`` = the operator sends it himself
    (ADR-0010 ManualSendProvider) — possible with any contact type."""

    EMAIL = "email"
    TELEGRAM = "telegram"
    VK = "vk"
    MANUAL = "manual"


class OfferStyle(StrEnum):
    """ТЗ §28: A — professional, B — as short as possible, C — focused on the free sample."""

    PROFESSIONAL = "professional"
    SHORT = "short"
    FREE_SAMPLE = "free_sample"


STYLES: tuple[OfferStyle, ...] = (OfferStyle.PROFESSIONAL, OfferStyle.SHORT, OfferStyle.FREE_SAMPLE)
LABELS = "ABC"


class OfferVariantStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"  # invalid output after repair, provider error
    REFUSED = "refused"  # the model refused


OFFER_SCORE = Numeric(5, 2)


class OfferTemplate(IdMixin, TimestampMixin, Base):
    """An offer template of the owner: guidance / skeleton the model follows (it is data, not code).
    ``key`` groups the versions of one template; editing adds ``version + 1``; archived = hidden."""

    __tablename__ = "offer_templates"
    __table_args__ = (UniqueConstraint("workspace_id", "key", "version"),)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    key: Mapped[str] = mapped_column(String(36), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class Offer(IdMixin, TimestampMixin, Base):
    __tablename__ = "offers"
    __table_args__ = (
        Index("ix_offers_workspace_status", "workspace_id", "status"),
        Index(
            "uq_offers_open_per_lead",
            "lead_id",
            unique=True,
            postgresql_where=text("status IN ('draft', 'pending_approval', 'approved', 'scheduled')"),
        ),
        CheckConstraint("round >= 1", name="round_positive"),
        CheckConstraint(f"variant_count BETWEEN 1 AND {MAX_OFFER_VARIANTS}", name="variant_count_range"),
    )
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[int | None] = mapped_column(ForeignKey("search_projects.id", ondelete="SET NULL"))
    status: Mapped[OfferStatus] = mapped_column(
        pg_enum(OfferStatus, "offer_status"),
        nullable=False,
        default=OfferStatus.DRAFT,
        server_default=OfferStatus.DRAFT.value,
    )
    round: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    variant_count: Mapped[int] = mapped_column(Integer, nullable=False)
    language: Mapped[str] = mapped_column(String(20), nullable=False)
    instructions: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    template_id: Mapped[int | None] = mapped_column(ForeignKey("offer_templates.id", ondelete="SET NULL"))
    # facts snapshot of the current round (``OfferInputs``): the drafts, the guard and the score use it
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"))
    channel: Mapped[OutreachChannel | None] = mapped_column(pg_enum(OutreachChannel, "outreach_channel"))
    attachment_variant_id: Mapped[int | None] = mapped_column(
        ForeignKey("generated_thumbnails.id", ondelete="SET NULL")
    )
    # sender for the email channel (Phase 8); None = the workspace's first active account at send time
    email_account_id: Mapped[int | None] = mapped_column(ForeignKey("email_accounts.id", ondelete="SET NULL"))
    manually_edited: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    skipped_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))  # «Skip»: end of the queue
    model_key: Mapped[str | None] = mapped_column(String(60))
    prompt_template_id: Mapped[int | None] = mapped_column(
        ForeignKey("prompt_templates.id", ondelete="SET NULL")
    )
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(COST)
    job_run_id: Mapped[int | None] = mapped_column(ForeignKey("job_runs.id", ondelete="SET NULL"))
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class OfferVariant(IdMixin, TimestampMixin, Base):
    __tablename__ = "offer_variants"
    __table_args__ = (
        UniqueConstraint("offer_id", "round", "label"),
        Index("uq_offer_variants_selected", "offer_id", unique=True, postgresql_where=text("is_selected")),
        CheckConstraint("personalization_score BETWEEN 0 AND 100", name="personalization_score_range"),
    )
    offer_id: Mapped[int] = mapped_column(ForeignKey("offers.id", ondelete="CASCADE"), nullable=False)
    round: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(1), nullable=False)
    style: Mapped[OfferStyle] = mapped_column(pg_enum(OfferStyle, "offer_style"), nullable=False)
    status: Mapped[OfferVariantStatus] = mapped_column(
        pg_enum(OfferVariantStatus, "offer_variant_status"),
        nullable=False,
        default=OfferVariantStatus.PENDING,
        server_default=OfferVariantStatus.PENDING.value,
    )
    is_selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    subject: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    cta: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    # the AI draft as returned (structure, facts_used, claims); kept after manual edits
    draft: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    guard_issues: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    personalization_score: Mapped[Decimal | None] = mapped_column(OFFER_SCORE)
    personalization_sources: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    model_key: Mapped[str | None] = mapped_column(String(60))
    ai_call_id: Mapped[int | None] = mapped_column(ForeignKey("ai_calls.id", ondelete="SET NULL"))
    cost_usd: Mapped[Decimal | None] = mapped_column(COST)
    error: Mapped[str | None] = mapped_column(Text)
    edited_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    edited_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class OfferStatusHistory(IdMixin, Base):
    __tablename__ = "offer_status_history"
    offer_id: Mapped[int] = mapped_column(
        ForeignKey("offers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_status: Mapped[OfferStatus | None] = mapped_column(pg_enum(OfferStatus, "offer_status"))
    to_status: Mapped[OfferStatus] = mapped_column(pg_enum(OfferStatus, "offer_status"), nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
