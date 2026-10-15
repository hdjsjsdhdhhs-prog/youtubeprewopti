from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import DbSession, Permission, ReadAuth, require
from app.domains.analysis import scoring
from app.domains.analysis.schemas import ScoringProfileOut, ThumbnailWeights
from app.domains.contacts import service as contacts
from app.domains.contacts.schemas import ContactabilityProfileOut, ContactabilityProfileSaved
from app.domains.contacts.scoring import ContactabilityWeights
from app.domains.identity.service import AuthContext
from app.domains.leads import service as leads
from app.domains.leads.schemas import (
    LeadProfileOut,
    LeadProfileSaved,
    LeadWeights,
    PriorityProfileOut,
    PriorityProfileSaved,
    PriorityWeights,
)

router = APIRouter(prefix="/scoring-profiles", tags=["analysis"])
AdminAuth = Annotated[AuthContext, Depends(require(Permission.MANAGE_INTEGRATIONS))]


@router.get("/thumbnail", response_model=ScoringProfileOut)
async def get_thumbnail_profile(ctx: ReadAuth, db: DbSession) -> ScoringProfileOut:
    """Active weights of the thumbnail ``overall_score`` (version 0 = built-in defaults)."""
    return scoring.profile_out(await scoring.active_thumbnail_weights(db, ctx.workspace.id))


@router.put("/thumbnail", response_model=ScoringProfileOut)
async def set_thumbnail_profile(body: ThumbnailWeights, ctx: AdminAuth, db: DbSession) -> ScoringProfileOut:
    """Save new weights as the next version (previous versions are kept). Stored audits are not
    re-run: ``overall_score`` is recomputed from their subscores on every read. Lead scores (which use the
    average thumbnail score) are recomputed."""
    out = await scoring.set_thumbnail_weights(db, ctx.workspace.id, ctx.user.id, body)
    await leads.sync_leads(db, ctx.workspace.id)
    return out


@router.get("/lead", response_model=LeadProfileOut)
async def get_lead_profile(ctx: ReadAuth, db: DbSession) -> LeadProfileOut:
    """Lead Score: maximum points per component and the niche value table (version 0 = defaults)."""
    return leads.lead_profile_out(await leads.active_lead_weights(db, ctx.workspace.id))


@router.put("/lead", response_model=LeadProfileSaved)
async def set_lead_profile(body: LeadWeights, ctx: AdminAuth, db: DbSession) -> LeadProfileSaved:
    """Save as the next version and rescore all leads of the workspace (deterministic, no AI)."""
    return await leads.set_lead_weights(db, ctx.workspace.id, ctx.user.id, body)


@router.get("/priority", response_model=PriorityProfileOut)
async def get_priority_profile(ctx: ReadAuth, db: DbSession) -> PriorityProfileOut:
    """Priority Score (§53) weights over the same components."""
    return leads.priority_profile_out(await leads.active_priority_weights(db, ctx.workspace.id))


@router.put("/priority", response_model=PriorityProfileSaved)
async def set_priority_profile(body: PriorityWeights, ctx: AdminAuth, db: DbSession) -> PriorityProfileSaved:
    return await leads.set_priority_weights(db, ctx.workspace.id, ctx.user.id, body)


@router.get("/contactability", response_model=ContactabilityProfileOut)
async def get_contactability_profile(ctx: ReadAuth, db: DbSession) -> ContactabilityProfileOut:
    """Contactability Score (§19): points per available contact type, capped at 100 (version 0 = defaults)."""
    return contacts.contact_profile_out(await contacts.active_contact_weights(db, ctx.workspace.id))


@router.put("/contactability", response_model=ContactabilityProfileSaved)
async def set_contactability_profile(
    body: ContactabilityWeights, ctx: AdminAuth, db: DbSession
) -> ContactabilityProfileSaved:
    """Save as the next version and rescore all leads of the workspace."""
    return await contacts.set_contact_weights(db, ctx.workspace.id, ctx.user.id, body)
