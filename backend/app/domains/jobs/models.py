from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from app.core.db import Base, IdMixin, TimestampMixin
from app.core.state_machine import JobStatus
from app.core.types import pg_enum

ACTIVE_STATUS_SQL = "status IN ('queued', 'running', 'retrying')"


class JobRun(IdMixin, TimestampMixin, Base):
    """Product-level view of a background job (ADR-0003). UI reads only this table."""

    __tablename__ = "job_runs"
    __table_args__ = (
        Index(
            "uq_job_runs_active_fingerprint", "fingerprint",
            unique=True, postgresql_where=text(ACTIVE_STATUS_SQL),
        ),
        Index("ix_job_runs_workspace_created", "workspace_id", "created_at"),
        Index(
            "ix_job_runs_undispatched", "id",
            postgresql_where=text("status = 'queued' AND procrastinate_job_id IS NULL"),
        ),
    )
    workspace_id: Mapped[int | None] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(60), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        pg_enum(JobStatus, "job_status"), nullable=False, default=JobStatus.QUEUED,
        server_default=JobStatus.QUEUED.value,
    )
    queue: Mapped[str] = mapped_column(String(30), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    progress_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    progress_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    budget_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    actual_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, default=Decimal(0), server_default="0"
    )
    procrastinate_job_id: Mapped[int | None] = mapped_column(BigInteger)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("job_runs.id", ondelete="SET NULL"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_human: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
