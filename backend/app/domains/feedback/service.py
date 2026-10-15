"""Human score corrections (ТЗ §47) and the feedback log (§46).

The AI / code value is never overwritten: a ``score_overrides`` row keeps the human value next to the AI
value it replaced. Where a human value applies:

* thumbnail ``overall_score`` / ``redesign_potential`` — in the Lead Score inputs (average thumbnail
  score, redesign potential) and in the thumbnail audit list;
* lead ``total`` / ``priority_score`` — in lead sorting, filters and the system tags;
* other subscores and channel audit scores — displayed next to the AI value (feedback for analytics).

An override belongs to one stored analysis: a re-analysis (new prompt / model) produces a new AI value
without the human one. Every change is appended to ``feedback_events``.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, Numeric, and_, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ConflictError, NotFoundError
from app.domains.analysis import scoring as thumb_scoring
from app.domains.analysis.audit import SUBSCORES
from app.domains.analysis.models import ChannelAnalysis, ThumbnailAnalysis
from app.domains.favorites.service import channel_visible
from app.domains.feedback.models import FeedbackEvent, FeedbackKind, ScoreOverride, ScoreTarget
from app.domains.feedback.schemas import (
    DecisionStats,
    FeedbackSummary,
    OverrideRemoved,
    OverrideStats,
    ScoreOverrideIn,
    ScoreOverrideOut,
    ScoreOverrideSaved,
)
from app.domains.identity.service import audit
from app.domains.leads.models import Lead, LeadScore
from app.domains.leads.results import RescoreResult
from app.domains.media.models import Thumbnail
from app.domains.youtube.models import Video

OVERALL = "overall_score"
FIELDS: dict[ScoreTarget, tuple[str, ...]] = {
    ScoreTarget.THUMBNAIL_ANALYSIS: (OVERALL, *SUBSCORES),
    ScoreTarget.CHANNEL_ANALYSIS: ("visual_consistency", "outdated_style_score"),
    ScoreTarget.LEAD: ("total", "priority_score"),
}
RANGES: dict[ScoreTarget, tuple[Decimal, Decimal]] = {
    ScoreTarget.THUMBNAIL_ANALYSIS: (Decimal(1), Decimal(10)),
    ScoreTarget.CHANNEL_ANALYSIS: (Decimal(1), Decimal(10)),
    ScoreTarget.LEAD: (Decimal(0), Decimal(100)),
}
# fields whose human value feeds the Lead Score inputs or the system tags => rescore the affected leads
RESCORE_FIELDS: dict[ScoreTarget, frozenset[str]] = {
    ScoreTarget.THUMBNAIL_ANALYSIS: frozenset({OVERALL, "redesign_potential"}),
    ScoreTarget.CHANNEL_ANALYSIS: frozenset(),
    ScoreTarget.LEAD: frozenset({"total", "priority_score"}),
}


def override_join(
    alias: type[ScoreOverride], workspace_id: int, target: ScoreTarget, field: str, target_id: Any
) -> ColumnElement[bool]:
    """ON clause of an outer join to the human value of ``field`` (used by leads and analysis lists)."""
    return and_(
        alias.workspace_id == workspace_id,
        alias.target_type == target,
        alias.target_id == target_id,
        alias.field == field,
    )


async def overrides_for(
    db: AsyncSession, workspace_id: int, target: ScoreTarget, ids: Sequence[int]
) -> dict[int, list[ScoreOverrideOut]]:
    out: dict[int, list[ScoreOverrideOut]] = {}
    if not ids:
        return out
    rows = await db.scalars(
        select(ScoreOverride)
        .where(
            ScoreOverride.workspace_id == workspace_id,
            ScoreOverride.target_type == target,
            ScoreOverride.target_id.in_(list(ids)),
        )
        .order_by(ScoreOverride.target_id, ScoreOverride.field)
    )
    for row in rows:
        out.setdefault(row.target_id, []).append(ScoreOverrideOut.model_validate(row))
    return out


# ---------------------------------------------------------------------------
# The current AI / code value and the leads it affects
# ---------------------------------------------------------------------------
async def _thumbnail_target(
    db: AsyncSession, workspace_id: int, analysis_id: int, field: str
) -> tuple[Decimal, list[int]]:
    analysis = await db.get(ThumbnailAnalysis, analysis_id)
    channel_ids = (
        list(
            await db.scalars(
                select(Video.channel_id)
                .join(Thumbnail, Thumbnail.video_id == Video.id)
                .where(
                    Thumbnail.image_asset_id == analysis.image_asset_id,
                    channel_visible(workspace_id, Video.channel_id),
                )
                .distinct()
            )
        )
        if analysis is not None
        else []
    )
    if analysis is None or not channel_ids:
        raise NotFoundError("Thumbnail analysis not found")
    value: Any
    if field == OVERALL:
        weights = await thumb_scoring.active_thumbnail_weights(db, workspace_id)
        value = await db.scalar(
            select(thumb_scoring.overall_sql(weights.weights)).where(ThumbnailAnalysis.id == analysis_id)
        )
    else:
        value = getattr(analysis, field)
    return Decimal(value), channel_ids


async def _channel_target(db: AsyncSession, workspace_id: int, analysis_id: int, field: str) -> Decimal:
    row = await db.scalar(
        select(ChannelAnalysis).where(
            ChannelAnalysis.id == analysis_id, channel_visible(workspace_id, ChannelAnalysis.channel_id)
        )
    )
    if row is None:
        raise NotFoundError("Channel analysis not found")
    return Decimal(getattr(row, field))


async def _lead_target(db: AsyncSession, workspace_id: int, lead_id: int, field: str) -> tuple[Decimal, int]:
    row = (
        await db.execute(
            select(Lead.channel_id, LeadScore)
            .outerjoin(LeadScore, and_(LeadScore.lead_id == Lead.id, LeadScore.is_current))
            .where(Lead.id == lead_id, Lead.workspace_id == workspace_id)
        )
    ).first()
    if row is None:
        raise NotFoundError("Lead not found")
    channel_id, score = row
    if score is None:
        raise ConflictError("The lead has no score yet; recompute the scores first", code="not_scored")
    return Decimal(getattr(score, field)), channel_id


async def _current(
    db: AsyncSession, workspace_id: int, target: ScoreTarget, target_id: int, field: str
) -> tuple[Decimal, list[int]]:
    """(current AI / code value, channels whose leads depend on it)."""
    if field not in FIELDS[target]:
        raise AppError(
            f"Unknown field for {target.value}", code="invalid_field",
            details={"allowed": list(FIELDS[target])},
        )
    if target == ScoreTarget.THUMBNAIL_ANALYSIS:
        return await _thumbnail_target(db, workspace_id, target_id, field)
    if target == ScoreTarget.CHANNEL_ANALYSIS:
        return await _channel_target(db, workspace_id, target_id, field), []
    value, channel_id = await _lead_target(db, workspace_id, target_id, field)
    return value, [channel_id]


async def _rescore(
    db: AsyncSession, workspace_id: int, target: ScoreTarget, field: str, channel_ids: list[int]
) -> RescoreResult | None:
    if field not in RESCORE_FIELDS[target] or not channel_ids:
        return None
    from app.domains.leads.service import sync_leads  # leads → feedback models only; avoid a cycle

    return await sync_leads(db, workspace_id, channel_ids=channel_ids)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------
async def set_override(
    db: AsyncSession, workspace_id: int, actor_id: int, body: ScoreOverrideIn
) -> ScoreOverrideSaved:
    lo, hi = RANGES[body.target_type]
    if not lo <= body.human_value <= hi:
        raise AppError(
            f"human_value must be between {lo} and {hi}", code="out_of_range",
            details={"min": str(lo), "max": str(hi)},
        )
    ai_value, channel_ids = await _current(db, workspace_id, body.target_type, body.target_id, body.field)
    row = await db.scalar(
        select(ScoreOverride).where(
            ScoreOverride.workspace_id == workspace_id,
            ScoreOverride.target_type == body.target_type,
            ScoreOverride.target_id == body.target_id,
            ScoreOverride.field == body.field,
        )
    )
    previous = row.human_value if row is not None else None
    if row is None:
        row = ScoreOverride(
            workspace_id=workspace_id, target_type=body.target_type, target_id=body.target_id,
            field=body.field, ai_value=ai_value, human_value=body.human_value, reason=body.reason,
            user_id=actor_id,
        )
        db.add(row)
    else:
        row.ai_value, row.human_value = ai_value, body.human_value
        row.reason, row.user_id = body.reason, actor_id
    db.add(
        FeedbackEvent(
            workspace_id=workspace_id, user_id=actor_id, kind=FeedbackKind.SCORE_OVERRIDE,
            target_type=body.target_type.value, target_id=body.target_id, field=body.field,
            ai_value=ai_value, human_value=body.human_value,
            payload={"previous_human_value": str(previous) if previous is not None else None,
                     "reason": body.reason},
        )
    )
    await db.flush()
    await audit(
        db, "score_override.set", workspace_id=workspace_id, actor_user_id=actor_id,
        entity_type=body.target_type.value, entity_id=body.target_id,
        diff={"field": body.field, "ai_value": str(ai_value), "human_value": str(body.human_value),
              "previous_human_value": str(previous) if previous is not None else None},
    )
    rescored = await _rescore(db, workspace_id, body.target_type, body.field, channel_ids)
    await db.refresh(row)
    return ScoreOverrideSaved(**ScoreOverrideOut.model_validate(row).model_dump(), rescored=rescored)


async def remove_override(
    db: AsyncSession, workspace_id: int, actor_id: int, override_id: int
) -> OverrideRemoved:
    row = await db.scalar(
        select(ScoreOverride).where(
            ScoreOverride.id == override_id, ScoreOverride.workspace_id == workspace_id
        )
    )
    if row is None:
        raise NotFoundError("Score override not found")
    try:
        ai_value, channel_ids = await _current(db, workspace_id, row.target_type, row.target_id, row.field)
    except (NotFoundError, ConflictError):  # target gone / not visible any more: still removable
        ai_value, channel_ids = row.ai_value, []
    db.add(
        FeedbackEvent(
            workspace_id=workspace_id, user_id=actor_id, kind=FeedbackKind.SCORE_OVERRIDE_REMOVED,
            target_type=row.target_type.value, target_id=row.target_id, field=row.field,
            ai_value=ai_value, human_value=row.human_value, payload={},
        )
    )
    await audit(
        db, "score_override.removed", workspace_id=workspace_id, actor_user_id=actor_id,
        entity_type=row.target_type.value, entity_id=row.target_id,
        diff={"field": row.field, "human_value": str(row.human_value)},
    )
    target, field = row.target_type, row.field
    await db.delete(row)
    await db.flush()
    return OverrideRemoved(rescored=await _rescore(db, workspace_id, target, field, channel_ids))


async def list_overrides(
    db: AsyncSession, workspace_id: int, target: ScoreTarget, target_ids: Sequence[int]
) -> list[ScoreOverrideOut]:
    found = await overrides_for(db, workspace_id, target, target_ids)
    return [o for tid in target_ids for o in found.get(tid, [])]


# ---------------------------------------------------------------------------
# AI vs human (ТЗ §46)
# ---------------------------------------------------------------------------
def _dec(v: Any) -> Decimal | None:
    return None if v is None else Decimal(v).quantize(Decimal("0.01"))


async def summary(db: AsyncSession, workspace_id: int) -> FeedbackSummary:
    o = ScoreOverride
    stats = await db.execute(
        select(
            o.target_type, o.field, func.count(), func.avg(o.ai_value), func.avg(o.human_value),
            func.avg(o.human_value - o.ai_value),
            func.count().filter(o.human_value < o.ai_value), func.count().filter(o.human_value > o.ai_value),
        )
        .where(o.workspace_id == workspace_id)
        .group_by(o.target_type, o.field)
        .order_by(o.target_type, o.field)
    )
    e = FeedbackEvent
    status = e.payload["to"].astext
    score = cast(e.payload["lead_score"].astext, Numeric)
    decisions = await db.execute(
        select(status, func.count(), func.avg(score))
        .where(e.workspace_id == workspace_id, e.kind == FeedbackKind.LEAD_STATUS)
        .group_by(status)
        .order_by(func.count().desc(), status)
    )
    events = await db.scalar(select(func.count()).select_from(e).where(e.workspace_id == workspace_id)) or 0
    return FeedbackSummary(
        overrides=[
            OverrideStats(
                target_type=t, field=f, overrides=int(n), avg_ai=_dec(ai) or Decimal(0),
                avg_human=_dec(hu) or Decimal(0), avg_delta=_dec(d) or Decimal(0),
                ai_overestimates=int(over), ai_underestimates=int(under),
            )
            for t, f, n, ai, hu, d, over, under in stats.tuples()
        ],
        lead_decisions=[
            DecisionStats(status=s, decisions=int(n), avg_lead_score=_dec(avg))
            for s, n, avg in decisions.tuples()
            if s is not None
        ],
        events=int(events),
    )
