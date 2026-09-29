"""Thin task layer (ADR-0003): binds Procrastinate delivery to the ``job_runs`` lifecycle.

A handler receives a :class:`JobRunContext` and returns a JSON-able result dict. The wrapper:

1. moves the run to RUNNING (skips it if it was cancelled / already finished);
2. on success stores the result and marks it COMPLETED;
3. on error records a human-readable error and marks it RETRYING or FAILED, then re-raises so
   Procrastinate applies the same retry decision.

Only transient errors are retried (:func:`is_retryable`); bugs and permanent provider errors fail fast.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import procrastinate
from procrastinate.jobs import Job
from procrastinate.retry import BaseRetryStrategy, RetryDecision
from sqlalchemy.exc import OperationalError

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.errors import AppError, IntegrationError, RetryableJobError
from app.core.logging import get_logger
from app.domains.jobs import service as jobs
from app.domains.jobs.service import JobQueue
from app.workers.queue import queue_app

log = get_logger("app.workers")


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, IntegrationError):
        return exc.retryable
    return isinstance(exc, RetryableJobError | OperationalError | TimeoutError | ConnectionError)


class JobRetryStrategy(BaseRetryStrategy):
    """Exponential backoff for transient errors; limits come from settings at decision time."""

    def __init__(self, *, max_attempts: int | None = None) -> None:
        self.max_attempts = max_attempts

    def get_retry_decision(self, *, exception: BaseException, job: Job) -> RetryDecision | None:
        s = get_settings()
        max_attempts = self.max_attempts or s.worker_retry_max_attempts
        # job.attempts = number of previous runs (0 on the first one)
        if not is_retryable(exception) or job.attempts + 1 >= max_attempts:
            return None
        wait = min(s.worker_retry_base_seconds * 2**job.attempts, s.worker_retry_max_backoff_seconds)
        return RetryDecision(retry_in={"seconds": wait})


def describe_error(exc: BaseException) -> tuple[str, str]:
    """(error_code, human message) stored on the job run and shown in the UI."""
    if isinstance(exc, IntegrationError):
        return exc.code.value, exc.human_message
    if isinstance(exc, AppError):
        return exc.code, exc.message
    if isinstance(exc, RetryableJobError):
        return "transient_error", str(exc) or "Temporary failure; will retry."
    if isinstance(exc, OperationalError | TimeoutError | ConnectionError):
        return "transient_error", f"Temporary infrastructure error ({type(exc).__name__}); will retry."
    return "internal_error", f"Unexpected error in the job ({type(exc).__name__}); see worker logs."


@dataclass
class JobRunContext:
    job_run_id: int
    workspace_id: int | None
    params: dict[str, Any]
    attempt: int
    resources: dict[str, Any] = field(default_factory=dict)  # worker ``additional_context`` (test overrides)

    async def progress(self, done: int, total: int | None = None) -> None:
        async with get_sessionmaker()() as db:
            await jobs.set_progress(db, self.job_run_id, done, total)
            await db.commit()


Handler = Callable[[JobRunContext], Awaitable[dict[str, Any] | None]]


async def run_job(
    handler: Handler, strategy: BaseRetryStrategy, context: procrastinate.JobContext, job_run_id: int
) -> None:
    job = context.job
    bound = log.bind(job_run_id=job_run_id, procrastinate_job_id=job.id, task=job.task_name,
                     attempt=job.attempts + 1)
    sessions = get_sessionmaker()

    async with sessions() as db:
        run = await jobs.begin_attempt(db, job_run_id)
        await db.commit()
    if run is None:
        bound.info("job_run.skipped", reason="missing, cancelled or already finished")
        return

    ctx = JobRunContext(
        job_run_id=job_run_id, workspace_id=run.workspace_id, params=dict(run.params), attempt=run.attempts,
        resources=dict(context.additional_context or {}),
    )
    bound.info("job_run.started")
    try:
        result = await handler(ctx)
    except Exception as exc:
        will_retry = strategy.get_retry_decision(exception=exc, job=job) is not None
        code, human = describe_error(exc)
        async with sessions() as db:
            await jobs.finish_failure(
                db, job_run_id, will_retry=will_retry, error_code=code, error_human=human
            )
            await db.commit()
        bound.warning("job_run.failed", will_retry=will_retry, error_code=code, exc_info=True)
        raise

    async with sessions() as db:
        stored = await jobs.finish_success(db, job_run_id, result or {})
        await db.commit()
    bound.info("job_run.completed" if stored else "job_run.finished_after_cancel")


def job_task(*, name: str, queue: JobQueue, max_attempts: int | None = None) -> Callable[[Handler], Handler]:
    """Register ``handler`` as a Procrastinate task named ``name`` (== ``job_runs.type``)."""
    strategy = JobRetryStrategy(max_attempts=max_attempts)

    def decorator(handler: Handler) -> Handler:
        @queue_app.task(name=name, queue=queue.value, retry=strategy, pass_context=True)
        async def _task(context: procrastinate.JobContext, job_run_id: int) -> None:
            await run_job(handler, strategy, context, job_run_id)

        return handler

    return decorator
