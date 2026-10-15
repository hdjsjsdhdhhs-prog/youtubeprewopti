from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from app.core.db import Base, IdMixin
from app.core.types import pg_enum


class ApprovalTarget(StrEnum):
    OFFER = "offer"  # offers arrive in Phase 7; target_id is not a foreign key until then


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    SKIPPED = "skipped"


class Approval(IdMixin, Base):
    """One human decision on a piece of outgoing content. ``content_hash`` is the SHA-256 of the exact
    content (text, attachment, contact …) at decision time: content changed after an approval makes it
    invalid. Append-only; the latest decision per target counts."""

    __tablename__ = "approvals"
    __table_args__ = (
        Index("ix_approvals_target", "workspace_id", "target_type", "target_id", "created_at"),
        CheckConstraint("length(content_hash) = 64", name="content_hash_sha256"),
    )
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    target_type: Mapped[ApprovalTarget] = mapped_column(
        pg_enum(ApprovalTarget, "approval_target"), nullable=False
    )
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    decision: Mapped[ApprovalDecision] = mapped_column(
        pg_enum(ApprovalDecision, "approval_decision"), nullable=False
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
