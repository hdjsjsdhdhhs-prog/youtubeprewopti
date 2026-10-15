"""Comparative channel audit (Phase 3.6): pre-flight estimate, confirmed start and reading results.

Aggregates of a channel audit (average / median thumbnail quality, share of weak / medium / strong,
best / worst video) are computed in SQL on every read from the *current* thumbnail audits of the compared
videos with the workspace's active weights — the AI never returns them (AI_ARCHITECTURE §3).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, Numeric, Subquery, and_, any_, func, literal, select
from sqlalchemy.dialects.postgresql import aggregate_order_by, array_agg
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError
from app.domains.ai import budget
from app.domains.ai.cost import estimate_tokens, pricing_known, token_cost
from app.domains.ai.models import PromptTemplate
from app.domains.ai.registry import TASKS, resolve_models
from app.domains.ai.runner import AINotConfiguredError
from app.domains.analysis import scoring
from app.domains.analysis.audit import Level, ProblemCode
from app.domains.analysis.channel_audit import MIN_THUMBNAILS, STRONG_FROM, WEAK_BELOW
from app.domains.analysis.channel_pipeline import CHANNEL_PROMPT, TASK, inputs_digest, select_channel_inputs
from app.domains.analysis.models import AILevel, ChannelAnalysis, ThumbnailAnalysis
from app.domains.analysis.schemas import (
    AnalysisStartResult,
    AnalyzedChannelOut,
    ChannelAggregates,
    ChannelAnalysisEstimate,
    ChannelAnalysisList,
    ChannelAnalysisOut,
    ChannelAnalysisRequest,
    ChannelAnalysisSort,
    ChannelAnalysisStartRequest,
    ChannelAnalysisSummary,
    ChannelAuditVideo,
    ChannelSelectionPreview,
    ChannelVisualReport,
)
from app.domains.analysis.service import budget_checks
from app.domains.feedback.models import ScoreTarget
from app.domains.feedback.service import overrides_for
from app.domains.identity.service import audit
from app.domains.jobs.schemas import JobRunOut
from app.domains.jobs.service import JobQueue, JobType, enqueue_job
from app.domains.media.models import Thumbnail
from app.domains.projects.models import ProjectChannel, ProjectStatus, SearchProject
from app.domains.projects.service import get_project
from app.domains.youtube.models import Channel, Video
from app.domains.youtube.schemas import SortOrder
from app.domains.youtube.service import get_visible_channel

_Q2 = Decimal("0.01")
_Q1 = Decimal("0.1")


# ---------------------------------------------------------------------------
# Estimate and start
# ---------------------------------------------------------------------------
async def analyzed_channels(
    db: AsyncSession, digests: dict[int, str], model_keys: list[str]
) -> set[int]:
    """Channels of ``digests`` whose exact inputs are already audited (current prompt version, one of
    ``model_keys``) — the cache the job would hit, so they cost nothing."""
    if not digests or not model_keys:
        return set()
    name, version = CHANNEL_PROMPT
    rows = await db.execute(
        select(ChannelAnalysis.channel_id, ChannelAnalysis.inputs_digest)
        .join(PromptTemplate, PromptTemplate.id == ChannelAnalysis.prompt_template_id)
        .where(
            PromptTemplate.name == name, PromptTemplate.version == version,
            ChannelAnalysis.model_key.in_(model_keys),
            ChannelAnalysis.channel_id.in_(list(digests)),
        )
    )
    return {cid for cid, digest in rows.tuples() if digests.get(cid) == digest}


@dataclass(frozen=True)
class ChannelPlan:
    estimate: ChannelAnalysisEstimate
    channel_ids: list[int]  # to audit (sorted)
    token_payload: dict[str, Any]


async def plan_channel_analysis(
    db: AsyncSession,
    settings: Settings,
    workspace_id: int,
    project: SearchProject,
    req: ChannelAnalysisRequest,
) -> ChannelPlan:
    inputs = await select_channel_inputs(db, project.id, req.videos_per_channel)
    channels_in_project = await db.scalar(
        select(func.count()).select_from(ProjectChannel).where(ProjectChannel.project_id == project.id)
    ) or 0
    ready = {cid: videos for cid, videos in inputs.items() if len(videos) >= MIN_THUMBNAILS}
    preview = ChannelSelectionPreview(
        channels_in_project=channels_in_project,
        channels_ready=len(ready),
        channels_not_enough=len(inputs) - len(ready),
        channels_without_audits=channels_in_project - len(inputs),
        min_thumbnails=MIN_THUMBNAILS,
    )
    spec = TASKS[TASK]
    models = await resolve_models(db, settings, TASK)
    model = models[0] if models else None
    digests = {cid: inputs_digest(videos) for cid, videos in ready.items()}
    done = await analyzed_channels(db, digests, [m.key for m in models])
    todo = sorted(cid for cid in ready if cid not in done)
    images = sum(len(ready[cid]) for cid in todo)
    tokens_in = tokens_out = 0
    for cid in todo:
        t_in, t_out = estimate_tokens(spec, len(ready[cid]), req.detail)
        tokens_in, tokens_out = tokens_in + t_in, tokens_out + t_out
    total = token_cost(model, tokens_in, tokens_out) if model is not None else None
    if model is None:
        per_item = None
    elif todo:
        per_item = None if total is None else (total / len(todo)).quantize(Decimal("0.000001"))
    else:  # nothing to do: show the price of a full channel for reference
        per_item = token_cost(model, *estimate_tokens(spec, req.videos_per_channel, req.detail))
    checks = await budget_checks(db, workspace_id, project.id, TASK, total, len(todo))

    token_payload: dict[str, Any] = {
        "project_id": project.id, "task": TASK.value,
        "channels": hashlib.sha256(
            ";".join(f"{cid}:{digests[cid]}" for cid in todo).encode("ascii")
        ).hexdigest(),
        "model_key": model.key if model else None, "api_model_id": model.api_model_id if model else None,
        "detail": req.detail, "videos_per_channel": req.videos_per_channel,
        "cost": str(total) if total is not None else None,
        "prompt": f"{CHANNEL_PROMPT[0]}/v{CHANNEL_PROMPT[1]}",
    }
    estimate = ChannelAnalysisEstimate(
        task=TASK.value,
        preview=preview,
        provider=model.provider if model else settings.effective_ai_provider,
        model_key=model.key if model else None,
        api_model_id=model.api_model_id if model else None,
        detail=req.detail,
        videos_per_channel=req.videos_per_channel,
        items=len(todo),
        already_analyzed=len(ready) - len(todo),
        images=images,
        cost_per_item_usd=per_item,
        estimated_cost_usd=total,
        pricing_verified=bool(model and pricing_known(model) and model.pricing_verified_at is not None),
        estimated_input_tokens=tokens_in,
        estimated_output_tokens=tokens_out,
        budgets=checks,
        within_budgets=not any(c.would_exceed for c in checks),
        confirm_token=budget.confirmation_token(token_payload),
    )
    return ChannelPlan(estimate=estimate, channel_ids=todo, token_payload=token_payload)


async def estimate_channel_analysis(
    db: AsyncSession, settings: Settings, workspace_id: int, project_id: int, req: ChannelAnalysisRequest
) -> ChannelAnalysisEstimate:
    project = await get_project(db, workspace_id, project_id)
    return (await plan_channel_analysis(db, settings, workspace_id, project, req)).estimate


async def start_channel_analysis(
    db: AsyncSession,
    settings: Settings,
    workspace_id: int,
    actor_id: int,
    project_id: int,
    req: ChannelAnalysisStartRequest,
) -> AnalysisStartResult:
    """Queue the channel audit of exactly the confirmed estimate (recomputed here; changed => 409
    ``confirmation_required`` with the new token, nothing starts)."""
    project = await get_project(db, workspace_id, project_id)
    if project.status == ProjectStatus.ARCHIVED:
        raise ConflictError("Project is archived; restore it to analyse channels", code="project_archived")
    plan = await plan_channel_analysis(
        db, settings, workspace_id, project,
        ChannelAnalysisRequest(videos_per_channel=req.videos_per_channel, detail=req.detail),
    )
    est = plan.estimate
    if est.model_key is None:
        raise AINotConfiguredError(
            "AI provider is not configured: set YTL_VIBECODE_API_KEY (or YTL_OPENAI_API_KEY, or "
            "YTL_AI_PROVIDER=mock for demo results)."
        )
    budget.require_confirmation(req.confirm_token, plan.token_payload)
    if not plan.channel_ids:
        return AnalysisStartResult(
            job=None, created=False, items=0, already_analyzed=est.already_analyzed,
            estimated_cost_usd=Decimal(0),
        )
    enq = await enqueue_job(
        db,
        type_=JobType.CHANNEL_ANALYSIS,
        queue=JobQueue.BULK,
        params={
            "project_id": project.id, "channel_ids": plan.channel_ids,
            "videos_per_channel": req.videos_per_channel, "detail": req.detail, "model_key": est.model_key,
        },
        workspace_id=workspace_id,
        created_by=actor_id,
        progress_total=len(plan.channel_ids),
    )
    if enq.created:
        cost = est.estimated_cost_usd
        enq.job.estimated_cost_usd = cost
        await db.flush()
        await audit(
            db, "job.enqueued", workspace_id=workspace_id, actor_user_id=actor_id, entity_type="job_run",
            entity_id=enq.job.id,
            diff={
                "type": enq.job.type, "project_id": project.id, "channels": len(plan.channel_ids),
                "images": est.images, "model_key": est.model_key, "detail": req.detail,
                "estimated_cost_usd": str(cost) if cost is not None else None,
                "confirm_token": req.confirm_token,
            },
        )
    return AnalysisStartResult(
        job=JobRunOut.model_validate(enq.job), created=enq.created, items=len(plan.channel_ids),
        already_analyzed=est.already_analyzed, estimated_cost_usd=est.estimated_cost_usd,
    )


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
def _num(x: float) -> ColumnElement[Any]:
    return literal(Decimal(str(x)), Numeric)


def aggregates_subquery(weights: dict[str, float], *conds: ColumnElement[bool]) -> Subquery:
    """One row per current channel audit matching ``conds``: aggregates of the compared videos' current
    thumbnail audits under ``weights``. Videos without a current audit any more are not counted."""
    overall = scoring.overall_sql(weights)
    return (
        select(
            ChannelAnalysis.id.label("channel_analysis_id"),
            func.count().label("n"),
            func.round(func.avg(overall), 2).label("avg"),
            func.percentile_cont(0.5).within_group(overall).label("median"),
            func.count().filter(overall < _num(WEAK_BELOW)).label("weak"),
            func.count().filter(overall >= _num(STRONG_FROM)).label("strong"),
            array_agg(aggregate_order_by(Video.id, overall.desc(), Video.id))[1].label("best"),
            array_agg(aggregate_order_by(Video.id, overall.asc(), Video.id))[1].label("worst"),
        )
        .select_from(ChannelAnalysis)
        .join(Video, Video.id == any_(ChannelAnalysis.video_ids))
        .join(Thumbnail, Thumbnail.video_id == Video.id)
        .join(
            ThumbnailAnalysis,
            and_(ThumbnailAnalysis.image_asset_id == Thumbnail.image_asset_id, ThumbnailAnalysis.is_current),
        )
        .where(ChannelAnalysis.is_current, *conds)
        .group_by(ChannelAnalysis.id)
        .subquery()
    )


NO_AGGREGATES = ChannelAggregates(
    thumbnails_analyzed=0, avg_score=None, median_score=None, pct_weak=Decimal(0), pct_medium=Decimal(0),
    pct_strong=Decimal(0), best_video_id=None, worst_video_id=None,
)


def _pct(part: int, n: int) -> Decimal:
    return (Decimal(100 * part) / n).quantize(_Q1) if n else Decimal(0)


def aggregates_out(row: Any) -> ChannelAggregates:
    n = int(row.n or 0)
    weak, strong = int(row.weak or 0), int(row.strong or 0)
    return ChannelAggregates(
        thumbnails_analyzed=n,
        avg_score=Decimal(row.avg).quantize(_Q2) if row.avg is not None else None,
        median_score=Decimal(str(row.median)).quantize(_Q2) if row.median is not None else None,
        pct_weak=_pct(weak, n), pct_medium=_pct(n - weak - strong, n), pct_strong=_pct(strong, n),
        best_video_id=row.best, worst_video_id=row.worst,
    )


async def channel_visual_report(db: AsyncSession, workspace_id: int, channel_id: int) -> ChannelVisualReport:
    await get_visible_channel(db, workspace_id, channel_id)
    active = await scoring.active_thumbnail_weights(db, workspace_id)
    profile = scoring.profile_out(active)
    analysis = await db.scalar(
        select(ChannelAnalysis).where(ChannelAnalysis.channel_id == channel_id, ChannelAnalysis.is_current)
    )
    if analysis is None:
        return ChannelVisualReport(analysis=None, aggregates=None, videos=[], scoring=profile)
    agg = aggregates_subquery(active.weights, ChannelAnalysis.id == analysis.id)
    agg_row = (await db.execute(select(agg))).first()
    overall = scoring.overall_sql(active.weights)
    rows = await db.execute(
        select(
            Video, Thumbnail.image_asset_id, overall.label("overall"),
            ThumbnailAnalysis.improvement_opportunity,
        )
        .outerjoin(Thumbnail, Thumbnail.video_id == Video.id)
        .outerjoin(
            ThumbnailAnalysis,
            and_(ThumbnailAnalysis.image_asset_id == Thumbnail.image_asset_id, ThumbnailAnalysis.is_current),
        )
        .where(Video.id.in_(analysis.video_ids))
    )
    by_id = {
        v.id: ChannelAuditVideo(
            video_id=v.id, youtube_video_id=v.youtube_video_id, title=v.title, published_at=v.published_at,
            view_count=v.view_count, image_asset_id=asset_id,
            overall_score=Decimal(score) if score is not None else None,
            improvement_opportunity=AILevel(opp).value if opp is not None else None,
        )
        for v, asset_id, score, opp in rows.tuples()
    }
    return ChannelVisualReport(
        analysis=ChannelAnalysisOut.model_validate(analysis),
        # no row => none of the compared videos has a current thumbnail audit any more
        aggregates=aggregates_out(agg_row) if agg_row is not None else NO_AGGREGATES,
        videos=[by_id[i] for i in analysis.video_ids if i in by_id],
        scoring=profile,
        overrides=(await overrides_for(db, workspace_id, ScoreTarget.CHANNEL_ANALYSIS, [analysis.id])).get(
            analysis.id, []
        ),
    )


async def list_channel_analyses(
    db: AsyncSession,
    workspace_id: int,
    project_id: int,
    *,
    sort: ChannelAnalysisSort,
    order: SortOrder,
    opportunity: Level | None,
    problem: ProblemCode | None,
    limit: int,
    offset: int,
) -> ChannelAnalysisList:
    """Channels of the project with a current channel audit. Default order: lowest average thumbnail
    quality first (best redesign leads)."""
    await get_project(db, workspace_id, project_id)
    active = await scoring.active_thumbnail_weights(db, workspace_id)
    in_project = (
        select(ProjectChannel.channel_id).where(ProjectChannel.project_id == project_id).scalar_subquery()
    )
    agg = aggregates_subquery(active.weights, ChannelAnalysis.channel_id.in_(in_project))
    base = (
        select(ChannelAnalysis, Channel, agg)
        .join(Channel, Channel.id == ChannelAnalysis.channel_id)
        .outerjoin(agg, agg.c.channel_analysis_id == ChannelAnalysis.id)
        .where(ChannelAnalysis.is_current, ChannelAnalysis.channel_id.in_(in_project))
    )
    conds: list[ColumnElement[bool]] = []
    if opportunity is not None:
        conds.append(ChannelAnalysis.visual_opportunity == AILevel(opportunity))
    if problem is not None:
        conds.append(ChannelAnalysis.recurring_problem_codes.contains([problem.value]))
    filtered = base.where(*conds)
    total = await db.scalar(select(func.count()).select_from(filtered.subquery())) or 0
    sort_col: Any = {
        ChannelAnalysisSort.AVG_SCORE: agg.c.avg,
        ChannelAnalysisSort.VISUAL_CONSISTENCY: ChannelAnalysis.visual_consistency,
        ChannelAnalysisSort.OUTDATED_STYLE: ChannelAnalysis.outdated_style_score,
        ChannelAnalysisSort.SUBSCRIBERS: Channel.subscriber_count,
        ChannelAnalysisSort.ANALYZED_AT: ChannelAnalysis.created_at,
    }[sort]
    ordered = sort_col.asc().nulls_last() if order == SortOrder.ASC else sort_col.desc().nulls_last()
    rows = await db.execute(filtered.order_by(ordered, Channel.id).limit(limit).offset(offset))
    items = [
        AnalyzedChannelOut(
            channel_id=row.Channel.id, youtube_channel_id=row.Channel.youtube_channel_id,
            title=row.Channel.title, subscriber_count=row.Channel.subscriber_count,
            analysis=ChannelAnalysisOut.model_validate(row.ChannelAnalysis), aggregates=aggregates_out(row),
        )
        for row in rows.all()
    ]

    current = select(ChannelAnalysis.visual_opportunity, ChannelAnalysis.provider).where(
        ChannelAnalysis.is_current, ChannelAnalysis.channel_id.in_(in_project)
    ).subquery()
    head = (
        await db.execute(select(func.count(), func.count().filter(current.c.provider == "mock")))
    ).one()
    by_level = await db.execute(
        select(current.c.visual_opportunity, func.count()).group_by(current.c.visual_opportunity)
    )
    return ChannelAnalysisList(
        items=items, total=total, limit=limit, offset=offset,
        summary=ChannelAnalysisSummary(
            channels_analyzed=int(head[0]), mock_results=int(head[1]),
            opportunity={AILevel(k).value: int(n) for k, n in by_level.all()},
        ),
        scoring=scoring.profile_out(active),
    )
