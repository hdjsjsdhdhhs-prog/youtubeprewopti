from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import DbSession, Permission, ReadAuth, require
from app.domains.analysis import scoring
from app.domains.analysis.schemas import ScoringProfileOut, ThumbnailWeights
from app.domains.identity.service import AuthContext

router = APIRouter(prefix="/scoring-profiles", tags=["analysis"])
AdminAuth = Annotated[AuthContext, Depends(require(Permission.MANAGE_INTEGRATIONS))]


@router.get("/thumbnail", response_model=ScoringProfileOut)
async def get_thumbnail_profile(ctx: ReadAuth, db: DbSession) -> ScoringProfileOut:
    """Active weights of the thumbnail ``overall_score`` (version 0 = built-in defaults)."""
    return scoring.profile_out(await scoring.active_thumbnail_weights(db, ctx.workspace.id))


@router.put("/thumbnail", response_model=ScoringProfileOut)
async def set_thumbnail_profile(body: ThumbnailWeights, ctx: AdminAuth, db: DbSession) -> ScoringProfileOut:
    """Save new weights as the next version (previous versions are kept). Stored audits are not
    re-run: ``overall_score`` is recomputed from their subscores on every read."""
    return await scoring.set_thumbnail_weights(db, ctx.workspace.id, ctx.user.id, body)
