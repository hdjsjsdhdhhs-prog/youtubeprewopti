"""End-to-end: API-style enqueue -> real Procrastinate worker -> job_runs / thumbnails / storage.

The worker uses its own connections, so data here is really committed (not the rollback fixture)
and the touched tables are truncated afterwards. Runs only against the dedicated test database.
"""

from __future__ import annotations

import factories as f
import pytest
from sqlalchemy import select, text

import app.workers.tasks  # noqa: F401  (registers tasks on queue_app)
from app.core.db import get_sessionmaker
from app.core.errors import IntegrationError, IntegrationErrorCode
from app.domains.identity.service import create_owner
from app.domains.jobs.models import JobRun
from app.domains.media.models import FetchStatus, ImageAsset, ImageMetrics, ImageSource, Thumbnail
from app.domains.media.service import enqueue_channel_thumbnails
from app.providers.thumbnails import MockThumbnailFetcher
from app.workers.queue import queue_app

TRUNCATE = (
    "TRUNCATE users, workspaces, channels, image_assets, job_runs, audit_logs, procrastinate_events, "
    "procrastinate_periodic_defers, procrastinate_jobs, procrastinate_workers RESTART IDENTITY CASCADE"
)


class ScriptedFetcher(MockThumbnailFetcher):
    """Mock fetcher that raises scripted errors per URL (consumed in order)."""

    def __init__(self, script: dict[str, list[IntegrationError]] | None = None) -> None:
        self.script = script or {}
        self.calls: list[str] = []

    async def fetch(self, url: str) -> bytes:
        self.calls.append(url)
        errors = self.script.get(url)
        if errors:
            raise errors.pop(0)
        return await super().fetch(url)


def _err(code: IntegrationErrorCode, retryable: bool) -> IntegrationError:
    return IntegrationError(code, f"scripted {code.value}", retryable=retryable, provider="test")


@pytest.fixture
async def committed(settings, monkeypatch):
    monkeypatch.setattr(settings, "worker_retry_base_seconds", 0)  # retries become due immediately
    sessions = get_sessionmaker()
    async with sessions() as db:
        user, ws = await create_owner(db, email="worker@example.com", password="correct-horse-battery-staple",
                                      display_name="W", workspace_name="Worker WS")
        ch = await f.channel(db, projects=[await f.project(db, ws.id)])
        videos = [await f.video(db, ch) for _ in range(3)]
        await db.commit()
    try:
        yield {"user": user, "ws": ws, "channel": ch, "videos": videos}
    finally:
        async with sessions() as db:
            await db.execute(text(TRUNCATE))
            await db.commit()


async def _enqueue(data) -> int:
    async with get_sessionmaker()() as db:
        enq, _ = await enqueue_channel_thumbnails(db, data["ws"].id, data["user"].id, data["channel"].id)
        await db.commit()
    return enq.job.id


async def _run_worker(**resources) -> None:
    async with queue_app.open_async():
        await queue_app.run_worker_async(
            queues=["bulk"], wait=False, concurrency=1, listen_notify=False,
            install_signal_handlers=False, additional_context=resources,
        )


async def _state(job_run_id: int):
    async with get_sessionmaker()() as db:
        run = await db.scalar(select(JobRun).where(JobRun.id == job_run_id))
        queue_status = await db.scalar(
            text("SELECT status::text FROM procrastinate_jobs WHERE id = :id"),
            {"id": run.procrastinate_job_id},
        )
        thumbs = {t.video_id: t for t in await db.scalars(select(Thumbnail))}
        assets = list(await db.scalars(select(ImageAsset)))
    return run, queue_status, thumbs, assets


async def test_worker_downloads_thumbnails(committed, storage):
    job_id = await _enqueue(committed)
    fetcher = ScriptedFetcher()
    await _run_worker(thumbnail_fetcher=fetcher, storage=storage)

    run, queue_status, thumbs, assets = await _state(job_id)
    assert (run.status, queue_status, run.attempts) == ("completed", "succeeded", 1)
    assert (run.progress_done, run.progress_total) == (3, 3)
    assert run.result["downloaded"] == 3 and run.result["failed"] == 0 and run.result["fetcher"] == "mock"
    assert run.started_at and run.finished_at and run.error_code is None
    assert all(t.fetch_status == FetchStatus.OK and t.image_asset_id for t in thumbs.values())
    assert len(assets) == 3 and {a.source for a in assets} == {ImageSource.DEMO}  # mock => labelled demo
    assert all(storage.exists(a.storage_key) for a in assets)
    assert run.result["metrics_computed"] == 3  # deterministic metrics in the same job (Phase 3)
    async with get_sessionmaker()() as db:
        assert set(await db.scalars(select(ImageMetrics.image_asset_id))) == {a.id for a in assets}

    # re-running the same work is a no-op: nothing pending => no new job
    async with get_sessionmaker()() as db:
        enq, n = await enqueue_channel_thumbnails(
            db, committed["ws"].id, committed["user"].id, committed["channel"].id)
    assert enq is None and n == 0


async def test_transient_error_is_retried_and_resumes(committed, storage):
    job_id = await _enqueue(committed)
    second = f"https://i.ytimg.com/vi/{committed['videos'][1].youtube_video_id}/hqdefault.jpg"
    fetcher = ScriptedFetcher({second: [_err(IntegrationErrorCode.PROVIDER_UNAVAILABLE, True)]})
    await _run_worker(thumbnail_fetcher=fetcher, storage=storage)

    run, queue_status, thumbs, _ = await _state(job_id)
    assert (run.status, queue_status, run.attempts) == ("completed", "succeeded", 2)
    assert run.result["downloaded"] == 2 and run.result["skipped"] == 1  # 1st thumbnail kept from attempt 1
    assert len(fetcher.calls) == 4  # v1, v2(fail) | v2, v3
    assert all(t.fetch_status == FetchStatus.OK for t in thumbs.values())


async def test_permanent_item_error_does_not_fail_the_job(committed, storage):
    job_id = await _enqueue(committed)
    bad = committed["videos"][0]
    url = f"https://i.ytimg.com/vi/{bad.youtube_video_id}/hqdefault.jpg"
    fetcher = ScriptedFetcher({url: [_err(IntegrationErrorCode.NOT_FOUND, False)]})
    await _run_worker(thumbnail_fetcher=fetcher, storage=storage)

    run, _, thumbs, _ = await _state(job_id)
    assert run.status == "completed" and run.attempts == 1
    assert run.result["failed"] == 1 and run.result["failures"] == [
        {"video_id": bad.id, "error": "scripted not_found"}]
    assert thumbs[bad.id].fetch_status == FetchStatus.FAILED and thumbs[bad.id].error == "scripted not_found"


async def test_retries_exhausted_marks_job_failed(committed, storage, settings, monkeypatch):
    monkeypatch.setattr(settings, "worker_retry_max_attempts", 2)
    job_id = await _enqueue(committed)
    first = f"https://i.ytimg.com/vi/{committed['videos'][0].youtube_video_id}/hqdefault.jpg"
    fetcher = ScriptedFetcher({first: [_err(IntegrationErrorCode.RATE_LIMITED, True) for _ in range(5)]})
    await _run_worker(thumbnail_fetcher=fetcher, storage=storage)

    run, queue_status, _, _ = await _state(job_id)
    assert (run.status, queue_status, run.attempts) == ("failed", "failed", 2)
    assert run.error_code == "rate_limited" and run.error_human == "scripted rate_limited"
    assert run.finished_at is not None and len(fetcher.calls) == 2


async def test_unexpected_error_fails_fast_without_retry(committed, storage):
    class Broken(ScriptedFetcher):
        async def fetch(self, url: str) -> bytes:
            self.calls.append(url)
            raise ZeroDivisionError("bug")

    job_id = await _enqueue(committed)
    fetcher = Broken()
    await _run_worker(thumbnail_fetcher=fetcher, storage=storage)
    run, queue_status, _, _ = await _state(job_id)
    assert (run.status, queue_status, run.attempts) == ("failed", "failed", 1)
    assert run.error_code == "internal_error"
    assert len(fetcher.calls) == 1


async def test_cancelled_job_run_is_skipped_by_worker(committed, storage):
    job_id = await _enqueue(committed)
    async with get_sessionmaker()() as db:  # cancelled in job_runs only; queue entry still 'todo'
        await db.execute(text("UPDATE job_runs SET status = 'cancelled' WHERE id = :id"), {"id": job_id})
        await db.commit()
    fetcher = ScriptedFetcher()
    await _run_worker(thumbnail_fetcher=fetcher, storage=storage)

    run, queue_status, thumbs, _ = await _state(job_id)
    assert (run.status, queue_status, run.attempts) == ("cancelled", "succeeded", 0)
    assert fetcher.calls == [] and all(t.fetch_status == FetchStatus.PENDING for t in thumbs.values())
