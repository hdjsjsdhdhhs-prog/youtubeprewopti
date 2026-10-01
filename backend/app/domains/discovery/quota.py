"""YouTube quota ledger (ADR-0008): units are reserved *before* each API call.

Google counts failed requests too, so reservations are never refunded. The worker reserves through
:class:`CommittingQuota` (own short transaction per call), so units already spent stay recorded even if
the surrounding discovery work is rolled back or the job fails.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import IntegrationError, IntegrationErrorCode
from app.domains.discovery.models import YouTubeQuotaLedger

# Google resets the YouTube quota at midnight Pacific Time.
QUOTA_DAY_SQL = text("(now() AT TIME ZONE 'America/Los_Angeles')::date")


@dataclass(frozen=True)
class QuotaUsage:
    provider: str
    quota_day: date
    used: int
    limit: int

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)


def quota_exhausted(provider: str, units: int, usage: QuotaUsage) -> IntegrationError:
    return IntegrationError(
        IntegrationErrorCode.QUOTA_EXCEEDED,
        f"YouTube quota for {usage.quota_day.isoformat()} (Pacific Time) is used up: {usage.used} of "
        f"{usage.limit} units, the next call needs {units}. Pending queries stay in the project; start the "
        "search again after the reset (midnight Pacific Time).",
        retryable=False, provider=provider,
    )


async def get_usage(db: AsyncSession, provider: str, limit: int) -> QuotaUsage:
    day = await db.scalar(select(QUOTA_DAY_SQL))
    used = await db.scalar(
        select(YouTubeQuotaLedger.units_used).where(
            YouTubeQuotaLedger.provider == provider, YouTubeQuotaLedger.quota_day == day
        )
    )
    assert day is not None
    return QuotaUsage(provider=provider, quota_day=day, used=used or 0, limit=limit)


async def reserve(db: AsyncSession, provider: str, units: int, limit: int) -> QuotaUsage:
    """Atomically add ``units`` to today's counter unless that would exceed ``limit``.

    Raises a non-retryable ``QUOTA_EXCEEDED`` IntegrationError otherwise. Caller commits.
    """
    if units > limit:
        raise quota_exhausted(provider, units, await get_usage(db, provider, limit))
    stmt = insert(YouTubeQuotaLedger).values(
        provider=provider, quota_day=QUOTA_DAY_SQL, units_used=units, units_limit=limit
    )
    upsert = stmt.on_conflict_do_update(
        index_elements=["provider", "quota_day"],
        set_={
            "units_used": YouTubeQuotaLedger.units_used + stmt.excluded.units_used,
            "units_limit": stmt.excluded.units_limit,
        },
        where=(YouTubeQuotaLedger.units_used + stmt.excluded.units_used) <= stmt.excluded.units_limit,
    ).returning(YouTubeQuotaLedger.quota_day, YouTubeQuotaLedger.units_used)
    row = (await db.execute(upsert)).first()
    if row is None:
        raise quota_exhausted(provider, units, await get_usage(db, provider, limit))
    return QuotaUsage(provider=provider, quota_day=row.quota_day, used=row.units_used, limit=limit)


class QuotaGuard(Protocol):
    spent: int

    async def reserve(self, units: int) -> None: ...


class SessionQuota:
    """Reserves inside a given session (flush only) — for code paths/tests that own the transaction."""

    def __init__(self, db: AsyncSession, provider: str, limit: int) -> None:
        self._db, self._provider, self._limit = db, provider, limit
        self.spent = 0

    async def reserve(self, units: int) -> None:
        await reserve(self._db, self._provider, units, self._limit)
        self.spent += units


class CommittingQuota:
    """Reserves in a separate, immediately committed transaction (worker)."""

    def __init__(self, sessions: Callable[[], AsyncSession], provider: str, limit: int) -> None:
        self._sessions, self._provider, self._limit = sessions, provider, limit
        self.spent = 0

    async def reserve(self, units: int) -> None:
        async with self._sessions() as db:
            await reserve(db, self._provider, units, self._limit)
            await db.commit()
        self.spent += units
