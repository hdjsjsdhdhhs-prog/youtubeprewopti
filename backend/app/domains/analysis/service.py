"""Prefilter (which thumbnails go to AI), the budget gate's pre-flight estimate, starting the confirmed
AI audit and reading its results (AI_PIPELINE).

Order of the pipeline: ingestion (download + deterministic metrics, free) → **prefilter** (SQL over
channels, videos and ``image_metrics``) → **estimate + confirmation** → AI audit (``pipeline.py``).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError
from app.domains.ai import budget
from app.domains.ai.cost import estimate_tokens, pricing_known, token_cost
from app.domains.ai.models import PromptTemplate
from app.domains.ai.registry import TASKS, AITask, resolve_models
from app.domains.ai.runner import AINotConfiguredError
from app.domains.analysis import scoring
from app.domains.analysis.audit import Level, ProblemCode
from app.domains.analysis.models import AILevel, ThumbnailAnalysis
from app.domains.analysis.pipeline import THUMBNAIL_PROMPT
from app.domains.analysis.schemas import (
    AnalysisEstimate,
    AnalysisEstimateRequest,
    AnalysisSort,
    AnalysisStartRequest,
    AnalysisStartResult,
    AnalysisSummary,
    AnalyzedVideoOut,
    BudgetCheckOut,
    MetricBounds,
    PrefilterPreview,
    ProblemCount,
    ThumbnailAnalysisList,
    ThumbnailAnalysisOut,
    ThumbnailPrefilter,
)
from app.domains.identity.service import audit
from app.domains.jobs.schemas import JobRunOut
from app.domains.jobs.service import JobQueue, JobType, enqueue_job
from app.domains.media.imaging import METRICS_ALGO_VERSION
from app.domains.media.models import FetchStatus, ImageMetrics, Thumbnail
from app.domains.projects.models import ProjectChannel, ProjectStatus, SearchProject
from app.domains.projects.service import get_project
from app.domains.youtube.models import Channel, ChannelMetrics, Video
from app.domains.youtube.schemas import ChannelFilterSet, SortOrder
from app.domains.youtube.service import channel_filter_conditions

SAMPLE_SIZE = 24


@dataclass(frozen=True)
class Selection:
    preview: PrefilterPreview
    asset_ids: list[int]  # distinct image assets to analyse (identical thumbnails are analysed once)


def project_prefilter(project: SearchProject) -> ThumbnailPrefilter:
    return ThumbnailPrefilter.model_validate(project.prefilter_settings or {})


def _metric_conditions(bounds: MetricBounds) -> list[ColumnElement[bool]]:
    conds: list[ColumnElement[bool]] = []
    for name in MetricBounds.model_fields:
        rng = getattr(bounds, name)
        if rng is None:
            continue
        column = getattr(ImageMetrics, name)
        if rng.min is not None:
            conds.append(column >= rng.min)
        if rng.max is not None:
            conds.append(column <= rng.max)
    return conds


async def select_thumbnails(
    db: AsyncSession, workspace_id: int, project: SearchProject, pf: ThumbnailPrefilter
) -> Selection:
    in_project = and_(ProjectChannel.project_id == project.id, ProjectChannel.channel_id == Channel.id)
    channel_conds: list[ColumnElement[bool]] = []
    if pf.apply_channel_filters and project.filter_settings:
        filters = ChannelFilterSet.model_validate(project.filter_settings)
        channel_conds = await channel_filter_conditions(db, workspace_id, project.id, filters)
    matched = (
        select(Channel.id)
        .join(ProjectChannel, in_project)
        .outerjoin(ChannelMetrics, ChannelMetrics.channel_id == Channel.id)
        .where(*channel_conds)
    )
    channels_in_project = await db.scalar(
        select(func.count()).select_from(ProjectChannel).where(ProjectChannel.project_id == project.id)
    ) or 0
    channels_matched = await db.scalar(select(func.count()).select_from(matched.subquery())) or 0

    video_conds: list[ColumnElement[bool]] = [Video.channel_id.in_(matched.scalar_subquery())]
    if pf.exclude_shorts:
        video_conds.append(Video.is_short.is_not(True))
    if pf.max_video_age_days is not None:
        video_conds.append(Video.published_at >= func.now() - timedelta(days=pf.max_video_age_days))
    if pf.min_video_views is not None:
        video_conds.append(Video.view_count >= pf.min_video_views)
    rank = func.row_number().over(
        partition_by=Video.channel_id, order_by=(Video.published_at.desc().nulls_last(), Video.id.desc())
    )
    ranked = select(Video.id.label("video_id"), Video.published_at, rank.label("rn")).where(*video_conds)
    considered = ranked.subquery()

    ready = and_(
        Thumbnail.fetch_status == FetchStatus.OK,
        Thumbnail.image_asset_id.is_not(None),
        ImageMetrics.image_asset_id.is_not(None),
    )
    metric_ok = and_(ready, *_metric_conditions(pf.metrics))
    base = (
        select(considered.c.video_id, considered.c.published_at, Thumbnail.image_asset_id)
        .select_from(considered)
        .outerjoin(Thumbnail, Thumbnail.video_id == considered.c.video_id)
        .outerjoin(
            ImageMetrics,
            and_(
                ImageMetrics.image_asset_id == Thumbnail.image_asset_id,
                ImageMetrics.algo_version == METRICS_ALGO_VERSION,
            ),
        )
        .where(considered.c.rn <= pf.videos_per_channel)
    )
    # Asset ids of the selection are needed anyway (estimate, confirmation) => counts are taken in Python.
    rows = (
        await db.execute(
            base.add_columns(ready.label("ready"), metric_ok.label("ok"))
            .order_by(considered.c.published_at.desc().nulls_last(), considered.c.video_id.desc())
        )
    ).all()
    selected = [r for r in rows if r.ok]
    not_ready = sum(1 for r in rows if not r.ready)
    preview = PrefilterPreview(
        channels_in_project=channels_in_project,
        channels_matched=channels_matched,
        videos_considered=len(rows),
        thumbnails_not_ready=not_ready,
        excluded_by_metrics=len(rows) - not_ready - len(selected),
        thumbnails_selected=len(selected),
        sample_video_ids=[r.video_id for r in selected[:SAMPLE_SIZE]],
    )
    return Selection(preview=preview, asset_ids=sorted({r.image_asset_id for r in selected}))


async def preview_prefilter(
    db: AsyncSession, workspace_id: int, project_id: int, pf: ThumbnailPrefilter | None
) -> PrefilterPreview:
    project = await get_project(db, workspace_id, project_id)
    return (await select_thumbnails(db, workspace_id, project, pf or project_prefilter(project))).preview


def _ids_digest(ids: list[int]) -> str:
    return hashlib.sha256(",".join(map(str, ids)).encode("ascii")).hexdigest()


async def analyzed_assets(db: AsyncSession, asset_ids: list[int], model_keys: list[str]) -> set[int]:
    """Assets of ``asset_ids`` that already have an audit for the current prompt version and one of
    ``model_keys`` (the cache the job would hit — they cost nothing)."""
    if not asset_ids or not model_keys:
        return set()
    name, version = THUMBNAIL_PROMPT
    return set(
        await db.scalars(
            select(ThumbnailAnalysis.image_asset_id)
            .join(PromptTemplate, PromptTemplate.id == ThumbnailAnalysis.prompt_template_id)
            .where(
                PromptTemplate.name == name, PromptTemplate.version == version,
                ThumbnailAnalysis.model_key.in_(model_keys),
                ThumbnailAnalysis.image_asset_id.in_(asset_ids),
            )
            .distinct()
        )
    )


async def budget_checks(
    db: AsyncSession, workspace_id: int, project_id: int, task: AITask, total: Decimal | None, items: int
) -> list[BudgetCheckOut]:
    """Would ``items`` AI calls costing ``total`` exceed any applicable budget (pre-flight view, §76)?"""
    checks: list[BudgetCheckOut] = []
    for b in await budget.applicable_budgets(db, workspace_id, project_id=project_id, task=task.value):
        check = budget.check_usage(await budget.budget_usage(db, b), cost=total, operations=items)
        checks.append(BudgetCheckOut(
            budget_id=b.id, description=budget.describe_budget(b), limit_usd=b.limit_usd,
            max_ai_operations=b.max_ai_operations, spent_usd=check.usage.spent_usd,
            operations=check.usage.operations, would_exceed=check.would_exceed, reason=check.reason,
        ))
    return checks


@dataclass(frozen=True)
class AnalysisPlan:
    estimate: AnalysisEstimate
    asset_ids: list[int]  # to analyse (sorted)
    token_payload: dict[str, Any]


async def plan_thumbnail_analysis(
    db: AsyncSession,
    settings: Settings,
    workspace_id: int,
    project: SearchProject,
    req: AnalysisEstimateRequest,
) -> AnalysisPlan:
    pf = req.prefilter or project_prefilter(project)
    selection = await select_thumbnails(db, workspace_id, project, pf)
    task = AITask.THUMBNAIL_ANALYSIS
    spec = TASKS[task]
    models = await resolve_models(db, settings, task)
    model = models[0] if models else None
    done = await analyzed_assets(db, selection.asset_ids, [m.key for m in models])
    todo = [a for a in selection.asset_ids if a not in done]
    items = len(todo)
    tokens_in, tokens_out = estimate_tokens(spec, 1, req.detail)
    per_item = token_cost(model, tokens_in, tokens_out) if model is not None else None
    total = per_item * items if per_item is not None else None

    checks = await budget_checks(db, workspace_id, project.id, task, total, items)

    token_payload: dict[str, Any] = {
        "project_id": project.id, "task": task.value, "assets": _ids_digest(todo),
        "model_key": model.key if model else None, "api_model_id": model.api_model_id if model else None,
        "detail": req.detail, "per_item": str(per_item) if per_item is not None else None,
        "prefilter": pf.model_dump(mode="json"),
        "prompt": f"{THUMBNAIL_PROMPT[0]}/v{THUMBNAIL_PROMPT[1]}",
    }
    estimate = AnalysisEstimate(
        task=task.value,
        preview=selection.preview,
        provider=model.provider if model else settings.effective_ai_provider,
        model_key=model.key if model else None,
        api_model_id=model.api_model_id if model else None,
        detail=req.detail,
        items=items,
        already_analyzed=len(selection.asset_ids) - items,
        cost_per_item_usd=per_item,
        estimated_cost_usd=total.quantize(Decimal("0.000001")) if total is not None else None,
        pricing_verified=bool(model and pricing_known(model) and model.pricing_verified_at is not None),
        estimated_input_tokens=tokens_in * items,
        estimated_output_tokens=tokens_out * items,
        budgets=checks,
        within_budgets=not any(c.would_exceed for c in checks),
        confirm_token=budget.confirmation_token(token_payload),
    )
    return AnalysisPlan(estimate=estimate, asset_ids=todo, token_payload=token_payload)


async def estimate_thumbnail_analysis(
    db: AsyncSession, settings: Settings, workspace_id: int, project_id: int, req: AnalysisEstimateRequest
) -> AnalysisEstimate:
    """Pre-flight estimate of an AI thumbnail audit (§76). Nothing is called or charged here."""
    project = await get_project(db, workspace_id, project_id)
    return (await plan_thumbnail_analysis(db, settings, workspace_id, project, req)).estimate


async def start_thumbnail_analysis(
    db: AsyncSession,
    settings: Settings,
    workspace_id: int,
    actor_id: int,
    project_id: int,
    req: AnalysisStartRequest,
) -> AnalysisStartResult:
    """Queue the audit of exactly the confirmed estimate. The estimate is recomputed here: if the selection,
    model, price or detail changed since the user saw it, ``confirmation_required`` (409) carries the new
    token and nothing is started. Budgets are enforced again before every single AI call."""
    project = await get_project(db, workspace_id, project_id)
    if project.status == ProjectStatus.ARCHIVED:
        raise ConflictError("Project is archived; restore it to analyse thumbnails", code="project_archived")
    plan = await plan_thumbnail_analysis(
        db, settings, workspace_id, project,
        AnalysisEstimateRequest(prefilter=req.prefilter, detail=req.detail),
    )
    est = plan.estimate
    if est.model_key is None:
        raise AINotConfiguredError(
            "AI provider is not configured: set YTL_VIBECODE_API_KEY (or YTL_OPENAI_API_KEY, or "
            "YTL_AI_PROVIDER=mock for demo results)."
        )
    budget.require_confirmation(req.confirm_token, plan.token_payload)
    if not plan.asset_ids:
        return AnalysisStartResult(
            job=None, created=False, items=0, already_analyzed=est.already_analyzed,
            estimated_cost_usd=Decimal(0),
        )
    enq = await enqueue_job(
        db,
        type_=JobType.THUMBNAIL_ANALYSIS,
        queue=JobQueue.BULK,
        params={
            "project_id": project.id, "asset_ids": plan.asset_ids, "detail": req.detail,
            "model_key": est.model_key,
        },
        workspace_id=workspace_id,
        created_by=actor_id,
        progress_total=len(plan.asset_ids),
    )
    if enq.created:
        cost = est.estimated_cost_usd
        enq.job.estimated_cost_usd = cost
        await db.flush()
        await audit(
            db, "job.enqueued", workspace_id=workspace_id, actor_user_id=actor_id, entity_type="job_run",
            entity_id=enq.job.id,
            diff={
                "type": enq.job.type, "project_id": project.id, "images": len(plan.asset_ids),
                "model_key": est.model_key, "detail": req.detail,
                "estimated_cost_usd": str(cost) if cost is not None else None,
                "confirm_token": req.confirm_token,
            },
        )
    return AnalysisStartResult(
        job=JobRunOut.model_validate(enq.job), created=enq.created, items=len(plan.asset_ids),
        already_analyzed=est.already_analyzed, estimated_cost_usd=est.estimated_cost_usd,
    )


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
TOP_PROBLEMS = 8


def _project_joins(stmt: Select[Any], project_id: int) -> Select[Any]:
    """Videos of the project's channels whose thumbnail has a current analysis."""
    return (
        stmt.select_from(Video)
        .join(Thumbnail, Thumbnail.video_id == Video.id)
        .join(
            ThumbnailAnalysis,
            and_(ThumbnailAnalysis.image_asset_id == Thumbnail.image_asset_id, ThumbnailAnalysis.is_current),
        )
        .join(Channel, Channel.id == Video.channel_id)
        .join(
            ProjectChannel,
            and_(ProjectChannel.channel_id == Channel.id, ProjectChannel.project_id == project_id),
        )
    )


def _project_analyses(project_id: int) -> Select[Any]:
    return _project_joins(select(Video, Channel.title.label("channel_title"), ThumbnailAnalysis), project_id)


async def _summary(db: AsyncSession, project_id: int, overall: ColumnElement[Any]) -> AnalysisSummary:
    """Over everything analysed in the project (list filters do not apply)."""
    rows = _project_joins(
        select(
            overall.label("overall"), ThumbnailAnalysis.improvement_opportunity.label("opportunity"),
            ThumbnailAnalysis.provider.label("provider"), ThumbnailAnalysis.problem_codes.label("codes"),
        ),
        project_id,
    ).subquery()
    head = (
        await db.execute(
            select(func.count(), func.avg(rows.c.overall), func.count().filter(rows.c.provider == "mock"))
        )
    ).one()
    opportunity = await db.execute(select(rows.c.opportunity, func.count()).group_by(rows.c.opportunity))
    codes = select(func.unnest(rows.c.codes).label("code")).subquery()
    problems = await db.execute(
        select(codes.c.code, func.count())
        .group_by(codes.c.code)
        .order_by(func.count().desc(), codes.c.code)
        .limit(TOP_PROBLEMS)
    )
    return AnalysisSummary(
        videos_analyzed=int(head[0]),
        avg_overall=Decimal(head[1]).quantize(Decimal("0.01")) if head[1] is not None else None,
        opportunity={AILevel(k).value: int(n) for k, n in opportunity.all()},
        top_problems=[ProblemCount(code=c, count=int(n)) for c, n in problems.all()],
        mock_results=int(head[2]),
    )


async def list_thumbnail_analyses(
    db: AsyncSession,
    workspace_id: int,
    project_id: int,
    *,
    sort: AnalysisSort,
    order: SortOrder,
    opportunity: Level | None,
    problem: ProblemCode | None,
    max_overall: Decimal | None,
    channel_id: int | None,
    limit: int,
    offset: int,
) -> ThumbnailAnalysisList:
    """Videos of the project with a current thumbnail audit; ``overall_score`` uses the workspace's
    active weights (computed in SQL, so it sorts and filters)."""
    await get_project(db, workspace_id, project_id)
    active = await scoring.active_thumbnail_weights(db, workspace_id)
    overall = scoring.overall_sql(active.weights)

    conds: list[ColumnElement[bool]] = []
    if opportunity is not None:
        conds.append(ThumbnailAnalysis.improvement_opportunity == AILevel(opportunity))
    if problem is not None:
        conds.append(ThumbnailAnalysis.problem_codes.contains([problem.value]))  # @> uses the GIN index
    if max_overall is not None:
        conds.append(overall <= max_overall)
    if channel_id is not None:
        conds.append(Video.channel_id == channel_id)
    filtered = _project_analyses(project_id).where(*conds)
    total = await db.scalar(select(func.count()).select_from(filtered.subquery())) or 0

    sort_col: Any = {
        AnalysisSort.OVERALL: overall,
        AnalysisSort.REDESIGN_POTENTIAL: ThumbnailAnalysis.redesign_potential,
        AnalysisSort.PUBLISHED_AT: Video.published_at,
        AnalysisSort.VIEWS: Video.view_count,
    }[sort]
    ordered = sort_col.asc().nulls_last() if order == SortOrder.ASC else sort_col.desc().nulls_last()
    rows = await db.execute(
        filtered.add_columns(overall.label("overall")).order_by(ordered, Video.id).limit(limit).offset(offset)
    )
    fields = [k for k in ThumbnailAnalysisOut.model_fields if k != "overall_score"]
    items = [
        AnalyzedVideoOut(
            video_id=video.id, youtube_video_id=video.youtube_video_id, title=video.title,
            published_at=video.published_at, view_count=video.view_count, channel_id=video.channel_id,
            channel_title=channel_title,
            analysis=ThumbnailAnalysisOut(
                **{k: getattr(analysis, k) for k in fields}, overall_score=Decimal(value)
            ),
        )
        for video, channel_title, analysis, value in rows.all()
    ]
    return ThumbnailAnalysisList(
        items=items, total=total, limit=limit, offset=offset,
        summary=await _summary(db, project_id, overall), scoring=scoring.profile_out(active),
    )
