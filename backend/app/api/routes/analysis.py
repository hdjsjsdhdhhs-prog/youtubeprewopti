from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import AppSettings, DbSession, ReadAuth
from app.domains.analysis import service
from app.domains.analysis.schemas import (
    AnalysisEstimate,
    AnalysisEstimateRequest,
    PrefilterPreview,
    ThumbnailPrefilter,
)

router = APIRouter(prefix="/projects/{project_id}", tags=["analysis"])


@router.post("/prefilter/preview", response_model=PrefilterPreview)
async def preview_prefilter(
    project_id: int, ctx: ReadAuth, db: DbSession, body: ThumbnailPrefilter | None = None
) -> PrefilterPreview:
    """What the prefilter selects (body = unsaved settings; empty body = the project's saved ones).
    Read-only: nothing is stored."""
    return await service.preview_prefilter(db, ctx.workspace.id, project_id, body)


@router.post("/thumbnail-analysis/estimate", response_model=AnalysisEstimate)
async def estimate_thumbnail_analysis(
    project_id: int, body: AnalysisEstimateRequest, ctx: ReadAuth, db: DbSession, settings: AppSettings
) -> AnalysisEstimate:
    """Pre-flight cost estimate of an AI thumbnail audit (budget gate, §76): selection, model, price per
    item, budget checks and the ``confirm_token`` that starting the run must echo. Nothing is charged."""
    return await service.estimate_thumbnail_analysis(db, settings, ctx.workspace.id, project_id, body)
