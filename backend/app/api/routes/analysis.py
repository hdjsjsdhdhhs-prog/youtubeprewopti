from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import AppSettings, DbSession, Paging, ReadAuth, WriteAuth
from app.domains.analysis import channel_service, service
from app.domains.analysis.audit import Level, ProblemCode
from app.domains.analysis.schemas import (
    AnalysisEstimate,
    AnalysisEstimateRequest,
    AnalysisSort,
    AnalysisStartRequest,
    AnalysisStartResult,
    ChannelAnalysisEstimate,
    ChannelAnalysisList,
    ChannelAnalysisRequest,
    ChannelAnalysisSort,
    ChannelAnalysisStartRequest,
    ChannelVisualReport,
    PrefilterPreview,
    ThumbnailAnalysisList,
    ThumbnailPrefilter,
)
from app.domains.youtube.schemas import SortOrder

router = APIRouter(prefix="/projects/{project_id}", tags=["analysis"])
channel_router = APIRouter(prefix="/channels", tags=["analysis"])


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


@router.post(
    "/thumbnail-analysis", response_model=AnalysisStartResult, status_code=status.HTTP_202_ACCEPTED
)
async def start_thumbnail_analysis(
    project_id: int, body: AnalysisStartRequest, ctx: WriteAuth, db: DbSession, settings: AppSettings
) -> AnalysisStartResult:
    """Queue the AI thumbnail audit of the estimate whose ``confirm_token`` is echoed. The estimate is
    recomputed: if anything changed, 409 ``confirmation_required`` returns the new token and nothing starts.
    Already analysed images are not sent again (cache by image, prompt version and model)."""
    return await service.start_thumbnail_analysis(
        db, settings, ctx.workspace.id, ctx.user.id, project_id, body
    )


@router.post("/channel-analysis/estimate", response_model=ChannelAnalysisEstimate)
async def estimate_channel_analysis(
    project_id: int, body: ChannelAnalysisRequest, ctx: ReadAuth, db: DbSession, settings: AppSettings
) -> ChannelAnalysisEstimate:
    """Pre-flight estimate of the comparative AI audit of the project's channels (§17, §76). A channel
    needs at least 3 audited thumbnails; channels whose exact inputs are already audited are free."""
    return await channel_service.estimate_channel_analysis(db, settings, ctx.workspace.id, project_id, body)


@router.post(
    "/channel-analysis", response_model=AnalysisStartResult, status_code=status.HTTP_202_ACCEPTED
)
async def start_channel_analysis(
    project_id: int, body: ChannelAnalysisStartRequest, ctx: WriteAuth, db: DbSession, settings: AppSettings
) -> AnalysisStartResult:
    """Queue the channel audit of the estimate whose ``confirm_token`` is echoed (changed => 409
    ``confirmation_required`` with the new token, nothing starts)."""
    return await channel_service.start_channel_analysis(
        db, settings, ctx.workspace.id, ctx.user.id, project_id, body
    )


@router.get("/channel-analyses", response_model=ChannelAnalysisList)
async def list_channel_analyses(
    project_id: int,
    ctx: ReadAuth,
    db: DbSession,
    page: Paging,
    sort: ChannelAnalysisSort = ChannelAnalysisSort.AVG_SCORE,
    order: SortOrder = SortOrder.ASC,
    opportunity: Level | None = None,
    problem: ProblemCode | None = None,
) -> ChannelAnalysisList:
    """Channels of the project with a current channel audit; aggregates use the workspace's thumbnail
    weights. Default order: lowest average thumbnail quality first."""
    return await channel_service.list_channel_analyses(
        db, ctx.workspace.id, project_id, sort=sort, order=order, opportunity=opportunity, problem=problem,
        limit=page.limit, offset=page.offset,
    )


@channel_router.get("/{channel_id}/visual-audit", response_model=ChannelVisualReport)
async def get_channel_visual_audit(channel_id: int, ctx: ReadAuth, db: DbSession) -> ChannelVisualReport:
    """Current comparative audit of the channel with code-computed aggregates (``analysis: null`` = not
    audited yet)."""
    return await channel_service.channel_visual_report(db, ctx.workspace.id, channel_id)


@router.get("/thumbnail-analyses", response_model=ThumbnailAnalysisList)
async def list_thumbnail_analyses(
    project_id: int,
    ctx: ReadAuth,
    db: DbSession,
    page: Paging,
    sort: AnalysisSort = AnalysisSort.OVERALL,
    order: SortOrder = SortOrder.ASC,
    opportunity: Level | None = None,
    problem: ProblemCode | None = None,
    max_overall: Annotated[Decimal | None, Query(ge=1, le=10)] = None,
    channel_id: int | None = None,
) -> ThumbnailAnalysisList:
    """Videos of the project with a current AI thumbnail audit. Default order: weakest ``overall_score``
    first (best redesign candidates). ``summary`` covers all analysed videos of the project."""
    return await service.list_thumbnail_analyses(
        db, ctx.workspace.id, project_id, sort=sort, order=order, opportunity=opportunity, problem=problem,
        max_overall=max_overall, channel_id=channel_id, limit=page.limit, offset=page.offset,
    )
