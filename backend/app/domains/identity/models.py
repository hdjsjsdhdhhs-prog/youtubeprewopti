from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import CITEXT, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from app.core.db import Base, IdMixin, TimestampMixin
from app.core.types import pg_enum


class WorkspaceRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


ROLE_RANK = {
    WorkspaceRole.VIEWER: 0, WorkspaceRole.OPERATOR: 1, WorkspaceRole.ADMIN: 2, WorkspaceRole.OWNER: 3,
}


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    last_login_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class Workspace(IdMixin, TimestampMixin, Base):
    __tablename__ = "workspaces"
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")


class WorkspaceMember(IdMixin, TimestampMixin, Base):
    __tablename__ = "workspace_members"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id"),)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[WorkspaceRole] = mapped_column(pg_enum(WorkspaceRole, "workspace_role"), nullable=False)


class UserSession(IdMixin, Base):
    __tablename__ = "sessions"
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(400))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class AuditLog(IdMixin, Base):
    __tablename__ = "audit_logs"
    workspace_id: Mapped[int | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), index=True
    )
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(100))
    entity_id: Mapped[str | None] = mapped_column(String(100))
    diff: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class IdempotencyKey(IdMixin, Base):
    """Stored response for an ``Idempotency-Key`` request (ADR-0011).

    ``response_status`` NULL = in progress."""

    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("workspace_id", "key"),)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(),
                                                 nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, index=True)


class Secret(IdMixin, TimestampMixin, Base):
    __tablename__ = "secrets"
    workspace_id: Mapped[int | None] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    purpose: Mapped[str] = mapped_column(String(100), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
