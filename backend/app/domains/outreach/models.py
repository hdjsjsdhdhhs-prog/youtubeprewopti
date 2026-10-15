"""Outreach (Phase 8.1; DATABASE.md «Outreach»; ADR-0010, ADR-0011).

* ``email_accounts`` — SMTP accounts of a workspace: server, sender, the password in ``secrets`` (never in
  this table), daily limit, randomised cooldown between sends, opt-out text, health (consecutive failures →
  automatic pause).
* ``messages`` — one outgoing message built from an *approved* offer: an exact snapshot of what is sent
  (recipient, subject, text with the opt-out footer, attachment), the approval it relies on and its
  ``content_hash``. ``idempotency_key`` = hash(variant, contact, channel) is unique among live messages: a
  repeated request or job never creates a second send. Non-email channels (Telegram, VK, manual) are sent
  by the operator (``manual`` status) — there is no automatic provider for them yet (Q-001).
* ``message_events`` — what happened after: sent, delivered, opened, replied, positive / negative reply,
  bounced, opt-out, failed, cancelled (SMTP reports none of these by itself: the operator records them).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from app.core.db import Base, IdMixin, TimestampMixin
from app.core.state_machine import StateMachine
from app.core.types import pg_enum
from app.domains.offers.models import OutreachChannel

DEFAULT_DAILY_LIMIT = 20
DEFAULT_COOLDOWN_MIN = 60
DEFAULT_COOLDOWN_MAX = 180
MAX_DAILY_LIMIT = 500
DEFAULT_OPT_OUT_TEXT = (
    "Если вы не хотите получать от меня письма, просто ответьте «нет» — я больше не напишу.\n"
    "If you'd rather not hear from me, just reply \"no\" and I won't write again."
)


class SmtpSecurityMode(StrEnum):
    STARTTLS = "starttls"
    SSL = "ssl"
    NONE = "none"


class AccountStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"  # by the owner or automatically after repeated failures
    ERROR = "error"  # the server refused the login: fix the settings, then resume


class EmailAccount(IdMixin, TimestampMixin, Base):
    __tablename__ = "email_accounts"
    __table_args__ = (
        CheckConstraint(f"daily_limit BETWEEN 1 AND {MAX_DAILY_LIMIT}", name="daily_limit_range"),
        CheckConstraint(
            "cooldown_min_seconds >= 0 AND cooldown_max_seconds >= cooldown_min_seconds",
            name="cooldown_range",
        ),
        CheckConstraint("smtp_port BETWEEN 1 AND 65535", name="smtp_port_range"),
    )
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    from_email: Mapped[str] = mapped_column(String(320), nullable=False)
    from_name: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")
    reply_to: Mapped[str | None] = mapped_column(String(320))
    smtp_host: Mapped[str] = mapped_column(String(255), nullable=False)
    smtp_port: Mapped[int] = mapped_column(Integer, nullable=False)
    smtp_security: Mapped[SmtpSecurityMode] = mapped_column(
        pg_enum(SmtpSecurityMode, "smtp_security"), nullable=False
    )
    smtp_username: Mapped[str] = mapped_column(String(320), nullable=False, default="", server_default="")
    secret_id: Mapped[int | None] = mapped_column(ForeignKey("secrets.id", ondelete="SET NULL"))
    status: Mapped[AccountStatus] = mapped_column(
        pg_enum(AccountStatus, "email_account_status"),
        nullable=False,
        default=AccountStatus.ACTIVE,
        server_default=AccountStatus.ACTIVE.value,
    )
    paused_reason: Mapped[str | None] = mapped_column(Text)
    daily_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_DAILY_LIMIT, server_default=str(DEFAULT_DAILY_LIMIT)
    )
    cooldown_min_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_COOLDOWN_MIN, server_default=str(DEFAULT_COOLDOWN_MIN)
    )
    cooldown_max_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_COOLDOWN_MAX, server_default=str(DEFAULT_COOLDOWN_MAX)
    )
    opt_out_text: Mapped[str] = mapped_column(Text, nullable=False, default=DEFAULT_OPT_OUT_TEXT)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_send_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))  # cooldown reservation
    last_sent_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(60))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    last_checked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class MessageStatus(StrEnum):
    QUEUED = "queued"  # waiting for its time / account cooldown (also between retries: next_attempt_at)
    SENDING = "sending"  # handed to the server; a crash here is never re-sent automatically
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"
    MANUAL = "manual"  # the operator sends it himself (Telegram, VK, manual) and marks it sent


LIVE_STATUSES = (MessageStatus.QUEUED, MessageStatus.SENDING, MessageStatus.SENT, MessageStatus.MANUAL)
ACTIVE_STATUSES = (MessageStatus.QUEUED, MessageStatus.SENDING, MessageStatus.MANUAL)

_M = MessageStatus
MESSAGE_MACHINE: StateMachine[MessageStatus] = StateMachine(
    "message",
    {
        _M.QUEUED: {_M.SENDING, _M.CANCELLED, _M.FAILED},
        _M.SENDING: {_M.SENT, _M.QUEUED, _M.FAILED},  # QUEUED = a transient error, retried later
        _M.MANUAL: {_M.SENT, _M.CANCELLED},
        _M.SENT: set(),
        _M.FAILED: set(),
        _M.CANCELLED: set(),
    },
)


class Message(IdMixin, TimestampMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index(
            "uq_messages_live_idempotency_key",
            "idempotency_key",
            unique=True,
            postgresql_where=text("status IN ('queued', 'sending', 'sent', 'manual')"),
        ),
        Index("ix_messages_due", "status", "scheduled_at"),
        Index("ix_messages_workspace_created", "workspace_id", "created_at"),
        CheckConstraint("length(content_hash) = 64", name="content_hash_sha256"),
        CheckConstraint("attempts >= 0", name="attempts_nonneg"),
    )
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    offer_id: Mapped[int] = mapped_column(
        ForeignKey("offers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    offer_variant_id: Mapped[int | None] = mapped_column(ForeignKey("offer_variants.id", ondelete="SET NULL"))
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"))
    approval_id: Mapped[int | None] = mapped_column(ForeignKey("approvals.id", ondelete="SET NULL"))
    channel: Mapped[OutreachChannel] = mapped_column(
        pg_enum(OutreachChannel, "outreach_channel"), nullable=False
    )
    email_account_id: Mapped[int | None] = mapped_column(ForeignKey("email_accounts.id", ondelete="SET NULL"))
    contact_type: Mapped[str] = mapped_column(String(20), nullable=False)
    to_address: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    body: Mapped[str] = mapped_column(Text, nullable=False)  # exactly what is sent (with the opt-out footer)
    attachment_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("image_assets.id", ondelete="SET NULL")
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # the approved offer content
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[MessageStatus] = mapped_column(pg_enum(MessageStatus, "message_status"), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    scheduled_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    provider_message_id: Mapped[str | None] = mapped_column(String(300))
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_message: Mapped[str | None] = mapped_column(Text)
    job_run_id: Mapped[int | None] = mapped_column(ForeignKey("job_runs.id", ondelete="SET NULL"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class MessageEventType(StrEnum):
    SENT = "sent"
    DELIVERED = "delivered"
    OPENED = "opened"
    REPLIED = "replied"
    POSITIVE_REPLY = "positive_reply"
    NEGATIVE_REPLY = "negative_reply"
    BOUNCED = "bounced"
    OPT_OUT = "opt_out"
    FAILED = "failed"
    CANCELLED = "cancelled"


# events an operator may record on a sent message
MANUAL_EVENTS = frozenset(
    {
        MessageEventType.DELIVERED,
        MessageEventType.OPENED,
        MessageEventType.REPLIED,
        MessageEventType.POSITIVE_REPLY,
        MessageEventType.NEGATIVE_REPLY,
        MessageEventType.BOUNCED,
        MessageEventType.OPT_OUT,
    }
)


class MessageEvent(IdMixin, Base):
    __tablename__ = "message_events"
    __table_args__ = (Index("ix_message_events_message", "message_id", "occurred_at"),)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), nullable=False)
    type: Mapped[MessageEventType] = mapped_column(
        pg_enum(MessageEventType, "message_event_type"), nullable=False
    )
    note: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    occurred_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
