"""End-to-end: API-level enqueue → real Procrastinate worker → discovery with the offline mock.

Data is really committed (the worker uses its own connections); tables are truncated afterwards.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select, text

import app.workers.tasks  # noqa: F401  (registers tasks on queue_app)
from app.core.db import get_sessionmaker
from app.domains.discovery.models import YouTubeQuotaLedger
from app.domains.discovery.service import start_discovery
from app.domains.identity.service import create_owner
from app.domains.jobs.models import JobRun
from app.domains.projects.models import ProjectChannel, QueryStatus, SearchProject, SearchQuery
from app.domains.youtube.models import Channel
from app.providers.youtube import MockYouTubeProvider
from app.workers.queue import queue_app

TRUNCATE = (
    "TRUNCATE users, workspaces, channels, image_assets, job_runs, audit_logs, youtube_quota_ledger, "
    "procrastinate_events, procrastinate_periodic_defers, procrastinate_jobs, procrastinate_workers "
    "RESTART IDENTITY CASCADE"
)


@pytest.fixture
async def committed(settings, monkeypatch):
    monkeypatch.setattr(settings, "worker_retry_base_seconds", 0)
    monkeypatch.setattr(settings, "youtube_provider", "mock")
    sessions = get_sessionmaker()
    async with sessions() as db:
        user, ws = await create_owner(
            db,
            email="disc@example.com",
            password="correct-horse-battery-staple",
            display_name="D",
            workspace_name="Discovery WS",
        )
        project = SearchProject(workspace_id=ws.id, name="P", results_per_query=10, videos_to_analyze=5)
        db.add(project)
        await db.flush()
        for t in ("etf", "dividends"):
            db.add(SearchQuery(project_id=project.id, text=t, text_normalized=t))
        await db.commit()
    try:
        yield {"user": user, "ws": ws, "project": project}
    finally:
        async with sessions() as db:
            await db.execute(text(TRUNCATE))
            await db.commit()


async def _start(data, settings) -> int | None:
    async with get_sessionmaker()() as db:
        plan = await start_discovery(
            db,
            settings,
            data["ws"].id,
            data["user"].id,
            data["project"].id,
            query_ids=None,
            include_done=False,
        )
        await db.commit()
    return plan.enqueued.job.id if plan.enqueued else None


async def _run_worker(provider) -> None:
    async with queue_app.open_async():
        await queue_app.run_worker_async(
            queues=["bulk"],
            wait=False,
            concurrency=1,
            listen_notify=False,
            install_signal_handlers=False,
            additional_context={"youtube_provider": provider},
        )


async def _snapshot(job_id: int):
    async with get_sessionmaker()() as db:
        run = await db.scalar(select(JobRun).where(JobRun.id == job_id))
        statuses = dict((await db.execute(select(SearchQuery.text, SearchQuery.status))).tuples().all())
        channels = await db.scalar(select(func.count()).select_from(Channel))
        members = await db.scalar(select(func.count()).select_from(ProjectChannel))
        used = await db.scalar(select(func.sum(YouTubeQuotaLedger.units_used)))
    return run, statuses, channels, members, used


async def test_discovery_job_runs_all_pending_queries(committed, settings):
    job_id = await _start(committed, settings)
    await _run_worker(MockYouTubeProvider())

    run, statuses, channels, members, used = await _snapshot(job_id)
    assert run.status == "completed" and run.attempts == 1, run.error_human
    assert (run.progress_done, run.progress_total) == (2, 2)
    assert statuses == {"etf": QueryStatus.DONE, "dividends": QueryStatus.DONE}
    assert run.result["queries_done"] == 2 and run.result["provider"] == "youtube_mock"
    assert run.result["channels_found"] == members == channels  # one row per channel, deduplicated
    assert run.result["quota_units_spent"] == used > 200  # committed ledger survives the job transaction
    assert await _start(committed, settings) is None  # nothing pending any more


async def test_quota_exhaustion_stops_the_job_and_the_rest_resumes_later(committed, settings, monkeypatch):
    monkeypatch.setattr(settings, "youtube_daily_quota", 150)  # enough for exactly one query
    job_id = await _start(committed, settings)
    await _run_worker(MockYouTubeProvider())

    run, statuses, _, _, used = await _snapshot(job_id)
    assert run.status == "failed" and run.attempts == 1  # quota errors are not retried
    assert run.error_code == "quota_exceeded" and "Pacific Time" in run.error_human
    assert sorted(statuses.values()) == [QueryStatus.DONE, QueryStatus.PENDING]  # nothing left RUNNING
    assert used <= 150

    monkeypatch.setattr(settings, "youtube_daily_quota", 10_000)  # e.g. after the reset
    resumed = await _start(committed, settings)
    await _run_worker(MockYouTubeProvider())
    run, statuses, _, _, _ = await _snapshot(resumed)
    assert run.status == "completed" and run.params["query_ids"] and len(run.params["query_ids"]) == 1
    assert set(statuses.values()) == {QueryStatus.DONE}
