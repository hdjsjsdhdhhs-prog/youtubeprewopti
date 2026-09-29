from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import DbSession, Paging, ReadAuth, WriteAuth
from app.api.schemas import Page
from app.core.state_machine import JobStatus
from app.domains.jobs import service
from app.domains.jobs.schemas import JobRunDetail, JobRunOut

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=Page[JobRunOut])
async def list_jobs(
    ctx: ReadAuth,
    db: DbSession,
    page: Paging,
    status: JobStatus | None = None,
    type: Annotated[str | None, Query(max_length=60)] = None,  # noqa: A002
) -> Page[JobRunOut]:
    items, total = await service.list_job_runs(
        db, ctx.workspace.id, status=status, type_=type, limit=page.limit, offset=page.offset
    )
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/{job_id}", response_model=JobRunDetail)
async def get_job(job_id: int, ctx: ReadAuth, db: DbSession) -> JobRunDetail:
    return await service.get_job_run(db, ctx.workspace.id, job_id)


@router.post("/{job_id}/cancel", response_model=JobRunOut)
async def cancel_job(job_id: int, ctx: WriteAuth, db: DbSession) -> JobRunOut:
    """Cancel a queued/retrying job; a running one is asked to abort. Finished jobs => 409."""
    return await service.cancel_job_run(db, ctx.workspace.id, ctx.user.id, job_id)
