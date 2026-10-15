"""Human score corrections (ТЗ §47) and «AI vs human» analytics (§46), Phase 4."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import DbSession, ReadAuth, WriteAuth
from app.domains.feedback import service
from app.domains.feedback.models import ScoreTarget
from app.domains.feedback.schemas import (
    FeedbackSummary,
    OverrideRemoved,
    ScoreOverrideIn,
    ScoreOverrideOut,
    ScoreOverrideSaved,
)

router = APIRouter(prefix="/score-overrides", tags=["feedback"])
summary_router = APIRouter(prefix="/feedback", tags=["feedback"])


@router.get("", response_model=list[ScoreOverrideOut])
async def list_overrides(
    target_type: ScoreTarget,
    ctx: ReadAuth,
    db: DbSession,
    target_id: Annotated[list[int], Query(min_length=1, max_length=500)],
) -> list[ScoreOverrideOut]:
    return await service.list_overrides(db, ctx.workspace.id, target_type, target_id)


@router.put("", response_model=ScoreOverrideSaved)
async def set_override(body: ScoreOverrideIn, ctx: WriteAuth, db: DbSession) -> ScoreOverrideSaved:
    """Set or change the human value of one score; the AI / code value is kept (``ai_value`` is taken by
    the server). A human thumbnail ``overall_score`` / ``redesign_potential`` or Lead ``total`` /
    ``priority_score`` rescores the affected leads (``rescored``)."""
    return await service.set_override(db, ctx.workspace.id, ctx.user.id, body)


@router.delete("/{override_id}", response_model=OverrideRemoved)
async def remove_override(override_id: int, ctx: WriteAuth, db: DbSession) -> OverrideRemoved:
    """Back to the AI / code value (logged as feedback)."""
    return await service.remove_override(db, ctx.workspace.id, ctx.user.id, override_id)


@summary_router.get("/summary", response_model=FeedbackSummary)
async def feedback_summary(ctx: ReadAuth, db: DbSession) -> FeedbackSummary:
    """Where the AI over- / underestimates (current human values vs the AI values they replaced) and the
    manual lead decisions with the average Lead Score the code gave those leads."""
    return await service.summary(db, ctx.workspace.id)
