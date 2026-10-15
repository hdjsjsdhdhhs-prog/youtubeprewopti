"""``offer_generation`` job (ТЗ §20–21, §28; AI_ARCHITECTURE §6).

For every still pending variant of the offer's current round: one structured call through ``AIRunner``
(budget reservation, ``ai_calls`` row, schema validation with one repair, model fallback) with the facts
snapshot stored on the offer and the variant's style; then the deterministic guard and personalization
score. Each variant is committed on its own, so a retried job continues with the variants still pending.

* invalid output after repair => the variant is ``failed``; a refusal => ``refused`` (the job goes on);
* budget / cost cap reached or a permanent provider error => remaining variants ``failed``, the job fails;
* a transient provider error => re-raised for a retry unless it is the last attempt.

At the end the best ready variant (first without guard issues, else the first ready one) is selected and the
offer goes to the Approval Queue (``pending_approval``; the lead too); no ready variant => ``failed``.
A job of a round that is no longer current (the owner regenerated) does nothing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError, IntegrationError
from app.core.state_machine import LeadStatus
from app.domains.ai.runner import AIInvalidOutputError, AIRefusedError, AIRunner, CallContext
from app.domains.leads.models import Lead
from app.domains.offers import drafting
from app.domains.offers.drafting import OfferDraft, OfferInputs
from app.domains.offers.models import Offer, OfferStatus, OfferVariant, OfferVariantStatus
from app.domains.offers.service import OFFER_PROMPT, TASK, check_variant, move_lead, move_offer, other_texts
from app.providers.ai import AIProvider

Sessions = Callable[[], AbstractAsyncContextManager[AsyncSession]]
Progress = Callable[[int, int], Awaitable[None]]
REASON_READY = "auto: черновики оффера готовы"
REASON_NONE = "auto: ни один черновик оффера не получен"


async def _set(sessions: Sessions, variant_id: int, status: OfferVariantStatus, error: str) -> None:
    async with sessions() as db:
        await db.execute(
            update(OfferVariant).where(OfferVariant.id == variant_id).values(status=status, error=error)
        )
        await db.commit()


async def _finish(db: AsyncSession, offer: Offer, error: str | None) -> bool:
    """Select the best ready variant of the round and move the offer (and lead) on. Returns «any ready»."""
    variants = list(
        await db.scalars(
            select(OfferVariant)
            .where(OfferVariant.offer_id == offer.id, OfferVariant.round == offer.round)
            .order_by(OfferVariant.label)
        )
    )
    ready = [v for v in variants if v.status == OfferVariantStatus.READY]
    lead = await db.scalar(
        select(Lead)
        .where(Lead.id == offer.lead_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert lead is not None
    if not ready:
        offer.error = error or "Ни один черновик не получен."
        move_offer(db, offer, OfferStatus.FAILED, None, REASON_NONE)
        return False
    best = next((v for v in ready if not v.guard_issues), ready[0])
    await db.execute(
        update(OfferVariant)
        .where(OfferVariant.offer_id == offer.id, OfferVariant.is_selected)
        .values(is_selected=False)
    )
    await db.flush()
    best.is_selected = True
    offer.error = error
    move_offer(db, offer, OfferStatus.PENDING_APPROVAL, None, REASON_READY)
    if lead.status == LeadStatus.OFFER_DRAFT:  # an operator may have rejected the lead meanwhile
        move_lead(db, lead, LeadStatus.PENDING_APPROVAL, None, REASON_READY)
    return True


async def run_offer_generation(
    sessions: Sessions,
    provider: AIProvider,
    settings: Settings,
    *,
    job_run_id: int | None,
    workspace_id: int | None,
    params: dict[str, Any],
    progress: Progress,
    last_attempt: bool,
) -> dict[str, Any]:
    """params: ``{"offer_id", "round", "lead_id", "project_id"}``."""
    offer_id, round_ = int(params["offer_id"]), int(params["round"])
    runner = AIRunner(sessions, provider, settings)
    async with sessions() as db:
        offer = await db.scalar(select(Offer).where(Offer.id == offer_id))
        if offer is None or offer.round != round_ or offer.status != OfferStatus.DRAFT:
            return {"skipped": "the offer round is no longer being generated"}
        inputs = OfferInputs.model_validate(offer.inputs)
        project_id, ws = offer.project_id, offer.workspace_id
        variants = list(
            await db.scalars(
                select(OfferVariant)
                .where(OfferVariant.offer_id == offer_id, OfferVariant.round == round_)
                .order_by(OfferVariant.label)
            )
        )
        await db.commit()

    counts = {
        s.value: 0 for s in (OfferVariantStatus.READY, OfferVariantStatus.FAILED, OfferVariantStatus.REFUSED)
    }
    total = len(variants)
    for done, variant in enumerate(variants, start=1):
        if variant.status != OfferVariantStatus.PENDING:  # finished in an earlier attempt
            counts[variant.status.value] = counts.get(variant.status.value, 0) + 1
            await progress(done, total)
            continue
        try:
            res = await runner.run(
                task=TASK,
                schema=OfferDraft,
                prompt=OFFER_PROMPT,
                variables=drafting.prompt_variables(inputs, variant.label, variant.style),
                ctx=CallContext(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    job_run_id=job_run_id,
                    input_ref={"offer_id": offer_id, "round": round_, "variant": variant.label},
                ),
            )
        except AIRefusedError as exc:
            await _set(sessions, variant.id, OfferVariantStatus.REFUSED, exc.message)
            counts[OfferVariantStatus.REFUSED.value] += 1
            await progress(done, total)
            continue
        except AIInvalidOutputError as exc:
            await _set(sessions, variant.id, OfferVariantStatus.FAILED, exc.message)
            counts[OfferVariantStatus.FAILED.value] += 1
            await progress(done, total)
            continue
        except (AppError, IntegrationError) as exc:
            transient = isinstance(exc, IntegrationError) and exc.retryable and not last_attempt
            if not transient:
                message = exc.message if isinstance(exc, AppError) else exc.human_message
                async with sessions() as db:
                    await db.execute(
                        update(OfferVariant)
                        .where(
                            OfferVariant.offer_id == offer_id,
                            OfferVariant.round == round_,
                            OfferVariant.status == OfferVariantStatus.PENDING,
                        )
                        .values(status=OfferVariantStatus.FAILED, error=message)
                    )
                    current = await db.scalar(select(Offer).where(Offer.id == offer_id).with_for_update())
                    if (
                        current is not None
                        and current.round == round_
                        and current.status == OfferStatus.DRAFT
                    ):
                        await _finish(db, current, message)
                    await db.commit()
            raise
        draft = res.output
        async with sessions() as db:
            row = await db.scalar(select(OfferVariant).where(OfferVariant.id == variant.id))
            assert row is not None
            row.subject, row.body, row.cta = draft.subject.strip(), draft.body.strip(), draft.cta.strip()
            row.draft = draft.model_dump(mode="json")
            row.model_key, row.ai_call_id, row.cost_usd = res.model_key, res.call_id, res.cost_usd
            row.status, row.error = OfferVariantStatus.READY, None
            issues, score, sources = check_variant(inputs, row, await other_texts(db, ws, offer_id))
            row.guard_issues = [i.model_dump() for i in issues]
            row.personalization_score = score
            row.personalization_sources = [s.model_dump() for s in sources]
            await db.commit()
        counts[OfferVariantStatus.READY.value] += 1
        await progress(done, total)

    async with sessions() as db:
        current = await db.scalar(select(Offer).where(Offer.id == offer_id).with_for_update())
        any_ready = False
        if current is not None and current.round == round_ and current.status == OfferStatus.DRAFT:
            any_ready = await _finish(db, current, None)
        await db.commit()
    return {
        **counts,
        "total": total,
        "offer_id": offer_id,
        "round": round_,
        "offer_status": (OfferStatus.PENDING_APPROVAL if any_ready else OfferStatus.FAILED).value,
        "provider": provider.name,
        "ai_calls": runner.calls,
        "spent_usd": str(runner.spent_usd),
    }
