"""Phase 3.5 AI thumbnail audit pipeline against the real database (per-test transaction): stored audits,
SHA-256 / pHash cache, per-image failures, budget stop, ``is_current`` switching."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import factories as f
import pytest
from sqlalchemy import select

from app.domains.ai.budget import BudgetExceededError
from app.domains.ai.models import AICall, AICallStatus, Budget, BudgetPeriod, BudgetScope
from app.domains.analysis.audit import SUBSCORES, ThumbnailAudit
from app.domains.analysis.models import ThumbnailAnalysis
from app.domains.analysis.pipeline import run_thumbnail_analysis
from app.domains.media.imaging import hamming
from app.domains.media.models import ImageSource
from app.domains.media.service import ensure_image_metrics, store_image
from app.providers.ai import MockAIProvider, StructuredResponse, Usage


def valid_audit(**over) -> str:
    base = {k: 5.0 for k in SUBSCORES} | {
        "has_face": False, "face_emotion": None, "text_present": True, "text_word_count_est": 3,
        "focal_point": "центр", "background_separation": "low", "clutter_level": "high",
        "problems": [{"code": "cluttered", "severity": "high", "description": "много деталей"}],
        "recommendations": [{"code": "cluttered", "action": "убрать фон", "rationale": "фокус"}],
        "visual_summary": "Перегружено.", "improvement_opportunity": "high",
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
    ws = owner[1].id
    p = await f.project(db, ws)
    ch = await f.channel(db, projects=[p], title="Кухня Пети")
    assets = []
    for seed in (1, 2):
        a = await store_image(db, storage, f.noise_png(seed), ImageSource.YOUTUBE_THUMBNAIL)
        await ensure_image_metrics(db, storage, a)
        assets.append(a)
    twin = await store_image(db, storage, f.noise_png(1, tweak=True), ImageSource.YOUTUBE_THUMBNAIL)
    videos = [await f.video(db, ch, asset=a, title=f"Рецепт {i}") for i, a in enumerate([*assets, twin])]
    return {"ws": ws, "project": p, "assets": assets, "twin": twin, "videos": videos}


async def _run(sessions, provider, storage, settings, world, asset_ids, job_run_id=None):
    progress: list[tuple[int, int]] = []

    async def record(done: int, total: int) -> None:
        progress.append((done, total))

    result = await run_thumbnail_analysis(
        sessions, provider, storage, settings, job_run_id=job_run_id, workspace_id=world["ws"],
        params={"project_id": world["project"].id, "asset_ids": asset_ids, "detail": "low"}, progress=record,
    )
    return result, progress


async def _analyses(db) -> list[ThumbnailAnalysis]:
    return list(await db.scalars(select(ThumbnailAnalysis).order_by(ThumbnailAnalysis.id)))


async def test_mock_audit_is_stored_and_cached(db, sessions, storage, mock_mode, world):
    a1, a2 = world["assets"]
    provider = MockAIProvider()
    result, progress = await _run(sessions, provider, storage, mock_mode, world, [a1.id, a2.id])

    assert (result["analyzed"], result["cached"], result["reused"], result["failed"]) == (2, 0, 0, 0)
    assert result["ai_calls"] == 2 and result["models"] == ["mock-vision"] and progress[-1] == (2, 2)
    rows = await _analyses(db)
    assert [r.image_asset_id for r in rows] == [a1.id, a2.id] and all(r.is_current for r in rows)
    first = rows[0]
    assert (first.provider, first.model_key, first.detail, first.reused_from_id) == (
        "mock", "mock-vision", "low", None
    )
    assert all(1 <= getattr(first, s) <= 10 for s in SUBSCORES)
    assert first.problem_codes == sorted({p["code"] for p in first.problems})
    call = await db.get(AICall, first.ai_call_id)
    assert call.status == AICallStatus.OK and call.input_ref["image_asset_id"] == a1.id
    ThumbnailAudit.model_validate(call.output)

    # what the model saw: the prompt with title, channel, metrics; one JPEG image
    req = provider.requests[0]
    assert "Рецепт 0" in req.user and "Кухня Пети" in req.user and "mean brightness" in req.user
    assert req.images[0].mime == "image/jpeg" and req.images[0].detail == "low"
    assert "Never promise or estimate CTR" in req.system

    again, _ = await _run(sessions, MockAIProvider(), storage, mock_mode, world, [a1.id, a2.id])
    assert (again["analyzed"], again["cached"], again["ai_calls"]) == (0, 2, 0)
    assert len(await _analyses(db)) == 2


async def test_visually_identical_image_reuses_the_audit(db, sessions, storage, mock_mode, world):
    a1, _ = world["assets"]
    twin = world["twin"]
    distance = hamming(twin.phash, a1.phash)
    assert twin.sha256 != a1.sha256 and distance <= 2
    result, _ = await _run(sessions, MockAIProvider(), storage, mock_mode, world, [a1.id, twin.id])

    assert (result["analyzed"], result["reused"], result["ai_calls"]) == (1, 1, 1)
    src, copy = await _analyses(db)
    assert (copy.image_asset_id, copy.reused_from_id, copy.phash_distance) == (twin.id, src.id, distance)
    assert copy.ai_call_id == src.ai_call_id and copy.visual_summary == src.visual_summary
    assert all(getattr(copy, s) == getattr(src, s) for s in SUBSCORES)


async def test_phash_reuse_can_be_disabled(db, sessions, storage, mock_mode, world, monkeypatch):
    monkeypatch.setattr(mock_mode, "analysis_phash_max_distance", -1)
    a1, _ = world["assets"]
    result, _ = await _run(sessions, MockAIProvider(), storage, mock_mode, world, [a1.id, world["twin"].id])
    assert (result["analyzed"], result["reused"]) == (2, 0)


async def test_invalid_output_fails_the_image_not_the_job(db, sessions, storage, mock_mode, world):
    a1, a2 = world["assets"]
    provider = Scripted('{"bad": 1}', '{"still": "bad"}', valid_audit(composition_score=2))
    result, _ = await _run(sessions, provider, storage, mock_mode, world, [a1.id, a2.id])

    assert (result["analyzed"], result["failed"]) == (1, 1)
    assert result["failures"][0]["image_asset_id"] == a1.id and "invalid" in result["failures"][0]["error"]
    [row] = await _analyses(db)
    assert row.image_asset_id == a2.id and float(row.composition_score) == 2.0
    assert row.problem_codes == ["cluttered"]


async def test_missing_file_is_reported(db, sessions, storage, mock_mode, world):
    a1, _ = world["assets"]
    storage.delete(a1.storage_key)
    result, _ = await _run(sessions, MockAIProvider(), storage, mock_mode, world, [a1.id])
    assert result["failed"] == 1 and "missing" in result["failures"][0]["error"]


async def test_budget_stop_keeps_finished_audits(db, sessions, storage, mock_mode, world):
    db.add(Budget(workspace_id=world["ws"], scope=BudgetScope.GLOBAL, period=BudgetPeriod.DAY,
                  max_ai_operations=1))
    await db.flush()
    a1, a2 = world["assets"]
    with pytest.raises(BudgetExceededError):
        await _run(sessions, MockAIProvider(), storage, mock_mode, world, [a1.id, a2.id])
    [row] = await _analyses(db)
    assert row.image_asset_id == a1.id


async def test_new_model_result_becomes_current_and_rerun_switches_back(
    db, sessions, storage, mock_mode, world, monkeypatch
):
    a1, _ = world["assets"]
    await _run(sessions, MockAIProvider(), storage, mock_mode, world, [a1.id])
    [mock_row] = await _analyses(db)

    # pretend the same image was audited by another model of another provider earlier/later
    monkeypatch.setattr(mock_mode, "ai_provider", "vibecode")
    provider = Scripted(valid_audit())
    provider.name, provider.is_mock = "vibecode", False
    result, _ = await _run(sessions, provider, storage, mock_mode, world, [a1.id])
    assert result["analyzed"] == 1 and result["models"][0] == "vc-vision-standard"
    rows = await _analyses(db)
    await db.refresh(mock_row)
    real = rows[1]
    assert (real.provider, real.model_key, real.is_current, mock_row.is_current) == (
        "vibecode", "vc-vision-standard", True, False)

    monkeypatch.setattr(mock_mode, "ai_provider", "mock")
    back, _ = await _run(sessions, MockAIProvider(), storage, mock_mode, world, [a1.id])
    assert back["cached"] == 1
    await db.refresh(mock_row)
    await db.refresh(real)
    assert mock_row.is_current and not real.is_current
