"""Leads: list with ТЗ §9 filters, card, sync/rescore, Ideal Lead Profiles (§52) — Phase 3.7; manual
statuses and bulk actions (§10–11) — Phase 4."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import DbSession, Paging, ReadAuth, WriteAuth
from app.core.state_machine import LeadStatus
from app.domains.analysis.audit import Level
from app.domains.leads import service, workflow
from app.domains.leads.schemas import (
    IdealLeadProfileIn,
    IdealLeadProfileOut,
    IdealLeadProfilePatch,
    IdealLeadProfileSaved,
    LeadBulkRequest,
    LeadBulkResult,
    LeadDetail,
    LeadFilters,
    LeadList,
    LeadSort,
    LeadStatusChange,
    LeadSyncRequest,
    RescoreResult,
)
from app.domains.leads.scoring import ActivityLevel
from app.domains.projects.service import get_project
from app.domains.youtube.schemas import SortOrder

router = APIRouter(prefix="/leads", tags=["leads"])
ideal_router = APIRouter(prefix="/ideal-lead-profiles", tags=["leads"])

NonNegQ = Annotated[int | None, Query(ge=0)]
Thumb = Annotated[Decimal | None, Query(ge=1, le=10)]
Pct = Annotated[Decimal | None, Query(ge=0, le=100)]


@router.get("", response_model=LeadList)
async def list_leads(
    ctx: ReadAuth,
    db: DbSession,
    page: Paging,
    project_id: int | None = None,
    channel_id: int | None = None,
    lead_status: Annotated[list[LeadStatus] | None, Query(alias="status", max_length=16)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    min_thumbnail_score: Thumb = None,
    max_thumbnail_score: Thumb = None,
    min_lead_score: Pct = None,
    max_lead_score: Pct = None,
    min_priority: Pct = None,
    min_subscribers: NonNegQ = None,
    max_subscribers: NonNegQ = None,
    min_avg_views: NonNegQ = None,
    activity: ActivityLevel | None = None,
    max_days_since_last_upload: NonNegQ = None,
    visual_opportunity: Level | None = None,
    niche: Annotated[list[int] | None, Query(max_length=50)] = None,
    tag: Annotated[list[int] | None, Query(max_length=50)] = None,
    favorite: bool | None = None,
    sort: LeadSort = LeadSort.LEAD_SCORE,
    order: SortOrder = SortOrder.DESC,
) -> LeadList:
    """Leads of the workspace; all filters are optional and AND-ed (§9). ``activity`` = at least this level
    (high: last video ≤ 14 days and ≥ 4 videos in 30 days; medium: last video ≤ 30 days). ``tag`` = any of
    these tags; ``favorite`` = the channel is (``true``) / is not (``false``) a favorite of the current
    user. Lead Score / Priority sort and filter by the human value where one is set (§47). Default order:
    highest Lead Score first."""
    filters = LeadFilters(
        project_id=project_id,
        channel_id=channel_id,
        status=lead_status or [],
        q=q,
        min_thumbnail_score=min_thumbnail_score,
        max_thumbnail_score=max_thumbnail_score,
        min_lead_score=min_lead_score,
        max_lead_score=max_lead_score,
        min_priority=min_priority,
        min_subscribers=min_subscribers,
        max_subscribers=max_subscribers,
        min_avg_views=min_avg_views,
        activity=activity,
        max_days_since_last_upload=max_days_since_last_upload,
        visual_opportunity=visual_opportunity,
        niche=niche or [],
        tag=tag or [],
        favorite=favorite,
    )
    return await service.list_leads(
        db, ctx.workspace.id, ctx.user.id, filters, sort=sort, order=order, limit=page.limit,
        offset=page.offset,
    )


@router.post("/bulk", response_model=LeadBulkResult)
async def bulk_action(body: LeadBulkRequest, ctx: WriteAuth, db: DbSession) -> LeadBulkResult:
    """One action on a selection (§11): ``selection.lead_ids`` (chosen rows) or ``selection.filters`` — the
    same filters as ``GET /api/leads`` («all filtered») minus ``exclude_ids``; at most 5000 leads.
    Actions: ``set_status`` (shortlisted / selected / analyzed / rejected, checked by the lead state
    machine), ``add_tag`` / ``remove_tag`` (user tags), ``favorite`` / ``unfavorite`` (the leads' channels,
    for the current user). Leads the action cannot apply to are listed in ``failures``; the rest change."""
    return await workflow.bulk(db, ctx.workspace.id, ctx.user.id, body)


@router.post("/sync", response_model=RescoreResult)
async def sync_leads(body: LeadSyncRequest, ctx: WriteAuth, db: DbSession) -> RescoreResult:
    """Create leads for channels that have none, move DISCOVERED → ANALYZED when thumbnails are audited and
    recompute the scores (deterministic, no AI, free). Runs automatically after discovery and analysis
    jobs; this is the manual trigger."""
    if body.project_id is not None:
        await get_project(db, ctx.workspace.id, body.project_id)
    return await service.sync_leads(db, ctx.workspace.id, body.project_id)


@router.get("/{lead_id}", response_model=LeadDetail)
async def get_lead(lead_id: int, ctx: ReadAuth, db: DbSession) -> LeadDetail:
    return await service.get_lead(db, ctx.workspace.id, ctx.user.id, lead_id)


@router.post("/{lead_id}/status", response_model=LeadDetail)
async def change_lead_status(
    lead_id: int, body: LeadStatusChange, ctx: WriteAuth, db: DbSession
) -> LeadDetail:
    """Manual decision: shortlist, select, back to analyzed, reject (with a reason) or restore a rejected
    lead (→ shortlisted). Other statuses belong to generation / offers / outreach (400
    ``status_not_manual``); a transition the state machine forbids is 409 ``invalid_transition``. Setting
    the current status again changes nothing."""
    return await workflow.change_status(db, ctx.workspace.id, ctx.user.id, lead_id, body)


@ideal_router.get("", response_model=list[IdealLeadProfileOut])
async def list_ideal_profiles(ctx: ReadAuth, db: DbSession) -> list[IdealLeadProfileOut]:
    return await service.list_ideal_profiles(db, ctx.workspace.id)


@ideal_router.post("", response_model=IdealLeadProfileSaved, status_code=status.HTTP_201_CREATED)
async def create_ideal_profile(
    body: IdealLeadProfileIn, ctx: WriteAuth, db: DbSession
) -> IdealLeadProfileSaved:
    """``is_default`` (default true) makes it the profile Channel Fit uses; leads are rescored."""
    return await service.create_ideal_profile(db, ctx.workspace.id, ctx.user.id, body)


@ideal_router.patch("/{profile_id}", response_model=IdealLeadProfileSaved)
async def update_ideal_profile(
    profile_id: int, body: IdealLeadProfilePatch, ctx: WriteAuth, db: DbSession
) -> IdealLeadProfileSaved:
    return await service.update_ideal_profile(db, ctx.workspace.id, ctx.user.id, profile_id, body)


@ideal_router.delete("/{profile_id}", response_model=RescoreResult)
async def delete_ideal_profile(profile_id: int, ctx: WriteAuth, db: DbSession) -> RescoreResult:
    return await service.delete_ideal_profile(db, ctx.workspace.id, ctx.user.id, profile_id)
