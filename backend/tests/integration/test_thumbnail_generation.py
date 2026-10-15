"""Phase 5 ``thumbnail_generation`` job against the real database (per-test transaction): stored 16:9
variants, ``ai_calls`` accounting, refusals / broken output per variant, budget and job cost cap stops,
retry of transient errors, lead status at the end."""

from __future__ import annotations

import io
from contextlib import asynccontextmanager
from decimal import Decimal

import factories as f
import pytest
from PIL import Image
from sqlalchemy import select

from app.core.errors import IntegrationError, IntegrationErrorCode
from app.core.state_machine import LeadStatus
from app.domains.ai.budget import BudgetExceededError, PricingUnknownError
from app.domains.ai.models import AICall, AICallStatus, AIModel, Budget, BudgetPeriod, BudgetScope
from app.domains.analysis.audit import SUBSCORES
from app.domains.generation import service
from app.domains.generation.models import (
    GeneratedThumbnail,
    GenerationRequest,
    GenerationStatus,
    VariantStatus,
)
from app.domains.generation.pipeline import run_thumbnail_generation
from app.domains.generation.schemas import GenerationIn, GenerationStart
from app.domains.jobs.models import JobRun
from app.domains.leads.models import Lead, LeadStatusHistory
from app.domains.leads.service import sync_leads
from app.domains.media.models import ImageAsset, ImageSource
from app.domains.media.service import ensure_image_metrics, store_image
from app.providers.ai import GeneratedImage, ImageGenResponse, MockAIProvider

VC_PRICE = Decimal("0.019683")  # vc-gpt-image-2.5, flat per image


def _png(color=(10, 120, 200), size=(300, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


class ScriptedImages:
    """Image provider whose answers are scripted per call: bytes, an exception or ``None`` (= a PNG)."""

    is_mock = False

    def __init__(self, *script, name: str = "vibecode") -> None:
        self.name = name
        self.script = list(script)
        self.requests = []

    async def list_models(self):
        return []

    async def generate_structured(self, request):  # pragma: no cover - not used by generation
        raise AssertionError("generation must not make structured calls")

    async def generate_images(self, request):
        self.requests.append(request)
        step = self.script.pop(0) if self.script else None
        if isinstance(step, BaseException):
            raise step
        return ImageGenResponse(images=[GeneratedImage(step or _png(), "image/png")], model=request.model)


def _error(code: IntegrationErrorCode, *, retryable: bool = False) -> IntegrationError:
    return IntegrationError(code, f"scripted {code.value}", retryable=retryable, provider="vibecode")


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
def vibecode_mode(settings, monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "vibecode")
    return settings


@pytest.fixture
async def world(db, owner, storage):
    """One channel: a weak audited thumbnail (2.0, small_text + cluttered) and a strong one (8.0)."""
    user, ws = owner
    p = await f.project(db, ws.id, "Generation")
    ch = await f.channel(db, projects=[p], title="Кухня Пети")
    t = await f.prompt_template(db)
    assets, videos = [], []
    for seed, score, codes, days in ((501, 2, ("small_text", "cluttered"), 3), (502, 8, (), 1)):
        a = await store_image(db, storage, f.noise_png(seed), ImageSource.YOUTUBE_THUMBNAIL)
        await ensure_image_metrics(db, storage, a)
        videos.append(await f.video(db, ch, asset=a, days_ago=days, title=f"Рецепт {seed}"))
        await f.thumbnail_analysis(db, t, a, codes=codes, **{k: score for k in SUBSCORES})
        assets.append(a)
    await sync_leads(db, ws.id)
    lead = await db.scalar(select(Lead).where(Lead.channel_id == ch.id))
    return {"user": user, "ws": ws, "project": p, "channel": ch, "assets": assets, "videos": videos,
            "lead": lead}


async def _start(db, settings, world, **body):
    w = world
    est = await service.estimate_generation(db, settings, w["ws"].id, w["lead"].id, GenerationIn(**body))
    res = await service.start_generation(
        db, settings, w["ws"].id, w["user"].id, w["lead"].id,
        GenerationStart(**body, confirm_token=est.confirm_token),
    )
    return est, res


async def _run(sessions, provider, storage, settings, world, res, *, last_attempt=True):
    progress: list[tuple[int, int]] = []

    async def record(done: int, total: int) -> None:
        progress.append((done, total))

    g = res.generation
    result = await run_thumbnail_generation(
        sessions, provider, storage, settings, job_run_id=g.job.id, workspace_id=world["ws"].id,
        params={"generation_request_id": g.id, "lead_id": g.lead_id, "project_id": world["project"].id},
        progress=record, last_attempt=last_attempt,
    )
    return result, progress


async def _variants(db, request_id) -> list[GeneratedThumbnail]:
    return list(
        await db.scalars(
            select(GeneratedThumbnail)
            .where(GeneratedThumbnail.generation_request_id == request_id)
            .order_by(GeneratedThumbnail.label)
            .execution_options(populate_existing=True)
        )
    )


async def _lead_status(db, world) -> LeadStatus:
    await db.refresh(world["lead"])
    return world["lead"].status


async def _calls(db) -> list[AICall]:
    return list(
        await db.scalars(select(AICall).where(AICall.task == "thumbnail_generation").order_by(AICall.id))
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
async def test_mock_generation_stores_16_9_variants_and_moves_the_lead(
    db, sessions, storage, mock_mode, world
):
    _, res = await _start(db, mock_mode, world, variant_count=3)
    assert res.lead_status == LeadStatus.GENERATION_PENDING and res.generation.provider_is_mock
    provider = MockAIProvider()
    result, progress = await _run(sessions, provider, storage, mock_mode, world, res)

    assert (result["ready"], result["failed"], result["refused"], result["total"]) == (3, 0, 0, 3)
    assert result["lead_status"] == "generated" and result["ai_calls"] == 3 and progress[-1] == (3, 3)
    variants = await _variants(db, res.generation.id)
    assert [v.label for v in variants] == ["A", "B", "C"]
    assert {v.status for v in variants} == {VariantStatus.READY}
    assert len({v.strategy for v in variants}) == 3
    for v in variants:
        asset = await db.get(ImageAsset, v.image_asset_id)
        assert asset.source == ImageSource.GENERATED and (asset.width, asset.height) == (1280, 720)
        with Image.open(io.BytesIO(storage.get(asset.storage_key))) as im:
            assert (im.format, im.size) == ("JPEG", (1280, 720))
        assert (v.model_key, v.api_model_id, v.cost_usd) == ("mock-image", "mock-image-1", Decimal(0))
        assert v.ai_call_id is not None

    calls = await _calls(db)
    assert [c.status for c in calls] == [AICallStatus.OK] * 3
    assert all(c.job_run_id == res.generation.job.id and c.image_inputs == 1 for c in calls)
    assert calls[0].input_ref["variant"] == "A"
    req = await db.get(GenerationRequest, res.generation.id)
    assert req.status == GenerationStatus.COMPLETED and req.error is None
    assert await _lead_status(db, world) == LeadStatus.GENERATED
    last = await db.scalar(
        select(LeadStatusHistory).where(LeadStatusHistory.lead_id == world["lead"].id)
        .order_by(LeadStatusHistory.id.desc()).limit(1)
    )
    assert (last.from_status, last.to_status, last.actor_user_id) == (
        LeadStatus.GENERATION_PENDING, LeadStatus.GENERATED, None)


async def test_the_model_gets_the_variant_prompt_and_the_current_thumbnail(
    db, sessions, storage, vibecode_mode, world
):
    _, res = await _start(db, vibecode_mode, world, variant_count=2)
    provider = ScriptedImages()
    await _run(sessions, provider, storage, vibecode_mode, world, res)

    variants = await _variants(db, res.generation.id)
    assert [r.prompt for r in provider.requests] == [v.prompt for v in variants]
    first = provider.requests[0]
    assert (first.model, first.size, first.quality, first.n) == ("gpt-image-2.5", "1536x1024", None, 1)
    assert len(first.references) == 1 and first.references[0].mime == "image/jpeg"  # weakest thumbnail
    # the brief fixed what the audit found
    assert "(fixes: small_text)" in first.prompt and "(fixes: cluttered)" in first.prompt
    assert "Кухня Пети" in first.prompt
    assert [v.cost_usd for v in variants] == [VC_PRICE, VC_PRICE]
    job = await db.get(JobRun, res.generation.job.id)
    await db.refresh(job)
    assert job.budget_usd == VC_PRICE * 2 and job.actual_cost_usd == VC_PRICE * 2


async def test_describe_only_sends_no_input_image(db, sessions, storage, vibecode_mode, world):
    _, res = await _start(db, vibecode_mode, world, variant_count=1, use_source_image=False)
    provider = ScriptedImages()
    await _run(sessions, provider, storage, vibecode_mode, world, res)
    assert provider.requests[0].references == ()
    assert "No current thumbnail is attached" in provider.requests[0].prompt


# ---------------------------------------------------------------------------
# Per-variant failures: the job goes on
# ---------------------------------------------------------------------------
async def test_refused_and_broken_variants_do_not_stop_the_others(
    db, sessions, storage, vibecode_mode, world
):
    _, res = await _start(db, vibecode_mode, world, variant_count=3)
    provider = ScriptedImages(_error(IntegrationErrorCode.CONTENT_POLICY), b"not an image", None)
    result, _ = await _run(sessions, provider, storage, vibecode_mode, world, res)

    assert (result["ready"], result["failed"], result["refused"]) == (1, 1, 1)
    a, b, c = await _variants(db, res.generation.id)
    assert (a.status, a.image_asset_id, a.error) == (VariantStatus.REFUSED, None, "scripted content_policy")
    assert b.status == VariantStatus.FAILED and "unusable image" in b.error
    assert c.status == VariantStatus.READY
    assert [x.status for x in await _calls(db)] == [AICallStatus.REFUSED, AICallStatus.OK, AICallStatus.OK]
    assert await _lead_status(db, world) == LeadStatus.GENERATED


async def test_nothing_generated_returns_the_lead_to_selected(db, sessions, storage, vibecode_mode, world):
    _, res = await _start(db, vibecode_mode, world, variant_count=2)
    refused = _error(IntegrationErrorCode.CONTENT_POLICY)
    result, _ = await _run(sessions, ScriptedImages(refused, refused), storage, vibecode_mode, world, res)
    assert result["refused"] == 2 and result["lead_status"] == "selected"
    assert await _lead_status(db, world) == LeadStatus.SELECTED
    refusals = await _calls(db)
    assert all(c.actual_cost_usd == 0 for c in refusals)  # moderation blocks are not billed


# ---------------------------------------------------------------------------
# Money: budgets and the confirmed cost cap stop the job, finished variants are kept
# ---------------------------------------------------------------------------
async def test_daily_task_budget_of_20_cents_stops_the_job(db, sessions, storage, vibecode_mode, world):
    """The owner's setting (2026-10-15): $0.20/day for thumbnail generation. $0.17 already spent today =>
    one more image (≈ $0.0197) fits, the second would exceed the limit."""
    ws = world["ws"].id
    db.add(Budget(workspace_id=ws, scope=BudgetScope.TASK, scope_ref="thumbnail_generation",
                  period=BudgetPeriod.DAY, limit_usd=Decimal("0.20")))
    db.add(AICall(workspace_id=ws, task="thumbnail_generation", provider="vibecode",
                  model_key="vc-gpt-image-2.5", api_model_id="gpt-image-2.5", status=AICallStatus.OK,
                  estimated_cost_usd=Decimal("0.17"), actual_cost_usd=Decimal("0.17")))
    await db.flush()
    est, res = await _start(db, vibecode_mode, world, variant_count=3)
    assert est.estimated_cost_usd == VC_PRICE * 3 and not est.within_budgets  # the owner was warned

    provider = ScriptedImages()
    with pytest.raises(BudgetExceededError, match="day budget of task thumbnail_generation"):
        await _run(sessions, provider, storage, vibecode_mode, world, res)
    assert len(provider.requests) == 1  # the refused call was never sent
    a, b, c = await _variants(db, res.generation.id)
    assert a.status == VariantStatus.READY
    assert b.status == c.status == VariantStatus.FAILED and "USD limit 0.2000" in b.error
    req = await db.get(GenerationRequest, res.generation.id)
    await db.refresh(req)
    assert req.status == GenerationStatus.FAILED
    assert await _lead_status(db, world) == LeadStatus.GENERATED  # one variant is ready
    spent_today = sum(c.actual_cost_usd for c in await _calls(db))
    assert spent_today == Decimal("0.17") + VC_PRICE <= Decimal("0.20")


async def test_confirmed_estimate_is_a_hard_cost_cap(db, sessions, storage, vibecode_mode, world):
    _, res = await _start(db, vibecode_mode, world, variant_count=2)
    job = await db.get(JobRun, res.generation.job.id)
    job.budget_usd = VC_PRICE  # e.g. the price went up after confirmation: only one image fits
    await db.flush()
    provider = ScriptedImages()
    with pytest.raises(BudgetExceededError, match="Job cost cap"):
        await _run(sessions, provider, storage, vibecode_mode, world, res)
    assert len(provider.requests) == 1
    assert [v.status for v in await _variants(db, res.generation.id)] == [
        VariantStatus.READY, VariantStatus.FAILED]


async def test_usd_budget_with_unknown_price_refuses_to_generate(db, sessions, storage, vibecode_mode, world):
    _, res = await _start(db, vibecode_mode, world, variant_count=1)
    db.add(Budget(workspace_id=world["ws"].id, scope=BudgetScope.GLOBAL, period=BudgetPeriod.MONTH,
                  limit_usd=Decimal("1")))
    job = await db.get(JobRun, res.generation.job.id)
    job.budget_usd = None
    model = await db.scalar(select(AIModel).where(AIModel.key == "vc-gpt-image-2.5"))
    model.price_per_image = {}
    await db.flush()
    provider = ScriptedImages()
    with pytest.raises(PricingUnknownError):
        await _run(sessions, provider, storage, vibecode_mode, world, res)
    assert provider.requests == []


# ---------------------------------------------------------------------------
# Provider errors
# ---------------------------------------------------------------------------
async def test_transient_error_is_retried_and_the_retry_continues(
    db, sessions, storage, vibecode_mode, world
):
    _, res = await _start(db, vibecode_mode, world, variant_count=3)
    flaky = ScriptedImages(None, _error(IntegrationErrorCode.RATE_LIMITED, retryable=True))
    with pytest.raises(IntegrationError):
        await _run(sessions, flaky, storage, vibecode_mode, world, res, last_attempt=False)
    assert [v.status for v in await _variants(db, res.generation.id)] == [
        VariantStatus.READY, VariantStatus.PENDING, VariantStatus.PENDING]
    assert await _lead_status(db, world) == LeadStatus.GENERATION_PENDING  # the job will be retried

    retry = ScriptedImages()
    result, _ = await _run(sessions, retry, storage, vibecode_mode, world, res)
    assert len(retry.requests) == 2 and result["ready"] == 3  # A was not generated (nor paid) again
    assert await _lead_status(db, world) == LeadStatus.GENERATED


async def test_permanent_error_fails_the_rest_and_keeps_finished_variants(
    db, sessions, storage, vibecode_mode, world
):
    _, res = await _start(db, vibecode_mode, world, variant_count=3)
    provider = ScriptedImages(None, _error(IntegrationErrorCode.RATE_LIMITED, retryable=True))
    with pytest.raises(IntegrationError):  # last attempt: no retry left
        await _run(sessions, provider, storage, vibecode_mode, world, res, last_attempt=True)
    a, b, c = await _variants(db, res.generation.id)
    assert a.status == VariantStatus.READY
    assert b.status == c.status == VariantStatus.FAILED and b.error == "scripted rate_limited"
    assert await _lead_status(db, world) == LeadStatus.GENERATED


async def test_unknown_model_falls_back_only_when_not_pinned(db, sessions, storage, vibecode_mode, world):
    """The job pins the confirmed model: MODEL_UNAVAILABLE must not silently switch to another price."""
    _, res = await _start(db, vibecode_mode, world, variant_count=1)
    provider = ScriptedImages(_error(IntegrationErrorCode.MODEL_UNAVAILABLE))
    with pytest.raises(IntegrationError):
        await _run(sessions, provider, storage, vibecode_mode, world, res)
    assert [r.model for r in provider.requests] == ["gpt-image-2.5"]
    [v] = await _variants(db, res.generation.id)
    assert v.status == VariantStatus.FAILED
    assert await _lead_status(db, world) == LeadStatus.SELECTED
