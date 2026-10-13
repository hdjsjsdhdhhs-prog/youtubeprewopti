"""Scoring profiles: configurable weights of deterministic scores (ТЗ §7, AI_ARCHITECTURE §2).

Only the thumbnail kind is used so far (3.5); lead/priority/contactability arrive with 3.7 and Phase 6.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, Numeric, func, literal, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.analysis.audit import DEFAULT_WEIGHTS, SUBSCORES
from app.domains.analysis.models import ScoringKind, ScoringProfile, ThumbnailAnalysis
from app.domains.analysis.schemas import ScoringProfileOut, ThumbnailWeights
from app.domains.identity.service import audit


@dataclass(frozen=True)
class ActiveWeights:
    version: int  # 0 = built-in defaults (no saved profile)
    weights: dict[str, float]


async def active_thumbnail_weights(db: AsyncSession, workspace_id: int) -> ActiveWeights:
    row = await db.scalar(
        select(ScoringProfile).where(
            ScoringProfile.workspace_id == workspace_id,
            ScoringProfile.kind == ScoringKind.THUMBNAIL,
            ScoringProfile.is_active,
        )
    )
    if row is None:
        return ActiveWeights(version=0, weights=dict(DEFAULT_WEIGHTS))
    # stored weights are validated on write; keys added later fall back to their defaults
    merged = DEFAULT_WEIGHTS | {k: float(v) for k, v in row.weights.items() if k in DEFAULT_WEIGHTS}
    return ActiveWeights(version=row.version, weights=merged)


def overall_sql(weights: dict[str, float]) -> ColumnElement[Any]:
    """SQL twin of ``audit.overall_score`` (for sorting/filtering), rounded to 2 decimals."""
    # NUMERIC literals: round(double precision, int) does not exist in PostgreSQL, and decimals match Python.
    def num(x: float) -> ColumnElement[Any]:
        return literal(Decimal(str(x)), Numeric)

    total = sum(Decimal(str(weights.get(k, 0.0))) for k in SUBSCORES)
    terms = [getattr(ThumbnailAnalysis, k) * num(weights[k]) for k in SUBSCORES if weights.get(k, 0.0) > 0]
    acc = terms[0]
    for t in terms[1:]:
        acc = acc + t
    return func.round(acc / literal(total, Numeric), 2)


def profile_out(active: ActiveWeights) -> ScoringProfileOut:
    return ScoringProfileOut(
        kind=ScoringKind.THUMBNAIL, version=active.version, is_default=active.version == 0,
        weights=ThumbnailWeights(**active.weights),
    )


async def set_thumbnail_weights(
    db: AsyncSession, workspace_id: int, actor_id: int, weights: ThumbnailWeights
) -> ScoringProfileOut:
    """Save new weights as the next version and make it active (older versions are kept)."""
    before = await active_thumbnail_weights(db, workspace_id)
    latest = await db.scalar(
        select(func.max(ScoringProfile.version)).where(
            ScoringProfile.workspace_id == workspace_id, ScoringProfile.kind == ScoringKind.THUMBNAIL
        )
    ) or 0
    await db.execute(
        update(ScoringProfile)
        .where(
            ScoringProfile.workspace_id == workspace_id,
            ScoringProfile.kind == ScoringKind.THUMBNAIL,
            ScoringProfile.is_active,
        )
        .values(is_active=False)
    )
    row = ScoringProfile(
        workspace_id=workspace_id, kind=ScoringKind.THUMBNAIL, version=latest + 1,
        weights=weights.model_dump(), is_active=True, created_by=actor_id,
    )
    db.add(row)
    await db.flush()
    await audit(
        db, "scoring_profile.updated", workspace_id=workspace_id, actor_user_id=actor_id,
        entity_type="scoring_profile", entity_id=row.id,
        diff={"kind": "thumbnail", "before": before.weights, "after": weights.model_dump(),
              "version": row.version},
    )
    return profile_out(ActiveWeights(version=row.version, weights=weights.model_dump()))
