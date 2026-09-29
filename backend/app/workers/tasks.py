"""Task handlers. Keep them thin: business logic lives in ``app/domains/*/service.py``."""

from __future__ import annotations

from typing import Any

import procrastinate

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.logging import get_logger
from app.domains.jobs import service as jobs
from app.domains.jobs.service import JobQueue, JobType
from app.domains.media.service import DownloadOutcome, download_thumbnail
from app.providers.storage import LocalFSStorage, StorageBackend
from app.providers.thumbnails import ThumbnailFetcher, open_thumbnail_fetcher
from app.workers.queue import queue_app
from app.workers.registry import JobRunContext, job_task

log = get_logger("app.workers")
MAX_REPORTED_FAILURES = 50


@job_task(name=JobType.THUMBNAIL_DOWNLOAD.value, queue=JobQueue.BULK)
async def thumbnail_download(ctx: JobRunContext) -> dict[str, Any]:
    """params: ``{"video_ids": [...]}``. Each thumbnail is committed on its own, so a retried job
    resumes where it stopped (already stored thumbnails are skipped)."""
    settings = get_settings()
    storage: StorageBackend = ctx.resources.get("storage") or LocalFSStorage(settings.storage_path)
    video_ids: list[int] = [int(v) for v in ctx.params.get("video_ids", [])]
    counts = {o.value: 0 for o in DownloadOutcome}
    failures: list[dict[str, Any]] = []

    async def run(fetcher: ThumbnailFetcher) -> None:
        sessions = get_sessionmaker()
        for done, video_id in enumerate(video_ids, start=1):
            async with sessions() as db:
                outcome, error = await download_thumbnail(db, storage, fetcher, video_id)
                await db.commit()
            counts[outcome.value] += 1
            if error and len(failures) < MAX_REPORTED_FAILURES:
                failures.append({"video_id": video_id, "error": error})
            await ctx.progress(done, len(video_ids))

    override: ThumbnailFetcher | None = ctx.resources.get("thumbnail_fetcher")
    if override is not None:
        await run(override)
        fetcher_name = override.name
    else:
        async with open_thumbnail_fetcher(settings) as fetcher:
            await run(fetcher)
            fetcher_name = fetcher.name
    return {**counts, "total": len(video_ids), "fetcher": fetcher_name, "failures": failures}


@queue_app.periodic(cron="* * * * *")
@queue_app.task(
    name="retry_stalled_jobs", queue=JobQueue.MONITORING.value, queueing_lock="retry_stalled_jobs",
    pass_context=True,
)
async def retry_stalled_jobs(context: procrastinate.JobContext, timestamp: int) -> None:
    """Re-queue jobs whose worker stopped heartbeating (crash, kill, Ctrl+C on Windows)."""
    manager = context.app.job_manager
    stalled = list(
        await manager.get_stalled_jobs(seconds_since_heartbeat=get_settings().worker_stalled_after_seconds)
    )
    for job in stalled:
        await manager.retry_job(job)
    if stalled:
        async with get_sessionmaker()() as db:
            updated = await jobs.mark_requeued(db, [j.id for j in stalled if j.id is not None])
            await db.commit()
        log.warning("jobs.stalled_requeued", count=len(stalled), job_runs=updated)
