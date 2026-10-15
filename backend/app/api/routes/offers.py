"""Offers and the Approval Queue (Phase 7, ТЗ §20–23, §28). Nothing here sends a message: «Approve» and
«Schedule» only record the human decision bound to the exact content (sending is Phase 8)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.api.deps import AppSettings, DbSession, Paging, ReadAuth, SendAuth, WriteAuth
from app.domains.offers import service, templates
from app.domains.offers.schemas import (
    ApprovalQueue,
    LeadOffers,
    OfferDecisionIn,
    OfferEdit,
    OfferEstimate,
    OfferIn,
    OfferOut,
    OfferScheduleIn,
    OfferStart,
    OfferStartResult,
    OfferTemplateIn,
    OfferTemplateOut,
    QueueStatus,
)

router = APIRouter(tags=["offers"])


@router.post("/leads/{lead_id}/offer/estimate", response_model=OfferEstimate)
async def estimate_offer(
    lead_id: int, body: OfferIn, ctx: ReadAuth, db: DbSession, settings: AppSettings
) -> OfferEstimate:
    """The facts the drafts may use (channel, concrete videos, audit problems, the free sample …), the exact
    prompt of variant A, price, budgets and the ``confirm_token``. Nothing is generated or charged."""
    return await service.estimate_offer(db, settings, ctx.workspace.id, lead_id, body)


@router.post("/leads/{lead_id}/offers", response_model=OfferStartResult, status_code=status.HTTP_202_ACCEPTED)
async def start_offer(
    lead_id: int, body: OfferStart, ctx: WriteAuth, db: DbSession, settings: AppSettings
) -> OfferStartResult:
    """Queue the confirmed estimate: a new offer, or a new round of the lead's latest one («Regenerate»).
    409: ``confirmation_required`` (new token), ``offer_generating``, ``generation_running``,
    ``lead_status``."""
    return await service.start_offer(db, settings, ctx.workspace.id, ctx.user.id, lead_id, body)


@router.get("/leads/{lead_id}/offers", response_model=LeadOffers)
async def lead_offers(lead_id: int, ctx: ReadAuth, db: DbSession) -> LeadOffers:
    return await service.lead_offers(db, ctx.workspace.id, lead_id)


@router.get("/offers/{offer_id}", response_model=OfferOut)
async def get_offer(offer_id: int, ctx: ReadAuth, db: DbSession) -> OfferOut:
    return await service.get_offer(db, ctx.workspace.id, offer_id)


@router.patch("/offers/{offer_id}", response_model=OfferOut)
async def edit_offer(offer_id: int, body: OfferEdit, ctx: WriteAuth, db: DbSession) -> OfferOut:
    """ТЗ §23: text / subject / CTA (marks ``manually_edited``), selected draft, attachment, contact, channel.
    A changed approved / scheduled offer goes back to the queue."""
    return await service.edit_offer(db, ctx.workspace.id, ctx.user.id, offer_id, body)


@router.post("/offers/{offer_id}/decision", response_model=OfferOut)
async def decide_offer(offer_id: int, body: OfferDecisionIn, ctx: SendAuth, db: DbSession) -> OfferOut:
    """approve (echo ``content_hash``; 409 ``content_changed`` / ``offer_not_approvable`` with blockers),
    reject, skip (stays in the queue, at its end)."""
    return await service.decide_offer(db, ctx.workspace.id, ctx.user.id, offer_id, body)


@router.post("/offers/{offer_id}/schedule", response_model=OfferOut)
async def schedule_offer(offer_id: int, body: OfferScheduleIn, ctx: SendAuth, db: DbSession) -> OfferOut:
    """Store the send time of an approved offer (409 ``not_approved`` / ``approval_outdated``)."""
    return await service.schedule_offer(db, ctx.workspace.id, ctx.user.id, offer_id, body)


@router.delete("/offers/{offer_id}/schedule", response_model=OfferOut)
async def unschedule_offer(offer_id: int, ctx: SendAuth, db: DbSession) -> OfferOut:
    return await service.unschedule_offer(db, ctx.workspace.id, ctx.user.id, offer_id)


@router.get("/approval-queue", response_model=ApprovalQueue)
async def approval_queue(
    ctx: ReadAuth,
    db: DbSession,
    page: Paging,
    status_: Annotated[QueueStatus, Query(alias="status")] = "pending_approval",
    project_id: int | None = None,
) -> ApprovalQueue:
    """ТЗ §22 cards: creator / channel, thumbnail, AI analysis, Lead Score, generated preview, offer draft,
    contact channels."""
    return await service.approval_queue(
        db,
        ctx.workspace.id,
        status=status_,
        project_id=project_id,
        limit=min(page.limit, 100),
        offset=page.offset,
    )


@router.get("/offer-templates", response_model=list[OfferTemplateOut])
async def list_templates(ctx: ReadAuth, db: DbSession) -> list[OfferTemplateOut]:
    return await templates.list_templates(db, ctx.workspace.id)


@router.post("/offer-templates", response_model=OfferTemplateOut, status_code=status.HTTP_201_CREATED)
async def create_template(body: OfferTemplateIn, ctx: WriteAuth, db: DbSession) -> OfferTemplateOut:
    return await templates.create_template(db, ctx.workspace.id, ctx.user.id, body)


@router.put("/offer-templates/{template_id}", response_model=OfferTemplateOut)
async def update_template(
    template_id: int, body: OfferTemplateIn, ctx: WriteAuth, db: DbSession
) -> OfferTemplateOut:
    """Adds a new version; offers keep the version they were written from."""
    return await templates.update_template(db, ctx.workspace.id, ctx.user.id, template_id, body)


@router.delete("/offer-templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_template(template_id: int, ctx: WriteAuth, db: DbSession) -> Response:
    await templates.archive_template(db, ctx.workspace.id, ctx.user.id, template_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
