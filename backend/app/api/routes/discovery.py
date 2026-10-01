from __future__ import annotations

from fastapi import APIRouter, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AppSettings, DbSession, ReadAuth, WriteAuth
from app.core.config import Settings
from app.domains.discovery import service
from app.domains.discovery.schemas import DiscoveryStart, DiscoveryStartResult, QuotaOut
from app.domains.jobs.schemas import JobRunOut

router = APIRouter(tags=["discovery"])


async def _quota(db: AsyncSession, settings: Settings) -> QuotaOut:
    name, usage = await service.quota_status(db, settings)
    if usage is None:
        return QuotaOut(provider=None, mode=None, quota_day=None, used=0, limit=settings.youtube_daily_quota,
                        remaining=0)
    return QuotaOut(
        provider=name, mode=settings.effective_youtube_provider, quota_day=usage.quota_day, used=usage.used,
        limit=usage.limit, remaining=usage.remaining,
    )


@router.get("/youtube/quota", response_model=QuotaOut)
async def youtube_quota(ctx: ReadAuth, db: DbSession, settings: AppSettings) -> QuotaOut:
    """Today's YouTube Data API quota usage as recorded by the ledger (resets at midnight Pacific Time)."""
    return await _quota(db, settings)


@router.post(
    "/projects/{project_id}/discovery",
    response_model=DiscoveryStartResult,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_discovery(
    project_id: int, body: DiscoveryStart, ctx: WriteAuth, db: DbSession, settings: AppSettings
) -> DiscoveryStartResult:
    """Queue a ``discovery`` job for the project's queries. The response carries a quota estimate; a
    run that does not fit today's remaining quota stops with ``quota_exceeded`` and can be resumed
    after the reset (finished queries are not repeated)."""
    plan = await service.start_discovery(
        db, settings, ctx.workspace.id, ctx.user.id, project_id,
        query_ids=body.query_ids, include_done=body.include_done,
    )
    return DiscoveryStartResult(
        job=JobRunOut.model_validate(plan.enqueued.job) if plan.enqueued else None,
        created=bool(plan.enqueued and plan.enqueued.created),
        queries=len(plan.query_ids),
        quota_search_units=plan.estimate.search_units,
        quota_max_units=plan.estimate.max_total_units,
        quota=await _quota(db, settings),
    )
