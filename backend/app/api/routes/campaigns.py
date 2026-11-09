"""Campaigns (Phase 8.2, ТЗ §28–29, §35, §59). Nothing here approves an offer: every offer of a campaign goes
through the Approval Queue one by one; a *running* campaign only queues what a human approved and the
dispatcher sends it within the campaign's window and limits."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import AppSettings, DbSession, Paging, ReadAuth, SendAuth, WriteAuth
from app.domains.campaigns import service
from app.domains.campaigns.models import CampaignLeadStatus, CampaignStatus
from app.domains.campaigns.schemas import (
    CampaignGenerationEstimate,
    CampaignGenerationResult,
    CampaignGenerationStart,
    CampaignIn,
    CampaignLeadList,
    CampaignLeadsIn,
    CampaignLeadsResult,
    CampaignList,
    CampaignOut,
    CampaignPatch,
    CampaignStats,
)

router = APIRouter(tags=["campaigns"])


@router.get("/campaigns", response_model=CampaignList)
async def list_campaigns(
    ctx: ReadAuth,
    db: DbSession,
    status_: Annotated[CampaignStatus | None, Query(alias="status")] = None,
    include_archived: bool = False,
) -> CampaignList:
    return await service.list_campaigns(
        db, ctx.workspace.id, status=status_, include_archived=include_archived
    )


@router.post("/campaigns", response_model=CampaignOut, status_code=status.HTTP_201_CREATED)
async def create_campaign(body: CampaignIn, ctx: WriteAuth, db: DbSession) -> CampaignOut:
    return await service.create_campaign(db, ctx.workspace.id, ctx.user.id, body)


@router.get("/campaigns/{campaign_id}", response_model=CampaignOut)
async def get_campaign(campaign_id: int, ctx: ReadAuth, db: DbSession) -> CampaignOut:
    return await service.get_campaign(db, ctx.workspace.id, campaign_id)


@router.patch("/campaigns/{campaign_id}", response_model=CampaignOut)
async def update_campaign(
    campaign_id: int, body: CampaignPatch, ctx: WriteAuth, db: DbSession
) -> CampaignOut:
    """Until completed. New settings apply to what is queued / written next."""
    return await service.update_campaign(db, ctx.workspace.id, ctx.user.id, campaign_id, body)


@router.get("/campaigns/{campaign_id}/leads", response_model=CampaignLeadList)
async def list_leads(
    campaign_id: int,
    ctx: ReadAuth,
    db: DbSession,
    page: Paging,
    status_: Annotated[CampaignLeadStatus | None, Query(alias="status")] = None,
) -> CampaignLeadList:
    return await service.list_campaign_leads(
        db, ctx.workspace.id, campaign_id, status=status_, limit=min(page.limit, 200), offset=page.offset
    )


@router.post("/campaigns/{campaign_id}/leads", response_model=CampaignLeadsResult)
async def add_leads(
    campaign_id: int, body: CampaignLeadsIn, ctx: WriteAuth, db: DbSession
) -> CampaignLeadsResult:
    """«Add to campaign»: chosen leads or all filtered. Skipped: already here, active in another campaign,
    closed or already contacted. Each new lead gets an A/B style (balanced, at random)."""
    return await service.add_leads(db, ctx.workspace.id, ctx.user.id, campaign_id, body)


@router.delete("/campaigns/{campaign_id}/leads/{lead_id}", response_model=CampaignOut)
async def remove_lead(campaign_id: int, lead_id: int, ctx: WriteAuth, db: DbSession) -> CampaignOut:
    """Its queued message (if any) is cancelled; the offer stays approved."""
    return await service.remove_lead(db, ctx.workspace.id, ctx.user.id, campaign_id, lead_id)


@router.post("/campaigns/{campaign_id}/offers/estimate", response_model=CampaignGenerationEstimate)
async def estimate_offers(
    campaign_id: int, ctx: ReadAuth, db: DbSession, settings: AppSettings
) -> CampaignGenerationEstimate:
    """Which leads get an offer now (one draft each in its A/B style), why others are skipped, price, budgets
    and the ``confirm_token``. Nothing is generated or charged."""
    return await service.estimate_generation(db, settings, ctx.workspace.id, campaign_id)


@router.post(
    "/campaigns/{campaign_id}/offers",
    response_model=CampaignGenerationResult,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_offers(
    campaign_id: int, body: CampaignGenerationStart, ctx: WriteAuth, db: DbSession, settings: AppSettings
) -> CampaignGenerationResult:
    """Queue exactly the confirmed estimate (409 ``confirmation_required`` with a new token otherwise). The
    drafts land in the Approval Queue."""
    return await service.start_generation(db, settings, ctx.workspace.id, ctx.user.id, campaign_id, body)


@router.get("/campaigns/{campaign_id}/stats", response_model=CampaignStats)
async def stats(campaign_id: int, ctx: ReadAuth, db: DbSession) -> CampaignStats:
    """A/B counts per style (ТЗ §28). No conclusions: see ``note`` and ``small_sample``."""
    return await service.campaign_stats(db, ctx.workspace.id, campaign_id)


@router.post("/campaigns/{campaign_id}/start", response_model=CampaignOut)
async def start(campaign_id: int, ctx: SendAuth, db: DbSession) -> CampaignOut:
    """Launch: from now on every offer of the campaign a human approves is queued and sent within the
    window and limits. 409 ``campaign_not_ready`` with ``issues``."""
    return await service.start_campaign(db, ctx.workspace.id, ctx.user.id, campaign_id)


@router.post("/campaigns/{campaign_id}/pause", response_model=CampaignOut)
async def pause(campaign_id: int, ctx: WriteAuth, db: DbSession) -> CampaignOut:
    """Queued messages wait (nothing is cancelled)."""
    return await service.pause_campaign(db, ctx.workspace.id, ctx.user.id, campaign_id)


@router.post("/campaigns/{campaign_id}/resume", response_model=CampaignOut)
async def resume(campaign_id: int, ctx: SendAuth, db: DbSession) -> CampaignOut:
    return await service.resume_campaign(db, ctx.workspace.id, ctx.user.id, campaign_id)


@router.post("/campaigns/{campaign_id}/complete", response_model=CampaignOut)
async def complete(campaign_id: int, ctx: WriteAuth, db: DbSession) -> CampaignOut:
    """Queued messages are cancelled (offers stay approved); the remaining leads are released."""
    return await service.complete_campaign(db, ctx.workspace.id, ctx.user.id, campaign_id)


@router.post("/campaigns/{campaign_id}/archive", response_model=CampaignOut)
async def archive(campaign_id: int, ctx: WriteAuth, db: DbSession) -> CampaignOut:
    return await service.archive_campaign(db, ctx.workspace.id, ctx.user.id, campaign_id)
