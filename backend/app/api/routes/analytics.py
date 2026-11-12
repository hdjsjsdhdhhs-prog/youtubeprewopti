"""Analytics (Phase 9, ТЗ §87; §28, §39, §46, §51, §75). Read-only aggregates; every endpoint takes the same
period (``date_from`` / ``date_to``, inclusive, UTC) and an optional project. Leads are the cohort found in
the period, messages are those sent in it, AI spend and decisions — made in it."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter

from app.api.deps import DbSession, ReadAuth
from app.domains.analytics import service
from app.domains.analytics.schemas import (
    ABDimension,
    ABReport,
    AIFeedbackReport,
    CampaignReport,
    CostReport,
    NicheReport,
    Overview,
    ScoreRangeReport,
)

router = APIRouter(prefix="/analytics", tags=["analytics"])


async def _scope(
    ctx: ReadAuth, db: DbSession, date_from: date | None, date_to: date | None, project_id: int | None
) -> service.Scope:
    return await service.make_scope(db, ctx.workspace.id, date_from, date_to, project_id)


@router.get("/overview", response_model=Overview)
async def overview(
    ctx: ReadAuth,
    db: DbSession,
    date_from: date | None = None,
    date_to: date | None = None,
    project_id: int | None = None,
) -> Overview:
    """Dashboard (ТЗ §39): tiles, the lead funnel, AI spend and the best niche / thumbnail score range /
    campaign / offer variant (only groups with at least ``min_sample`` sent are ranked)."""
    return await service.overview(db, await _scope(ctx, db, date_from, date_to, project_id))


@router.get("/costs", response_model=CostReport)
async def costs(
    ctx: ReadAuth,
    db: DbSession,
    date_from: date | None = None,
    date_to: date | None = None,
    project_id: int | None = None,
) -> CostReport:
    """AI spend by task and model, cost per lead / sent / reply (ТЗ §75)."""
    return await service.cost_report(db, await _scope(ctx, db, date_from, date_to, project_id))


@router.get("/niches", response_model=NicheReport)
async def niches(
    ctx: ReadAuth,
    db: DbSession,
    date_from: date | None = None,
    date_to: date | None = None,
    project_id: int | None = None,
) -> NicheReport:
    """Which niches give more leads, weaker thumbnails, high-opportunity channels, contacts and replies
    (ТЗ §51)."""
    return await service.niche_report(db, await _scope(ctx, db, date_from, date_to, project_id))


@router.get("/score-ranges", response_model=ScoreRangeReport)
async def score_ranges(
    ctx: ReadAuth,
    db: DbSession,
    date_from: date | None = None,
    date_to: date | None = None,
    project_id: int | None = None,
) -> ScoreRangeReport:
    """Leads by the channel's average thumbnail score: how many were selected and answered (ТЗ §39)."""
    return await service.score_range_report(db, await _scope(ctx, db, date_from, date_to, project_id))


@router.get("/campaigns", response_model=CampaignReport)
async def campaigns(
    ctx: ReadAuth,
    db: DbSession,
    date_from: date | None = None,
    date_to: date | None = None,
    project_id: int | None = None,
) -> CampaignReport:
    """Campaigns side by side: leads, approved offers, outcomes of the messages sent, cost (ТЗ §29)."""
    return await service.campaign_report(db, await _scope(ctx, db, date_from, date_to, project_id))


@router.get("/ab", response_model=ABReport)
async def ab(
    ctx: ReadAuth,
    db: DbSession,
    dimension: ABDimension = "style",
    campaign_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    project_id: int | None = None,
) -> ABReport:
    """A/B over every sent message (or one campaign's) by offer style, attachment or channel (ТЗ §28). A
    leader is named only with enough data and a difference beyond chance."""
    scope = await _scope(ctx, db, date_from, date_to, project_id)
    return await service.ab_report(db, scope, dimension, campaign_id)


@router.get("/ai-feedback", response_model=AIFeedbackReport)
async def ai_feedback(
    ctx: ReadAuth,
    db: DbSession,
    date_from: date | None = None,
    date_to: date | None = None,
    project_id: int | None = None,
) -> AIFeedbackReport:
    """AI recommendation vs human decision (ТЗ §46): generated variants by strategy / model, offer decisions
    and edits by style, manual lead decisions by Lead Score, score corrections."""
    return await service.ai_feedback_report(db, await _scope(ctx, db, date_from, date_to, project_id))
