"""Phase 7 ``offer_generation`` job against the real database (per-test transaction): retry of transient
errors, refusals per variant, a permanent provider error and a budget stop, ``ai_calls`` accounting."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import factories as f
import pytest
from sqlalchemy import func, select

from app.core.errors import IntegrationError, IntegrationErrorCode
from app.core.state_machine import LeadStatus
from app.domains.ai.budget import BudgetExceededError
from app.domains.ai.models import AICall, AICallStatus, Budget, BudgetPeriod, BudgetScope
from app.domains.analysis.audit import SUBSCORES
from app.domains.leads.models import Lead
from app.domains.leads.service import sync_leads
from app.domains.media.models import ImageSource
from app.domains.media.service import store_image
from app.domains.offers import service
from app.domains.offers.models import Offer, OfferStatus, OfferVariant, OfferVariantStatus
from app.domains.offers.pipeline import run_offer_generation
from app.domains.offers.schemas import OfferIn, OfferStart
from app.providers.ai import StructuredResponse
from app.providers.ai.base import Usage

CLEAN = {
    "subject": "Превью для Кухни",
    "body": "Посмотрел «Рецепт 501»: мелкий текст на превью. Сделал образец, посмотрите?",
    "cta": "Посмотрите?",
    "structure": {k: "…" for k in ("observation", "problem", "insight", "value", "free_sample", "cta")},
    "facts_used": [1],
    "claims": [],
}
REFUSAL = object()


class Scripted:
    name = "mock"
    is_mock = True

    def __init__(self, *script) -> None:
        self.script = list(script)
        self.calls = 0

    async def list_models(self):
        return []

    async def generate_images(self, request):  # pragma: no cover
        raise AssertionError

    async def generate_structured(self, request):
        self.calls += 1
        step = self.script.pop(0) if self.script else CLEAN
        if isinstance(step, BaseException):
            raise step
        if step is REFUSAL:
            return StructuredResponse(
                text=None, refusal="I can't help with that", usage=Usage(10, 0), model=request.model
            )
        return StructuredResponse(
            text=json.dumps(step, ensure_ascii=False), refusal=None, usage=Usage(10, 10), model=request.model
        )


def _err(retryable: bool) -> IntegrationError:
    code = IntegrationErrorCode.PROVIDER_UNAVAILABLE if retryable else IntegrationErrorCode.FORBIDDEN
    return IntegrationError(code, f"scripted {code.value}", retryable=retryable, provider="mock")


@pytest.fixture
def sessions(db):
    @asynccontextmanager
    async def shared():
        yield db

    return shared


@pytest.fixture
async def world(db, owner, storage, settings, monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "mock")
    user, ws = owner
    p = await f.project(db, ws.id, "Offers")
    ch = await f.channel(db, projects=[p], title="Кухня")
    t = await f.prompt_template(db)
    a = await store_image(db, storage, f.noise_png(501), ImageSource.YOUTUBE_THUMBNAIL)
    await f.video(db, ch, asset=a, title="Рецепт 501")
    await f.thumbnail_analysis(db, t, a, codes=("small_text",), **{k: 3 for k in SUBSCORES})
    await sync_leads(db, ws.id)
    lead = await db.scalar(select(Lead).where(Lead.channel_id == ch.id))
    return {"user": user, "ws": ws, "lead": lead, "settings": settings}


async def _start(db, world, n=3) -> Offer:
    w = world
    est = await service.estimate_offer(db, w["settings"], w["ws"].id, w["lead"].id, OfferIn(variant_count=n))
    res = await service.start_offer(
        db,
        w["settings"],
        w["ws"].id,
        w["user"].id,
        w["lead"].id,
        OfferStart(variant_count=n, confirm_token=est.confirm_token),
    )
    return await db.get(Offer, res.offer.id)


async def _run(db, sessions, world, offer, provider, *, last_attempt=True):
    async def progress(done, total):
        return None

    return await run_offer_generation(
        sessions,
        provider,
        world["settings"],
        job_run_id=offer.job_run_id,
        workspace_id=world["ws"].id,
        params={
            "offer_id": offer.id,
            "round": offer.round,
            "lead_id": offer.lead_id,
            "project_id": offer.project_id,
        },
        progress=progress,
        last_attempt=last_attempt,
    )


async def _statuses(db, offer) -> list[OfferVariantStatus]:
    rows = await db.scalars(
        select(OfferVariant)
        .where(OfferVariant.offer_id == offer.id)
        .order_by(OfferVariant.label)
        .execution_options(populate_existing=True)
    )
    return [v.status for v in rows]


async def test_transient_error_is_retried_and_the_job_resumes(db, sessions, world):
    offer = await _start(db, world)
    with pytest.raises(IntegrationError):
        await _run(db, sessions, world, offer, Scripted(CLEAN, _err(True)), last_attempt=False)
    assert await _statuses(db, offer) == [
        OfferVariantStatus.READY,
        OfferVariantStatus.PENDING,
        OfferVariantStatus.PENDING,
    ]
    assert (await db.get(Offer, offer.id)).status == OfferStatus.DRAFT
    second = Scripted()
    result = await _run(db, sessions, world, offer, second)
    assert second.calls == 2  # only the variants still pending
    assert result["ready"] == 3 and result["offer_status"] == "pending_approval"
    await db.refresh(offer)
    assert offer.status == OfferStatus.PENDING_APPROVAL
    assert (await db.get(Lead, world["lead"].id)).status == LeadStatus.PENDING_APPROVAL


async def test_refusal_fails_only_that_variant(db, sessions, world):
    offer = await _start(db, world)
    result = await _run(db, sessions, world, offer, Scripted(REFUSAL, CLEAN, CLEAN))
    assert (result["refused"], result["ready"]) == (1, 2)
    assert await _statuses(db, offer) == [
        OfferVariantStatus.REFUSED,
        OfferVariantStatus.READY,
        OfferVariantStatus.READY,
    ]
    refused = await db.scalar(
        select(func.count()).select_from(AICall).where(AICall.status == AICallStatus.REFUSED)
    )
    assert refused == 1


async def test_permanent_error_fails_the_rest_and_the_offer(db, sessions, world):
    offer = await _start(db, world)
    with pytest.raises(IntegrationError):
        await _run(db, sessions, world, offer, Scripted(_err(False)), last_attempt=False)
    assert await _statuses(db, offer) == [OfferVariantStatus.FAILED] * 3
    await db.refresh(offer)
    assert offer.status == OfferStatus.FAILED and "scripted" in (offer.error or "")
    assert (await db.get(Lead, world["lead"].id)).status == LeadStatus.OFFER_DRAFT


async def test_budget_stop_keeps_the_finished_draft(db, sessions, world):
    db.add(
        Budget(
            workspace_id=world["ws"].id,
            scope=BudgetScope.TASK,
            scope_ref="offer_generation",
            period=BudgetPeriod.DAY,
            max_ai_operations=1,
        )
    )
    await db.flush()
    offer = await _start(db, world)
    with pytest.raises(BudgetExceededError):
        await _run(db, sessions, world, offer, Scripted())
    assert await _statuses(db, offer) == [
        OfferVariantStatus.READY,
        OfferVariantStatus.FAILED,
        OfferVariantStatus.FAILED,
    ]
    await db.refresh(offer)
    # the finished draft still reaches the queue; the reason of the stop is kept
    assert offer.status == OfferStatus.PENDING_APPROVAL and "budget" in (offer.error or "").lower()
