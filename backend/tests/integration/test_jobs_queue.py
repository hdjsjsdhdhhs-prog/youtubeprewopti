"""Enqueue/cancel/lifecycle of job runs against the real Procrastinate schema (same transaction)."""

import pytest
from sqlalchemy import text

from app.core.errors import InvalidTransitionError, NotFoundError
from app.core.state_machine import JobStatus
from app.domains.jobs import service as jobs
from app.domains.jobs.service import JobQueue, JobType


async def _queue_row(db, procrastinate_id):
    return (
        await db.execute(
            text("SELECT queue_name, task_name, priority, args, status::text AS status "
                 "FROM procrastinate_jobs WHERE id = :id"),
            {"id": procrastinate_id},
        )
    ).mappings().one()


async def _enqueue(db, ws_id, params=None, **kw):
    return await jobs.enqueue_job(
        db, type_=JobType.THUMBNAIL_DOWNLOAD, queue=kw.pop("queue", JobQueue.BULK),
        params=params or {"video_ids": [1, 2]}, workspace_id=ws_id, **kw,
    )


async def test_enqueue_writes_job_run_and_queue_entry_atomically(db, owner):
    user, ws = owner
    enq = await _enqueue(db, ws.id, created_by=user.id, priority=5, progress_total=2)
    job = enq.job
    assert enq.created and job.status == JobStatus.QUEUED and job.procrastinate_job_id is not None
    assert (job.type, job.queue, job.priority, job.progress_total) == ("thumbnail_download", "bulk", 5, 2)
    row = await _queue_row(db, job.procrastinate_job_id)
    assert dict(row) == {"queue_name": "bulk", "task_name": "thumbnail_download", "priority": 5,
                         "args": {"job_run_id": job.id}, "status": "todo"}


async def test_enqueue_rollback_leaves_nothing(db, owner):
    nested = await db.begin_nested()
    enq = await _enqueue(db, owner[1].id)
    pid = enq.job.procrastinate_job_id
    await nested.rollback()
    count = await db.scalar(text("SELECT count(*) FROM procrastinate_jobs WHERE id = :id"), {"id": pid})
    assert count == 0


async def test_enqueue_is_idempotent_while_active(db, owner, other_owner):
    first = await _enqueue(db, owner[1].id)
    again = await _enqueue(db, owner[1].id)
    assert not again.created and again.job.id == first.job.id
    assert await db.scalar(text("SELECT count(*) FROM procrastinate_jobs")) == 1

    other_ws = await _enqueue(db, other_owner[1].id)  # same params, other workspace => separate job
    different = await _enqueue(db, owner[1].id, params={"video_ids": [3]})
    assert other_ws.created and different.created

    # once finished, the same request creates a new run
    started = await jobs.begin_attempt(db, first.job.id)
    assert started is not None and started.status == JobStatus.RUNNING and started.attempts == 1
    assert await jobs.finish_success(db, first.job.id, {"downloaded": 2})
    rerun = await _enqueue(db, owner[1].id)
    assert rerun.created and rerun.job.id != first.job.id


async def test_lifecycle_retrying_then_failed(db, owner):
    job_id = (await _enqueue(db, owner[1].id)).job.id
    await jobs.begin_attempt(db, job_id)
    await jobs.set_progress(db, job_id, 1, 2)
    assert await jobs.finish_failure(db, job_id, will_retry=True, error_code="timeout", error_human="slow")
    run = await jobs.get_job_run(db, owner[1].id, job_id)
    assert (run.status, run.progress_done, run.error_code) == ("retrying", 1, "timeout")
    assert run.finished_at is None

    again = await jobs.begin_attempt(db, job_id)
    assert again.status == JobStatus.RUNNING and again.attempts == 2
    assert await jobs.finish_failure(db, job_id, will_retry=False, error_code="timeout", error_human="slow")
    run = await jobs.get_job_run(db, owner[1].id, job_id)
    assert run.status == "failed" and run.finished_at is not None
    assert await jobs.begin_attempt(db, job_id) is None  # terminal => never runs again


async def test_running_again_after_worker_loss_is_allowed(db, owner):
    job_id = (await _enqueue(db, owner[1].id)).job.id
    await jobs.begin_attempt(db, job_id)
    again = await jobs.begin_attempt(db, job_id)  # re-delivered while still RUNNING
    assert again is not None and again.attempts == 2


async def test_mark_requeued_only_touches_running(db, owner):
    running = (await _enqueue(db, owner[1].id, params={"a": 1})).job
    queued = (await _enqueue(db, owner[1].id, params={"a": 2})).job
    await jobs.begin_attempt(db, running.id)
    n = await jobs.mark_requeued(db, [running.procrastinate_job_id, queued.procrastinate_job_id])
    assert n == 1
    assert (await jobs.get_job_run(db, owner[1].id, running.id)).error_code == "worker_lost"
    assert (await jobs.get_job_run(db, owner[1].id, queued.id)).status == "queued"
    assert await jobs.mark_requeued(db, []) == 0


async def test_cancel_queued_job_cancels_queue_entry(db, owner, other_owner):
    user, ws = owner
    job = (await _enqueue(db, ws.id)).job
    with pytest.raises(NotFoundError):
        await jobs.cancel_job_run(db, other_owner[1].id, other_owner[0].id, job.id)

    out = await jobs.cancel_job_run(db, ws.id, user.id, job.id)
    assert out.status == "cancelled" and out.finished_at is not None
    assert (await _queue_row(db, job.procrastinate_job_id))["status"] == "cancelled"
    assert await jobs.begin_attempt(db, job.id) is None
    with pytest.raises(InvalidTransitionError):
        await jobs.cancel_job_run(db, ws.id, user.id, job.id)
    action = await db.scalar(
        text("SELECT action FROM audit_logs WHERE entity_type = 'job_run' AND entity_id = :id"),
        {"id": str(job.id)},
    )
    assert action == "job.cancelled"


async def test_cancel_running_job_requests_abort(db, owner):
    user, ws = owner
    job = (await _enqueue(db, ws.id)).job
    await db.execute(text("UPDATE procrastinate_jobs SET status = 'doing' WHERE id = :id"),
                     {"id": job.procrastinate_job_id})
    await jobs.begin_attempt(db, job.id)
    await jobs.cancel_job_run(db, ws.id, user.id, job.id)
    abort = await db.scalar(text("SELECT abort_requested FROM procrastinate_jobs WHERE id = :id"),
                            {"id": job.procrastinate_job_id})
    assert abort is True
    assert not await jobs.finish_success(db, job.id, {})  # late success does not resurrect it
    assert (await jobs.get_job_run(db, ws.id, job.id)).status == "cancelled"
