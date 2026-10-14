"""Phase 3.6 comparative channel audit pipeline against the real database (per-test transaction): input
selection, stored audit, cache by inputs, re-audit after a new thumbnail audit, per-channel failures,
budget stop."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import factories as f
import pytest
from sqlalchemy import select, update

from app.domains.ai.budget import BudgetExceededError
from app.domains.ai.models import AICall, AICallStatus, Budget, BudgetPeriod, BudgetScope
from app.domains.analysis.channel_audit import ChannelVisualAudit
from app.domains.analysis.channel_pipeline import run_channel_analysis, select_channel_inputs
from app.domains.analysis.models import ChannelAnalysis, ThumbnailAnalysis
from app.domains.media.models import ImageSource
from app.domains.media.service import store_image
from app.providers.ai import MockAIProvider, StructuredResponse, Usage


def valid_audit(**over) -> str:
    base = {
        "visual_consistency": 3, "repeated_template": True, "outdated_style": 7, "has_design_system": False,
        "better_performing_patterns": [], "recurring_problems": [
            {"code": "small_text", "frequency": "high", "description": "мелкий текст"}],
        "weakest_video_indexes": [2], "best_redesign_candidates": [{"video_index": 1, "why": "идея"}],
        "visual_opportunity": "high", "summary": "Слабо.",
    }
    return json.dumps(base | over, ensure_ascii=False)


class Scripted:
    name, is_mock = "mock", True

    def __init__(self, *texts: str) -> None:
        self.texts = list(texts)
        self.requests = []

    async def list_models(self):
        return []

    async def generate_structured(self, request):
        self.requests.append(request)
        return StructuredResponse(
            text=self.texts.pop(0), refusal=None, usage=Usage(100, 50, 1), model=request.model
        )


@pytest.fixture
def sessions(db):
    @asynccontextmanager
    async def shared():
        yield db

    return shared


@pytest.fixture
def mock_mode(settings, monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "mock")
    return settings


@pytest.fixture
async def world(db, owner, storage):
    """Channel A: 4 audited videos + 1 Short + 1 not audited; channel B: 2 audited (not enough)."""
    ws = owner[1].id
    p = await f.project(db, ws)
    t = await f.prompt_template(db)
    a = await f.channel(db, projects=[p], title="Кухня Пети", subscriber_count=12000)
    b = await f.channel(db, projects=[p])
    seq = iter(range(100, 200))

    async def audited(ch, days_ago, **kw):
        asset = await store_image(db, storage, f.noise_png(next(seq)), ImageSource.YOUTUBE_THUMBNAIL)
        v = await f.video(db, ch, days_ago=days_ago, asset=asset, title=f"Рецепт {days_ago}", **kw)
        ta = await f.thumbnail_analysis(db, t, asset, codes=["small_text"])
        return v, asset, ta

    a_videos = [await audited(a, d, view_count=100 * d) for d in (1, 2, 3, 4)]
    await audited(a, 0, is_short=True)
    await f.video(db, a, days_ago=5)  # not downloaded / not audited
    b_videos = [await audited(b, d) for d in (1, 2)]
    return {"ws": ws, "project": p, "template": t, "a": a, "b": b, "a_videos": a_videos, "b_videos": b_videos}


async def _run(sessions, provider, storage, settings, world, channel_ids, n=8, job_run_id=None):
    progress: list[tuple[int, int]] = []

    async def record(done: int, total: int) -> None:
        progress.append((done, total))

    result = await run_channel_analysis(
        sessions, provider, storage, settings, job_run_id=job_run_id, workspace_id=world["ws"],
        params={"project_id": world["project"].id, "channel_ids": channel_ids, "videos_per_channel": n,
                "detail": "low"},
        progress=record,
    )
    return result, progress


async def _analyses(db) -> list[ChannelAnalysis]:
    return list(await db.scalars(select(ChannelAnalysis).order_by(ChannelAnalysis.id)))


async def test_input_selection_newest_audited_non_shorts(db, world):
    inputs = await select_channel_inputs(db, world["project"].id, 3)
    a, b = world["a"], world["b"]
    assert set(inputs) == {a.id, b.id}
    assert [v.video_id for v in inputs[a.id]] == [v.id for v, _, _ in world["a_videos"][:3]]  # newest first
    assert len(inputs[b.id]) == 2
    other = await f.project(db, world["ws"])
    assert await select_channel_inputs(db, other.id, 3) == {}  # scoped to the project's channels


async def test_mock_channel_audit_is_stored_and_cached(db, sessions, storage, mock_mode, world):
    a, b = world["a"], world["b"]
    provider = MockAIProvider()
    result, progress = await _run(sessions, provider, storage, mock_mode, world, [a.id, b.id])

    assert (result["analyzed"], result["not_enough"], result["cached"], result["failed"]) == (1, 1, 0, 0)
    assert result["ai_calls"] == 1 and result["models"] == ["mock-vision"] and progress[-1] == (2, 2)
    assert result["failures"][0]["channel_id"] == b.id and "at least 3" in result["failures"][0]["error"]
    [row] = await _analyses(db)
    expected = [v.id for v, _, _ in world["a_videos"]]
    assert (row.channel_id, row.is_current, row.provider, row.video_ids) == (a.id, True, "mock", expected)
    assert row.thumbnail_analysis_ids == [ta.id for _, _, ta in world["a_videos"]]
    assert set(row.weakest_video_ids) <= set(expected)
    assert all(c["video_id"] in expected for c in row.redesign_candidates)
    call = await db.get(AICall, row.ai_call_id)
    assert call.status == AICallStatus.OK and call.task == "channel_analysis"
    assert call.input_ref["channel_id"] == a.id and call.image_inputs == 4
    ChannelVisualAudit.model_validate(call.output)

    req = provider.requests[0]
    assert len(req.images) == 4 and all(i.mime == "image/jpeg" and i.detail == "low" for i in req.images)
    assert "Кухня Пети" in req.user and "12 000" in req.user and 'Video 1: "Рецепт 1"' in req.user
    assert "Video 4:" in req.user and "Video 5:" not in req.user
    assert "never a cause" in req.system

    again, _ = await _run(sessions, MockAIProvider(), storage, mock_mode, world, [a.id])
    assert (again["cached"], again["ai_calls"]) == (1, 0)
    assert len(await _analyses(db)) == 1


async def test_new_thumbnail_audit_means_a_new_channel_audit(db, sessions, storage, mock_mode, world):
    a = world["a"]
    await _run(sessions, MockAIProvider(), storage, mock_mode, world, [a.id])
    [old] = await _analyses(db)
    # the newest video's thumbnail is re-audited (e.g. by a real model): inputs change
    _, asset, ta = world["a_videos"][0]
    await db.execute(update(ThumbnailAnalysis).where(ThumbnailAnalysis.id == ta.id).values(is_current=False))
    await f.thumbnail_analysis(db, world["template"], asset, model_key="other")
    result, _ = await _run(sessions, MockAIProvider(), storage, mock_mode, world, [a.id])
    assert result["analyzed"] == 1
    old_row, new_row = await _analyses(db)
    await db.refresh(old_row)
    assert old_row.id == old.id and not old_row.is_current and new_row.is_current
    assert new_row.inputs_digest != old_row.inputs_digest


async def test_fewer_videos_per_channel_is_a_different_input(db, sessions, storage, mock_mode, world):
    a = world["a"]
    await _run(sessions, MockAIProvider(), storage, mock_mode, world, [a.id], n=4)
    result, _ = await _run(sessions, MockAIProvider(), storage, mock_mode, world, [a.id], n=3)
    assert result["analyzed"] == 1
    assert [len(r.video_ids) for r in await _analyses(db)] == [4, 3]


async def test_invalid_output_fails_the_channel_not_the_job(db, sessions, storage, mock_mode, world):
    a = world["a"]
    second = await f.channel(db, projects=[world["project"]])
    t = world["template"]
    for d in (1, 2, 3):
        asset = await store_image(db, storage, f.noise_png(300 + d), ImageSource.YOUTUBE_THUMBNAIL)
        await f.video(db, second, days_ago=d, asset=asset)
        await f.thumbnail_analysis(db, t, asset)
    provider = Scripted('{"bad": 1}', '{"still": "bad"}', valid_audit(weakest_video_indexes=[2, 12]))
    result, _ = await _run(sessions, provider, storage, mock_mode, world, [a.id, second.id])

    assert (result["analyzed"], result["failed"]) == (1, 1)
    assert result["failures"][0]["channel_id"] == a.id and "invalid" in result["failures"][0]["error"]
    [row] = await _analyses(db)
    assert row.channel_id == second.id and row.recurring_problem_codes == ["small_text"]
    assert len(row.weakest_video_ids) == 1  # position 12 does not exist (3 videos) and is dropped


async def test_missing_file_is_reported(db, sessions, storage, mock_mode, world):
    _, asset, _ = world["a_videos"][1]
    storage.delete(asset.storage_key)
    result, _ = await _run(sessions, MockAIProvider(), storage, mock_mode, world, [world["a"].id])
    assert result["failed"] == 1 and "missing" in result["failures"][0]["error"]
    assert await _analyses(db) == []


async def test_budget_stop_raises_and_stores_nothing(db, sessions, storage, mock_mode, world):
    db.add(Budget(workspace_id=world["ws"], scope=BudgetScope.TASK, scope_ref="channel_analysis",
                  period=BudgetPeriod.DAY, max_ai_operations=0))
    await db.flush()
    with pytest.raises(BudgetExceededError):
        await _run(sessions, MockAIProvider(), storage, mock_mode, world, [world["a"].id])
    assert await _analyses(db) == []
