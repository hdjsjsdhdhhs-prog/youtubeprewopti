"""Job runs: product-level lifecycle on top of the Procrastinate queue (ADR-0003, ADR-0011).

Enqueueing writes the ``job_runs`` row *and* the ``procrastinate_jobs`` row through the caller's
connection, so both commit (or roll back) together with the business change that triggered them.
The worker only moves a job run through ``JOB_MACHINE`` via the ``begin_attempt`` / ``finish_*``
functions below.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, cast

from sqlalchemy import CursorResult, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.core.state_machine import ACTIVE_JOB_STATUSES, JOB_MACHINE, JobStatus
from app.domains.identity.service import audit
from app.domains.jobs.models import ACTIVE_STATUS_SQL, JobRun
from app.domains.jobs.schemas import JobRunDetail, JobRunOut


class JobQueue(StrEnum):
    INTERACTIVE = "interactive"
    BULK = "bulk"
    MONITORING = "monitoring"
    OUTREACH = "outreach"


class JobType(StrEnum):
    """Job type == Procrastinate task name registered by the worker (``app/workers/tasks.py``)."""

    THUMBNAIL_DOWNLOAD = "thumbnail_download"


# Composite literal matches procrastinate_job_to_defer_v1
# (queue_name, task_name, priority, lock, queueing_lock, args, scheduled_at).
_DEFER_SQL = text(
    "SELECT unnest(procrastinate_defer_jobs_v1(ARRAY[ROW("
    "CAST(:queue AS varchar), CAST(:task AS varchar), CAST(:priority AS integer), NULL, NULL, "
    "CAST(:args AS jsonb), NULL)::procrastinate_job_to_defer_v1]))"
)
_CANCEL_SQL = text("SELECT procrastinate_cancel_job_v1(:id, true, false)")


def job_fingerprint(type_: str, workspace_id: int | None, params: dict[str, Any]) -> str:
    """Identity of a job for deduplication. Callers pass already-normalized params (e.g. sorted ids)."""
    canonical = json.dumps(
        {"type": type_, "workspace_id": workspace_id, "params": params},
        sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Enqueued:
    job: JobRun
    created: bool  # False => an identical active job already existed and was returned


async def enqueue_job(
    db: AsyncSession,
    *,
    type_: JobType,
    queue: JobQueue,
    params: dict[str, Any],
    workspace_id: int | None,
    created_by: int | None = None,
    priority: int = 0,
    progress_total: int = 0,
) -> Enqueued:
    """Create a job run and its queue entry in the caller's transaction (idempotent by fingerprint)."""
    fp = job_fingerprint(type_.value, workspace_id, params)
    job_id = await db.scalar(
        insert(JobRun)
        .values(
            workspace_id=workspace_id, type=type_.value, fingerprint=fp, status=JobStatus.QUEUED,
            queue=queue.value, priority=priority, params=params, progress_total=progress_total,
            created_by=created_by,
        )
        .on_conflict_do_nothing(index_elements=["fingerprint"], index_where=text(ACTIVE_STATUS_SQL))
        .returning(JobRun.id)
    )
    if job_id is None:
        existing = await db.scalar(
            select(JobRun).where(JobRun.fingerprint == fp, JobRun.status.in_(ACTIVE_JOB_STATUSES))
        )
        if existing is None:  # the active duplicate finished between our INSERT and SELECT
            raise ConflictError("An identical job has just finished, retry the request", code="job_race")
        return Enqueued(job=existing, created=False)

    procrastinate_id = await db.scalar(
        _DEFER_SQL,
        {"queue": queue.value, "task": type_.value, "priority": priority,
         "args": json.dumps({"job_run_id": job_id})},
    )
    await db.execute(update(JobRun).where(JobRun.id == job_id).values(procrastinate_job_id=procrastinate_id))
    job = await db.scalar(select(JobRun).where(JobRun.id == job_id).execution_options(populate_existing=True))
    assert job is not None
    return Enqueued(job=job, created=True)


async def cancel_job_run(db: AsyncSession, workspace_id: int, actor_id: int, job_id: int) -> JobRunOut:
    job = await db.scalar(
        select(JobRun).where(JobRun.id == job_id, JobRun.workspace_id == workspace_id).with_for_update()
    )
    if job is None:
        raise NotFoundError("Job not found")
    JOB_MACHINE.assert_can(job.status, JobStatus.CANCELLED)
    previous = job.status
    job.status = JobStatus.CANCELLED
    job.finished_at = datetime.now(UTC)
    if job.procrastinate_job_id is not None:
        # todo -> cancelled; doing -> abort requested (the worker cancels the running coroutine)
        await db.execute(_CANCEL_SQL, {"id": job.procrastinate_job_id})
    await db.flush()
    await audit(
        db, "job.cancelled", workspace_id=workspace_id, actor_user_id=actor_id,
        entity_type="job_run", entity_id=job.id, diff={"from": previous.value, "type": job.type},
    )
    return JobRunOut.model_validate(job)


# ---------------------------------------------------------------------------
# Worker-side lifecycle. Each call runs in its own short transaction (caller commits).
# ---------------------------------------------------------------------------
async def _locked(db: AsyncSession, job_run_id: int) -> JobRun | None:
    stmt = select(JobRun).where(JobRun.id == job_run_id).with_for_update()
    return await db.scalar(stmt.execution_options(populate_existing=True))


async def begin_attempt(db: AsyncSession, job_run_id: int) -> JobRun | None:
    """Mark the run RUNNING. Returns None if it must not run (missing, cancelled or already finished)."""
    job = await _locked(db, job_run_id)
    if job is None or JOB_MACHINE.is_terminal(job.status):
        return None
    if job.status != JobStatus.RUNNING:  # RUNNING again = the previous worker died mid-run
        JOB_MACHINE.assert_can(job.status, JobStatus.RUNNING)
        job.status = JobStatus.RUNNING
    job.attempts += 1
    job.started_at = job.started_at or datetime.now(UTC)
    await db.flush()
    return job


async def set_progress(db: AsyncSession, job_run_id: int, done: int, total: int | None = None) -> None:
    values: dict[str, Any] = {"progress_done": done}
    if total is not None:
        values["progress_total"] = total
    await db.execute(
        update(JobRun).where(JobRun.id == job_run_id, JobRun.status == JobStatus.RUNNING).values(**values)
    )


async def finish_success(db: AsyncSession, job_run_id: int, result: dict[str, Any]) -> bool:
    """RUNNING -> COMPLETED. Returns False if the run was cancelled meanwhile (left untouched)."""
    job = await _locked(db, job_run_id)
    if job is None or job.status != JobStatus.RUNNING:
        return False
    job.status = JobStatus.COMPLETED
    job.result = result
    job.error_code = None
    job.error_human = None
    job.finished_at = datetime.now(UTC)
    await db.flush()
    return True


async def finish_failure(
    db: AsyncSession, job_run_id: int, *, will_retry: bool, error_code: str, error_human: str
) -> bool:
    """RUNNING -> RETRYING (another attempt is scheduled) or FAILED (final)."""
    job = await _locked(db, job_run_id)
    if job is None or job.status != JobStatus.RUNNING:
        return False
    target = JobStatus.RETRYING if will_retry else JobStatus.FAILED
    JOB_MACHINE.assert_can(job.status, target)
    job.status = target
    job.error_code = error_code[:60]
    job.error_human = error_human
    if not will_retry:
        job.finished_at = datetime.now(UTC)
    await db.flush()
    return True


async def mark_requeued(db: AsyncSession, procrastinate_job_ids: Iterable[int]) -> int:
    """Stalled queue jobs were re-queued: their RUNNING job runs become RETRYING."""
    ids = list(procrastinate_job_ids)
    if not ids:
        return 0
    stmt = (
        update(JobRun)
        .where(JobRun.procrastinate_job_id.in_(ids), JobRun.status == JobStatus.RUNNING)
        .values(status=JobStatus.RETRYING, error_code="worker_lost",
                error_human="The worker processing this job stopped responding; the job was re-queued.")
    )
    result = cast(CursorResult[Any], await db.execute(stmt))  # DML without RETURNING => CursorResult
    return result.rowcount or 0


async def list_job_runs(
    db: AsyncSession,
    workspace_id: int,
    *,
    status: JobStatus | None,
    type_: str | None,
    limit: int,
    offset: int,
) -> tuple[list[JobRunOut], int]:
    where = [JobRun.workspace_id == workspace_id]
    if status is not None:
        where.append(JobRun.status == status)
    if type_:
        where.append(JobRun.type == type_)
    total = await db.scalar(select(func.count()).select_from(JobRun).where(*where)) or 0
    rows = await db.scalars(
        select(JobRun)
        .where(*where)
        .order_by(JobRun.created_at.desc(), JobRun.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return [JobRunOut.model_validate(j) for j in rows], total


async def get_job_run(db: AsyncSession, workspace_id: int, job_id: int) -> JobRunDetail:
    job = await db.scalar(select(JobRun).where(JobRun.id == job_id, JobRun.workspace_id == workspace_id))
    if job is None:
        raise NotFoundError("Job not found")
    return JobRunDetail.model_validate(job)
