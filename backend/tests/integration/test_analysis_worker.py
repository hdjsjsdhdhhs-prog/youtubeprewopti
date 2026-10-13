"""End-to-end: confirmed start through the service -> real Procrastinate worker -> thumbnail_analyses.

Data is really committed (the worker uses its own connections); touched tables are truncated afterwards.
"""

from __future__ import annotations

import factories as f
import pytest
from sqlalchemy import select, text

import app.workers.tasks  # noqa: F401  (registers tasks on queue_app)
from app.core.db import get_sessionmaker
from app.domains.ai.models import AICall
from app.domains.analysis.models import ThumbnailAnalysis
from app.domains.analysis.schemas import AnalysisEstimateRequest, AnalysisStartRequest
from app.domains.analysis.service import estimate_thumbnail_analysis, start_thumbnail_analysis
from app.domains.identity.service import create_owner
from app.domains.jobs.models import JobRun
from app.domains.media.models import ImageSource
from app.domains.media.service import ensure_image_metrics, store_image
from app.providers.ai import MockAIProvider
from app.workers.queue import queue_app

TRUNCATE = (
    "TRUNCATE users, workspaces, channels, image_assets, job_runs, audit_logs, ai_calls, prompt_templates, "
    "thumbnail_analyses, procrastinate_events, procrastinate_periodic_defers, procrastinate_jobs, "
    "procrastinate_workers RESTART IDENTITY CASCADE"
)


@pytest.fixture
async def committed(settings, storage, monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "mock")
    sessions = get_sessionmaker()
    async with sessions() as db:
        user, ws = await create_owner(db, email="audit@example.com", password="correct-horse-battery-staple",
                                      display_name="A", workspace_name="Audit WS")
        p = await f.project(db, ws.id)
        ch = await f.channel(db, projects=[p])
        for seed in (11, 12, 13):
            a = await store_image(db, storage, f.noise_png(seed), ImageSource.YOUTUBE_THUMBNAIL)
            await ensure_image_metrics(db, storage, a)
            await f.video(db, ch, asset=a)
        await db.commit()
    try:
        yield {"user": user, "ws": ws, "project": p}
    finally:
        async with sessions() as db:
            await db.execute(text(TRUNCATE))
            await db.commit()


async def test_worker_runs_the_confirmed_audit(committed, settings, storage):
    ws, user, p = committed["ws"], committed["user"], committed["project"]
    async with get_sessionmaker()() as db:
        e = await estimate_thumbnail_analysis(db, settings, ws.id, p.id, AnalysisEstimateRequest())
        started = await start_thumbnail_analysis(
            db, settings, ws.id, user.id, p.id, AnalysisStartRequest(confirm_token=e.confirm_token)
        )
        await db.commit()
    assert e.items == 3 and started.job is not None

    async with queue_app.open_async():
        await queue_app.run_worker_async(
            queues=["bulk"], wait=False, concurrency=1, listen_notify=False, install_signal_handlers=False,
            additional_context={"storage": storage, "ai_provider": MockAIProvider()},
        )

    async with get_sessionmaker()() as db:
        run = await db.get(JobRun, started.job.id)
        assert run.status == "completed", (run.error_code, run.error_human)
        assert (run.result["analyzed"], run.result["failed"], run.result["ai_calls"]) == (3, 0, 3)
        assert (run.progress_done, run.progress_total) == (3, 3)
        rows = list(await db.scalars(select(ThumbnailAnalysis)))
        assert len(rows) == 3 and all(r.is_current and r.provider == "mock" for r in rows)
        calls = list(await db.scalars(select(AICall).where(AICall.job_run_id == run.id)))
        assert len(calls) == 3 and {c.project_id for c in calls} == {p.id}

        after = await estimate_thumbnail_analysis(db, settings, ws.id, p.id, AnalysisEstimateRequest())
        assert (after.items, after.already_analyzed) == (0, 3)
