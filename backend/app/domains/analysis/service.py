"""Prefilter (which thumbnails go to AI) and the budget gate's pre-flight estimate (AI_PIPELINE).

Order of the pipeline: ingestion (download + deterministic metrics, free) → **prefilter** (SQL over
channels, videos and ``image_metrics``) → **estimate + confirmation** → AI audit (Phase 3.5).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domains.ai import budget
from app.domains.ai.cost import estimate_tokens, pricing_known, token_cost
from app.domains.ai.registry import TASKS, AITask, resolve_models
from app.domains.analysis.schemas import (
    AnalysisEstimate,
    AnalysisEstimateRequest,
    BudgetCheckOut,
    MetricBounds,
    PrefilterPreview,
    ThumbnailPrefilter,
)
from app.domains.media.imaging import METRICS_ALGO_VERSION
from app.domains.media.models import FetchStatus, ImageMetrics, Thumbnail
from app.domains.projects.models import ProjectChannel, SearchProject
from app.domains.projects.service import get_project
from app.domains.youtube.models import Channel, ChannelMetrics, Video
from app.domains.youtube.schemas import ChannelFilterSet
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


async def estimate_thumbnail_analysis(
    db: AsyncSession, settings: Settings, workspace_id: int, project_id: int, req: AnalysisEstimateRequest
) -> AnalysisEstimate:
    """Pre-flight estimate of an AI thumbnail audit (§76). Nothing is called or charged here."""
    project = await get_project(db, workspace_id, project_id)
    pf = req.prefilter or project_prefilter(project)
    selection = await select_thumbnails(db, workspace_id, project, pf)
    task = AITask.THUMBNAIL_ANALYSIS
    spec = TASKS[task]
    models = await resolve_models(db, settings, task)
    model = models[0] if models else None
    items = len(selection.asset_ids)
    tokens_in, tokens_out = estimate_tokens(spec, 1, req.detail)
    per_item = token_cost(model, tokens_in, tokens_out) if model is not None else None
    total = per_item * items if per_item is not None else None

    checks: list[BudgetCheckOut] = []
    for b in await budget.applicable_budgets(db, workspace_id, project_id=project.id, task=task.value):
        check = budget.check_usage(await budget.budget_usage(db, b), cost=total, operations=items)
        checks.append(BudgetCheckOut(
            budget_id=b.id, description=budget.describe_budget(b), limit_usd=b.limit_usd,
            max_ai_operations=b.max_ai_operations, spent_usd=check.usage.spent_usd,
            operations=check.usage.operations, would_exceed=check.would_exceed, reason=check.reason,
        ))

    token_payload: dict[str, Any] = {
        "project_id": project.id, "task": task.value, "assets": _ids_digest(selection.asset_ids),
        "model_key": model.key if model else None, "api_model_id": model.api_model_id if model else None,
        "detail": req.detail, "per_item": str(per_item) if per_item is not None else None,
        "prefilter": pf.model_dump(mode="json"),
    }
    return AnalysisEstimate(
        task=task.value,
        preview=selection.preview,
        provider=model.provider if model else settings.effective_ai_provider,
        model_key=model.key if model else None,
        api_model_id=model.api_model_id if model else None,
        detail=req.detail,
        items=items,
        cost_per_item_usd=per_item,
        estimated_cost_usd=total.quantize(Decimal("0.000001")) if total is not None else None,
        pricing_verified=bool(model and pricing_known(model) and model.pricing_verified_at is not None),
        estimated_input_tokens=tokens_in * items,
        estimated_output_tokens=tokens_out * items,
        budgets=checks,
        within_budgets=not any(c.would_exceed for c in checks),
        confirm_token=budget.confirmation_token(token_payload),
    )
