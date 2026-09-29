"""Live check of the real ``http`` thumbnail fetcher against i.ytimg.com through a real worker.

Opt-in (needs internet): ``YTL_LIVE_NETWORK=1 pytest tests/integration/test_thumbnails_live.py``.
Skipped by default so the regular suite stays offline and deterministic.
"""

from __future__ import annotations

import os

import factories as f
import pytest
from sqlalchemy import select, text

import app.workers.tasks  # noqa: F401  (registers tasks on queue_app)
from app.core.db import get_sessionmaker
from app.domains.identity.service import create_owner
from app.domains.jobs.models import JobRun
from app.domains.media.models import FetchStatus, ImageAsset, ImageSource, Thumbnail
from app.domains.media.service import enqueue_channel_thumbnails
from app.workers.queue import queue_app

pytestmark = pytest.mark.skipif(
    os.environ.get("YTL_LIVE_NETWORK") != "1", reason="live network test; set YTL_LIVE_NETWORK=1"
)

TRUNCATE = (
    "TRUNCATE users, workspaces, channels, image_assets, job_runs, audit_logs, procrastinate_events, "
    "procrastinate_periodic_defers, procrastinate_jobs, procrastinate_workers RESTART IDENTITY CASCADE"
)
# Long-lived public videos. "Me at the zoo" (2005) has no maxres thumbnail -> real 404.
OK_1 = "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"
OK_2 = "https://i.ytimg.com/vi/jNQXAC9IVRw/hqdefault.jpg"
MISSING = "https://i.ytimg.com/vi/jNQXAC9IVRw/maxresdefault.jpg"


async def test_real_thumbnails_are_downloaded_deduplicated_and_404_recorded(settings, storage, monkeypatch):
    monkeypatch.setattr(settings, "thumbnail_fetcher", "http")
    sessions = get_sessionmaker()
    async with sessions() as db:
        user, ws = await create_owner(db, email="live@example.com", password="live-test-password-1",
                                      display_name="L", workspace_name="Live WS")
        ch = await f.channel(db, projects=[await f.project(db, ws.id)])
        urls = [OK_1, OK_2, MISSING, OK_1]  # last one: same bytes as the first -> one asset
        videos = [await f.video(db, ch) for _ in urls]
        for v, url in zip(videos, urls, strict=True):
            await db.execute(
                text("UPDATE thumbnails SET original_url = :u WHERE video_id = :v"), {"u": url, "v": v.id}
            )
        await db.commit()
    try:
        async with sessions() as db:
            enq, n = await enqueue_channel_thumbnails(db, ws.id, user.id, ch.id)
            await db.commit()
        assert n == 4

        async with queue_app.open_async():
            await queue_app.run_worker_async(
                queues=["bulk"], wait=False, concurrency=1, listen_notify=False,
                install_signal_handlers=False, additional_context={"storage": storage},
            )

        async with sessions() as db:
            run = await db.scalar(select(JobRun).where(JobRun.id == enq.job.id))
            thumbs = {t.video_id: t for t in await db.scalars(select(Thumbnail))}
            assets = {a.id: a for a in await db.scalars(select(ImageAsset))}

        assert (run.status, run.attempts, run.progress_done) == ("completed", 1, 4), run.error_human
        assert run.result["fetcher"] == "http"
        assert (run.result["downloaded"], run.result["failed"]) == (3, 1)

        ok = [thumbs[v.id] for i, v in enumerate(videos) if urls[i] != MISSING]
        assert all(t.fetch_status == FetchStatus.OK and t.image_asset_id for t in ok)
        assert ok[0].image_asset_id == ok[2].image_asset_id  # SHA-256 dedup of identical bytes
        assert len(assets) == 2
        for a in assets.values():
            assert a.source == ImageSource.YOUTUBE_THUMBNAIL and a.mime == "image/jpeg"
            assert (a.width, a.height) == (480, 360) and a.phash
            assert storage.exists(a.storage_key)

        missing = thumbs[videos[2].id]
        assert missing.fetch_status == FetchStatus.FAILED and missing.image_asset_id is None
        assert missing.error and run.result["failures"][0]["video_id"] == videos[2].id
    finally:
        async with sessions() as db:
            await db.execute(text(TRUNCATE))
            await db.commit()
