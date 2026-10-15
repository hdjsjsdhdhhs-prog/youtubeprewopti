"""Contacts (ТЗ §18–19, DATABASE.md «Contacts»).

* ``contacts`` — public contacts found in a channel's own texts are **global** (``workspace_id`` NULL, like
  channels); a contact an operator adds by hand belongs to that workspace only.
* ``contact_states`` — a workspace's verdict on a contact (verified / invalid / opted_out …): workspaces never
  see each other's decisions on the shared public contacts.
* ``suppression_list`` — workspace-level «never contact» values (opt-out, bounce, complaint, manual); checked
  for every contact shown and, from Phase 8, before every send.
* ``channel_contact_scans`` — when the channel's texts were last scanned and with which extractor: a scan
  without usable contacts is the ТЗ status ``not_found``.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

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
    func,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from app.core.db import Base, IdMixin, TimestampMixin
from app.core.types import pg_enum


class ContactType(StrEnum):
    EMAIL = "email"
    TELEGRAM = "telegram"
    VK = "vk"
    INSTAGRAM = "instagram"
    WEBSITE = "website"
    OTHER = "other"  # other public social profiles (Facebook, X, TikTok, Boosty, Linktree …)


class ContactSource(StrEnum):
    YOUTUBE_ABOUT = "youtube_about"  # reserved: the «About» page is not in the Data API (no scraping, ТЗ §18)
    YOUTUBE_DESCRIPTION = "youtube_description"  # the channel description (Data API)
    VIDEO_DESCRIPTION = "video_description"  # descriptions of the channel's stored videos
    WEBSITE = "website"  # reserved: the channel's website (not fetched yet)
    LINKED_PROFILE = "linked_profile"  # reserved
    MANUAL = "manual"  # added by an operator


class ContactStatus(StrEnum):
    PUBLIC = "public"  # published by the channel itself
    VERIFIED = "verified"  # an operator confirmed it
    UNVERIFIED = "unverified"
    INVALID = "invalid"  # does not work / not the channel's
    OPTED_OUT = "opted_out"  # asked not to be contacted (also in the suppression list)


# statuses that do not count as a way to reach the channel
UNUSABLE = frozenset({ContactStatus.INVALID, ContactStatus.OPTED_OUT})


class SuppressionReason(StrEnum):
    OPT_OUT = "opt_out"
    BOUNCE = "bounce"
    MANUAL = "manual"
    COMPLAINT = "complaint"


class Contact(IdMixin, TimestampMixin, Base):
    __tablename__ = "contacts"
    __table_args__ = (
        UniqueConstraint(
            "channel_id", "workspace_id", "type", "value_normalized", postgresql_nulls_not_distinct=True
        ),
        Index("ix_contacts_value", "type", "value_normalized"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="confidence_range"),
        CheckConstraint("(workspace_id IS NULL) = (source <> 'manual')", name="manual_is_workspace_owned"),
    )
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), nullable=False)
    workspace_id: Mapped[int | None] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    type: Mapped[ContactType] = mapped_column(pg_enum(ContactType, "contact_type"), nullable=False)
    value: Mapped[str] = mapped_column(String(320), nullable=False)
    value_normalized: Mapped[str] = mapped_column(String(320), nullable=False)
    source: Mapped[ContactSource] = mapped_column(pg_enum(ContactSource, "contact_source"), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(500))
    source_video_id: Mapped[int | None] = mapped_column(ForeignKey("videos.id", ondelete="SET NULL"))
    seen_in_videos: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False)
    status: Mapped[ContactStatus] = mapped_column(
        pg_enum(ContactStatus, "contact_status"),
        nullable=False,
        default=ContactStatus.PUBLIC,
        server_default=ContactStatus.PUBLIC.value,
    )
    # False: no longer in the channel's texts (kept for history, does not count for Contactability)
    present: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    discovered_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    verified_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    note: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class ContactState(Base):
    """A workspace's own status of a public contact (overrides ``contacts.status`` for that workspace)."""

    __tablename__ = "contact_states"
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[ContactStatus] = mapped_column(pg_enum(ContactStatus, "contact_status"), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    verified_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class SuppressionEntry(IdMixin, Base):
    __tablename__ = "suppression_list"
    __table_args__ = (UniqueConstraint("workspace_id", "type", "value_normalized"),)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    type: Mapped[ContactType] = mapped_column(pg_enum(ContactType, "contact_type"), nullable=False)
    value_normalized: Mapped[str] = mapped_column(String(320), nullable=False)
    reason: Mapped[SuppressionReason] = mapped_column(
        pg_enum(SuppressionReason, "suppression_reason"), nullable=False
    )
    source_message_id: Mapped[int | None] = mapped_column(BigInteger)  # outreach messages arrive in Phase 8
    note: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class ChannelContactScan(Base):
    __tablename__ = "channel_contact_scans"
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), primary_key=True)
    extractor_version: Mapped[int] = mapped_column(Integer, nullable=False)
    inputs_digest: Mapped[str] = mapped_column(String(64), nullable=False)  # texts scanned (skip if same)
    texts_scanned: Mapped[int] = mapped_column(Integer, nullable=False)
    contacts_found: Mapped[int] = mapped_column(Integer, nullable=False)
    scanned_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
