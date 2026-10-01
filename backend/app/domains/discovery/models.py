from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, Integer, PrimaryKeyConstraint, String, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from app.core.db import Base


class YouTubeQuotaLedger(Base):
    """Units spent per provider and quota day (ADR-0008). Google resets the quota at midnight
    Pacific Time, so ``quota_day`` is computed by PostgreSQL as ``(now() AT TIME ZONE
    'America/Los_Angeles')::date`` (no tz database needed on the Windows host)."""

    __tablename__ = "youtube_quota_ledger"
    __table_args__ = (PrimaryKeyConstraint("provider", "quota_day"),)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    quota_day: Mapped[date] = mapped_column(Date, nullable=False)
    units_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    units_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
