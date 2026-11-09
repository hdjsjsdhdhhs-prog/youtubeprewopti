"""Campaigns (Phase 8.2, ТЗ §29, §27 «campaign limit», §28 A/B, §59 «campaign pause»; ADR-0010, ADR-0011).

* ``campaigns`` — a named outreach run over chosen leads: offer template / instructions, the A/B styles of
  the drafts, allowed channels, the send window (days, hours, time zone), a daily limit of its own and an
  automatic pause after repeated failures. Status: ``CAMPAIGN_MACHINE``.
* ``campaign_email_accounts`` — the SMTP accounts the campaign sends from (none = any active account).
* ``campaign_leads`` — a lead in the campaign: its A/B style (balanced random assignment), its offer and
  the outcome. A lead is *active* in at most one campaign at a time (partial unique index).

Nothing is approved here: every offer still goes through the Approval Queue one by one. A running campaign
only queues the message of an offer the human has approved (that approval is the consent to send), and the
dispatcher sends it within the campaign's window and limits (``rules.py``).
"""

from __future__ import annotations

from datetime import datetime, time
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    Time,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from app.core.db import Base, IdMixin, TimestampMixin
from app.core.state_machine import StateMachine
from app.core.types import pg_enum
from app.domains.offers.models import OfferStyle

DEFAULT_TIMEZONE = "Europe/Moscow"
DEFAULT_SEND_DAYS = (1, 2, 3, 4, 5)  # ISO weekdays, Monday = 1
DEFAULT_SEND_FROM = time(10, 0)
DEFAULT_SEND_TO = time(18, 0)
DEFAULT_CAMPAIGN_DAILY_LIMIT = 20
MAX_CAMPAIGN_DAILY_LIMIT = 500
DEFAULT_MAX_FAILURES = 3
MAX_FAILURES_LIMIT = 50


class CampaignStatus(StrEnum):
    """ADR-0011 / ТЗ §29."""

    DRAFT = "draft"  # being set up; not every requirement is met yet (no leads, no email account …)
    READY = "ready"  # can be started
    PENDING_APPROVAL = "pending_approval"  # offers are written and wait in the Approval Queue
    RUNNING = "running"  # approved offers are queued and sent within the window and limits
    PAUSED = "paused"  # by the owner or automatically: queued messages wait
    COMPLETED = "completed"  # every lead is done (or the owner finished it); queued messages cancelled
    ARCHIVED = "archived"  # hidden from the list; read-only


_C = CampaignStatus
CAMPAIGN_MACHINE: StateMachine[CampaignStatus] = StateMachine(
    "campaign",
    {
        _C.DRAFT: {_C.READY, _C.ARCHIVED},
        _C.READY: {_C.DRAFT, _C.PENDING_APPROVAL, _C.RUNNING, _C.ARCHIVED},
        _C.PENDING_APPROVAL: {_C.RUNNING, _C.COMPLETED, _C.ARCHIVED},
        _C.RUNNING: {_C.PAUSED, _C.COMPLETED},
        _C.PAUSED: {_C.RUNNING, _C.COMPLETED},
        _C.COMPLETED: {_C.ARCHIVED},
        _C.ARCHIVED: set(),
    },
)
# settings and leads can still change
OPEN_CAMPAIGN = frozenset({_C.DRAFT, _C.READY, _C.PENDING_APPROVAL, _C.RUNNING, _C.PAUSED})
# offers can be generated
GENERATING_CAMPAIGN = frozenset({_C.READY, _C.PENDING_APPROVAL, _C.RUNNING, _C.PAUSED})


class CampaignLeadStatus(StrEnum):
    ACTIVE = "active"  # in work: offer to write / review / send
    SENT = "sent"  # the message went out
    FAILED = "failed"  # the message could not be sent (see ``reason``)
    REJECTED = "rejected"  # the owner rejected the offer in the Approval Queue
    SKIPPED = "skipped"  # the lead was closed / opted out, or the campaign ended before the send
    REMOVED = "removed"  # taken out of the campaign by the owner


class Campaign(IdMixin, TimestampMixin, Base):
    __tablename__ = "campaigns"
    __table_args__ = (
        Index("ix_campaigns_workspace_status", "workspace_id", "status"),
        CheckConstraint(f"daily_limit BETWEEN 1 AND {MAX_CAMPAIGN_DAILY_LIMIT}", name="daily_limit_range"),
        CheckConstraint(
            f"max_consecutive_failures BETWEEN 1 AND {MAX_FAILURES_LIMIT}", name="max_failures_range"
        ),
        CheckConstraint("send_from < send_to", name="send_window_order"),
        CheckConstraint("cardinality(styles) BETWEEN 1 AND 3", name="styles_count"),
        CheckConstraint("cardinality(channels) >= 1", name="channels_count"),
        CheckConstraint("cardinality(send_days) >= 1", name="send_days_count"),
        CheckConstraint("consecutive_failures >= 0", name="failures_nonneg"),
    )
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("search_projects.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    status: Mapped[CampaignStatus] = mapped_column(
        pg_enum(CampaignStatus, "campaign_status"),
        nullable=False,
        default=CampaignStatus.DRAFT,
        server_default=CampaignStatus.DRAFT.value,
    )
    # the lead filters last used to add leads (for reference; the leads themselves are in campaign_leads)
    target_filters: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    template_id: Mapped[int | None] = mapped_column(ForeignKey("offer_templates.id", ondelete="SET NULL"))
    instructions: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    language: Mapped[str | None] = mapped_column(String(20))
    # A/B (ТЗ §28): the offer styles compared; each lead gets one of them
    styles: Mapped[list[str]] = mapped_column(ARRAY(String(20)), nullable=False)
    # allowed outreach channels in order of preference (email, telegram, vk, manual)
    channels: Mapped[list[str]] = mapped_column(ARRAY(String(20)), nullable=False)
    send_days: Mapped[list[int]] = mapped_column(ARRAY(SmallInteger), nullable=False)
    send_from: Mapped[time] = mapped_column(Time, nullable=False, default=DEFAULT_SEND_FROM)
    send_to: Mapped[time] = mapped_column(Time, nullable=False, default=DEFAULT_SEND_TO)
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default=DEFAULT_TIMEZONE, server_default=DEFAULT_TIMEZONE
    )
    starts_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))  # not before this moment
    daily_limit: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_CAMPAIGN_DAILY_LIMIT,
        server_default=str(DEFAULT_CAMPAIGN_DAILY_LIMIT),
    )
    max_consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_MAX_FAILURES, server_default=str(DEFAULT_MAX_FAILURES)
    )
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    paused_reason: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class CampaignEmailAccount(Base):
    __tablename__ = "campaign_email_accounts"
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), primary_key=True)
    email_account_id: Mapped[int] = mapped_column(
        ForeignKey("email_accounts.id", ondelete="CASCADE"), primary_key=True
    )


class CampaignLead(TimestampMixin, Base):
    __tablename__ = "campaign_leads"
    __table_args__ = (
        Index(
            "uq_campaign_leads_active_lead",
            "lead_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index("ix_campaign_leads_campaign_status", "campaign_id", "status"),
    )
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), primary_key=True)
    style: Mapped[OfferStyle] = mapped_column(pg_enum(OfferStyle, "offer_style"), nullable=False)
    status: Mapped[CampaignLeadStatus] = mapped_column(
        pg_enum(CampaignLeadStatus, "campaign_lead_status"),
        nullable=False,
        default=CampaignLeadStatus.ACTIVE,
        server_default=CampaignLeadStatus.ACTIVE.value,
    )
    offer_id: Mapped[int | None] = mapped_column(ForeignKey("offers.id", ondelete="SET NULL"), index=True)
    reason: Mapped[str | None] = mapped_column(Text)  # why skipped / failed / not queued yet
    added_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
