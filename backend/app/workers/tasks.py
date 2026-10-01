"""Task handlers. Keep them thin: business logic lives in ``app/domains/*/service.py``."""

from __future__ import annotations

from typing import Any

import procrastinate

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.logging import get_logger
from app.domains.discovery.quota import CommittingQuota
from app.domains.discovery.service import run_discovery
from app.domains.jobs import service as jobs
from app.domains.jobs.service import JobQueue, JobType
from app.domains.media.service import DownloadOutcome, MetricsOutcome, ingest_thumbnail
from app.providers.storage import LocalFSStorage, StorageBackend
from app.providers.thumbnails import ThumbnailFetcher, open_thumbnail_fetcher
from app.providers.youtube import YouTubeProvider, open_youtube_provider
from app.workers.queue import queue_app
from app.workers.registry import JobRunContext, job_task

log = get_logger("app.workers")
MAX_REPORTED_FAILURES = 50


@job_task(name=JobType.THUMBNAIL_DOWNLOAD.value, queue=JobQueue.BULK)
async def thumbnail_download(ctx: JobRunContext) -> dict[str, Any]:
    """params: ``{"video_ids": [...], "project_id" | "channel_id": …}``. Downloads each thumbnail (if not
    stored yet) and computes its deterministic metrics. Each video is committed on its own, so a retried
    job resumes where it stopped (stored thumbnails / current metrics are skipped)."""
    settings = get_settings()
    storage: StorageBackend = ctx.resources.get("storage") or LocalFSStorage(settings.storage_path)
    video_ids: list[int] = [int(v) for v in ctx.params.get("video_ids", [])]
    counts = {o.value: 0 for o in DownloadOutcome}
    metrics = {f"metrics_{o.value}": 0 for o in MetricsOutcome}
    failures: list[dict[str, Any]] = []

    async def run(fetcher: ThumbnailFetcher) -> None:
        sessions = get_sessionmaker()
        for done, video_id in enumerate(video_ids, start=1):
            async with sessions() as db:
                res = await ingest_thumbnail(db, storage, fetcher, video_id)
                await db.commit()
            counts[res.download.value] += 1
            metrics[f"metrics_{res.metrics.value}"] += 1
            if res.error and len(failures) < MAX_REPORTED_FAILURES:
                failures.append({"video_id": video_id, "error": res.error})
            await ctx.progress(done, len(video_ids))

    override: ThumbnailFetcher | None = ctx.resources.get("thumbnail_fetcher")
    if override is not None:
        await run(override)
        fetcher_name = override.name
    else:
        async with open_thumbnail_fetcher(settings) as fetcher:
            await run(fetcher)
            fetcher_name = fetcher.name
    return {**counts, **metrics, "total": len(video_ids), "fetcher": fetcher_name, "failures": failures}


@job_task(name=JobType.DISCOVERY.value, queue=JobQueue.BULK)
async def discovery(ctx: JobRunContext) -> dict[str, Any]:
    """params: ``{"project_id": …, "query_ids": [...]}`` — see ``discovery.service.run_discovery``."""
    settings = get_settings()
    sessions = get_sessionmaker()

    async def run(provider: YouTubeProvider) -> dict[str, Any]:
        quota = CommittingQuota(sessions, provider.name, settings.youtube_daily_quota)
        return await run_discovery(
            sessions, provider, quota, settings, job_run_id=ctx.job_run_id, workspace_id=ctx.workspace_id,
            params=ctx.params, progress=ctx.progress,
        )

    override: YouTubeProvider | None = ctx.resources.get("youtube_provider")
    if override is not None:
        return await run(override)
    async with open_youtube_provider(settings) as provider:
        return await run(provider)


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
