"""Scoring profiles: configurable weights of deterministic scores (ТЗ §7, AI_ARCHITECTURE §2).

Kinds in use: thumbnail (3.5), lead and priority (3.7, ``app.domains.leads.service``); contactability —
Phase 6. Every save adds a version and makes it the active one.
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


async def active_profile(db: AsyncSession, workspace_id: int, kind: ScoringKind) -> ScoringProfile | None:
    """The active saved profile of ``kind`` (None = the built-in defaults apply)."""
    return await db.scalar(
        select(ScoringProfile).where(
            ScoringProfile.workspace_id == workspace_id,
            ScoringProfile.kind == kind,
            ScoringProfile.is_active,
        )
    )


async def save_profile_version(
    db: AsyncSession, workspace_id: int, actor_id: int, kind: ScoringKind, weights: dict[str, Any],
    before: dict[str, Any],
) -> ScoringProfile:
    """Store ``weights`` as the next version of ``kind`` and make it active (older versions are kept)."""
    latest = await db.scalar(
        select(func.max(ScoringProfile.version)).where(
            ScoringProfile.workspace_id == workspace_id, ScoringProfile.kind == kind
        )
    ) or 0
    await db.execute(
        update(ScoringProfile)
        .where(
            ScoringProfile.workspace_id == workspace_id,
            ScoringProfile.kind == kind,
            ScoringProfile.is_active,
        )
        .values(is_active=False)
    )
    row = ScoringProfile(
        workspace_id=workspace_id, kind=kind, version=latest + 1, weights=weights, is_active=True,
        created_by=actor_id,
    )
    db.add(row)
    await db.flush()
    await audit(
        db, "scoring_profile.updated", workspace_id=workspace_id, actor_user_id=actor_id,
        entity_type="scoring_profile", entity_id=row.id,
        diff={"kind": kind.value, "before": before, "after": weights, "version": row.version},
    )
    return row


async def active_thumbnail_weights(db: AsyncSession, workspace_id: int) -> ActiveWeights:
    row = await active_profile(db, workspace_id, ScoringKind.THUMBNAIL)
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
    row = await save_profile_version(
        db, workspace_id, actor_id, ScoringKind.THUMBNAIL, weights.model_dump(), before.weights
    )
    return profile_out(ActiveWeights(version=row.version, weights=weights.model_dump()))
