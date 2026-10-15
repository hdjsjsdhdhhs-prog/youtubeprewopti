"""Human workflow on leads (ТЗ §10–11, Phase 4): manual decisions and bulk actions on a selection.

* **Manual statuses** — only ``MANUAL_STATUSES``; the rest are set by generation / offers / outreach. Every
  transition is checked by ``LEAD_MACHINE``, written to ``lead_status_history`` (with the actor) and to
  ``feedback_events`` together with the Lead Score the code gave the lead at that moment (ТЗ §46: «AI
  recommendation vs human decision»). Rejecting stores the reason on the lead until it leaves REJECTED.
* **Selection** (ТЗ §11) — chosen ids, or «all filtered» (the list filters) minus unticked ids; at most
  ``MAX_BULK_LEADS`` leads per request.
* **Bulk actions** — status, user tags (system tags follow their rules only), favorites of the current user.
  A lead the action cannot apply to (e.g. a forbidden transition) is reported in ``failures``, the others
  are still changed. Status changes never trigger a rescore: the status is not a Lead Score input.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, NotFoundError
from app.core.state_machine import LEAD_MACHINE, LeadStatus
from app.domains.favorites.models import FavoriteEntity
from app.domains.favorites.service import set_favorites
from app.domains.feedback.models import FeedbackEvent, FeedbackKind, ScoreOverride, ScoreTarget
from app.domains.identity.service import audit
from app.domains.leads import service, tags
from app.domains.leads.models import Lead, LeadScore, LeadStatusHistory
from app.domains.leads.schemas import (
    MAX_BULK_LEADS,
    BulkFailure,
    FavoriteAction,
    LeadBulkRequest,
    LeadBulkResult,
    LeadDetail,
    LeadSelection,
    LeadStatusChange,
    SetStatusAction,
    TagAction,
)

MANUAL_STATUSES: frozenset[LeadStatus] = frozenset(
    {
        LeadStatus.ANALYZED,  # back from the shortlist
        LeadStatus.SHORTLISTED,
        LeadStatus.SELECTED,
        LeadStatus.REJECTED,
    }
)
MAX_FAILURES_SHOWN = 50
BATCH = 500


def check_manual(target: LeadStatus) -> None:
    if target not in MANUAL_STATUSES:
        raise AppError(
            f"Status '{target.value}' is set by the system, not by hand",
            code="status_not_manual",
            details={"allowed": sorted(s.value for s in MANUAL_STATUSES)},
        )


async def _scores(
    db: AsyncSession, workspace_id: int, lead_ids: Sequence[int]
) -> dict[int, tuple[Decimal | None, Decimal | None]]:
    """lead id -> (code Lead Score, human Lead Score) at this moment."""
    human = ScoreOverride
    rows = await db.execute(
        select(Lead.id, LeadScore.total, human.human_value)
        .outerjoin(LeadScore, and_(LeadScore.lead_id == Lead.id, LeadScore.is_current))
        .outerjoin(
            human,
            and_(
                human.workspace_id == workspace_id,
                human.target_type == ScoreTarget.LEAD,
                human.target_id == Lead.id,
                human.field == "total",
            ),
        )
        .where(Lead.id.in_(list(lead_ids)))
    )
    return {lead_id: (total, human_total) for lead_id, total, human_total in rows.tuples()}


async def _locked(db: AsyncSession, workspace_id: int, lead_ids: Sequence[int]) -> list[Lead]:
    """The leads, row-locked in id order (concurrent rescores and operators) with fresh attributes."""
    return list(
        await db.scalars(
            select(Lead)
            .where(Lead.workspace_id == workspace_id, Lead.id.in_(list(lead_ids)))
            .order_by(Lead.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )


def _apply_status(
    db: AsyncSession,
    workspace_id: int,
    actor_id: int,
    lead: Lead,
    target: LeadStatus,
    reason: str,
    scores: tuple[Decimal | None, Decimal | None],
    now: datetime,
) -> bool:
    """False = already in ``target``; raises ``InvalidTransitionError`` for a forbidden transition."""
    if lead.status == target:
        return False
    LEAD_MACHINE.assert_can(lead.status, target)
    previous = lead.status
    total, human_total = scores
    db.add(
        LeadStatusHistory(
            lead_id=lead.id, from_status=previous, to_status=target, actor_user_id=actor_id, reason=reason
        )
    )
    db.add(
        FeedbackEvent(
            workspace_id=workspace_id,
            user_id=actor_id,
            kind=FeedbackKind.LEAD_STATUS,
            target_type=ScoreTarget.LEAD.value,
            target_id=lead.id,
            field="status",
            ai_value=total,
            human_value=human_total,
            payload={
                "from": previous.value,
                "to": target.value,
                "lead_score": str(total) if total is not None else None,
                "human_lead_score": str(human_total) if human_total is not None else None,
                "reason": reason,
            },
        )
    )
    lead.status = target
    lead.status_changed_at = now
    lead.rejected_reason = (reason or None) if target == LeadStatus.REJECTED else None
    return True


async def change_status(
    db: AsyncSession, workspace_id: int, actor_id: int, lead_id: int, body: LeadStatusChange
) -> LeadDetail:
    check_manual(body.status)
    locked = await _locked(db, workspace_id, [lead_id])
    if not locked:
        raise NotFoundError("Lead not found")
    [lead] = locked
    previous = lead.status
    scores = await _scores(db, workspace_id, [lead.id])
    changed = _apply_status(
        db,
        workspace_id,
        actor_id,
        lead,
        body.status,
        body.reason,
        scores.get(lead.id, (None, None)),
        datetime.now(UTC),
    )
    if changed:
        await db.flush()
        await audit(
            db,
            "lead.status_changed",
            workspace_id=workspace_id,
            actor_user_id=actor_id,
            entity_type="lead",
            entity_id=lead.id,
            diff={"from": previous.value, "to": body.status.value, "reason": body.reason},
        )
    return await service.get_lead(db, workspace_id, actor_id, lead.id)


# ---------------------------------------------------------------------------
# Selection and bulk actions
# ---------------------------------------------------------------------------
def _too_large(n: int) -> AppError:
    return AppError(
        f"The selection has more than {MAX_BULK_LEADS} leads; narrow the filters",
        code="selection_too_large",
        details={"max": MAX_BULK_LEADS, "selected": n},
    )


async def resolve_selection(
    db: AsyncSession, workspace_id: int, user_id: int, selection: LeadSelection
) -> list[int]:
    """Lead ids of the selection, in id order. Explicit ids must all belong to the workspace."""
    exclude = set(selection.exclude_ids)
    if selection.lead_ids is not None:
        wanted = list(dict.fromkeys(selection.lead_ids))
        found = set(
            await db.scalars(select(Lead.id).where(Lead.workspace_id == workspace_id, Lead.id.in_(wanted)))
        )
        missing = [i for i in wanted if i not in found]
        if missing:
            raise NotFoundError("Lead not found", details={"lead_ids": missing[:MAX_FAILURES_SHOWN]})
        return sorted(i for i in found if i not in exclude)
    assert selection.filters is not None  # the model validator guarantees one of the two
    limit = MAX_BULK_LEADS + len(exclude) + 1
    ids = [
        i
        for i in await service.lead_ids_matching(db, workspace_id, user_id, selection.filters, limit=limit)
        if i not in exclude
    ]
    if len(ids) > MAX_BULK_LEADS:
        raise _too_large(len(ids))
    return ids


async def _bulk_status(
    db: AsyncSession, workspace_id: int, actor_id: int, ids: list[int], action: SetStatusAction
) -> tuple[int, int, list[BulkFailure], list[int]]:
    check_manual(action.status)
    now = datetime.now(UTC)
    changed: list[int] = []
    unchanged = 0
    failures: list[BulkFailure] = []
    for start in range(0, len(ids), BATCH):
        batch = ids[start : start + BATCH]
        scores = await _scores(db, workspace_id, batch)
        for lead in await _locked(db, workspace_id, batch):
            try:
                ok = _apply_status(
                    db,
                    workspace_id,
                    actor_id,
                    lead,
                    action.status,
                    action.reason,
                    scores.get(lead.id, (None, None)),
                    now,
                )
            except AppError as e:
                failures.append(BulkFailure(lead_id=lead.id, error=e.code))
                continue
            if ok:
                changed.append(lead.id)
            else:
                unchanged += 1
        await db.flush()
    return len(changed), unchanged, failures, changed


async def _bulk_tag(
    db: AsyncSession, workspace_id: int, actor_id: int, ids: list[int], action: TagAction
) -> tuple[int, int, list[int]]:
    tag = await tags.get_user_tag(db, workspace_id, action.tag_id)
    if action.type == "add_tag":
        done = await tags.add_tag(db, tag, ids, actor_id)
    else:
        done = await tags.remove_tag(db, tag, ids)
    return len(done), len(ids) - len(done), sorted(done)


async def _bulk_favorite(
    db: AsyncSession, workspace_id: int, user_id: int, ids: list[int], action: FavoriteAction
) -> tuple[int, int]:
    channels = {
        lead_id: channel_id
        for lead_id, channel_id in (
            await db.execute(select(Lead.id, Lead.channel_id).where(Lead.id.in_(ids)))
        ).tuples()
    }
    done = await set_favorites(
        db, workspace_id, user_id, FavoriteEntity.CHANNEL, channels.values(), action.type == "favorite"
    )
    changed = sum(1 for channel_id in channels.values() if channel_id in done)
    return changed, len(ids) - changed


async def bulk(db: AsyncSession, workspace_id: int, actor_id: int, body: LeadBulkRequest) -> LeadBulkResult:
    ids = await resolve_selection(db, workspace_id, actor_id, body.selection)
    action = body.action
    failures: list[BulkFailure] = []
    if isinstance(action, SetStatusAction):
        changed, unchanged, failures, changed_ids = await _bulk_status(
            db, workspace_id, actor_id, ids, action
        )
        if changed_ids:
            await audit(
                db,
                "lead.bulk_status",
                workspace_id=workspace_id,
                actor_user_id=actor_id,
                entity_type="lead",
                entity_id=None,
                diff={
                    "to": action.status.value,
                    "reason": action.reason,
                    "lead_ids": changed_ids,
                    "failed": len(failures),
                },
            )
    elif isinstance(action, TagAction):
        changed, unchanged, changed_ids = await _bulk_tag(db, workspace_id, actor_id, ids, action)
        if changed_ids:
            await audit(
                db,
                f"lead.bulk_{action.type}",
                workspace_id=workspace_id,
                actor_user_id=actor_id,
                entity_type="tag",
                entity_id=action.tag_id,
                diff={"lead_ids": changed_ids},
            )
    else:  # favorites are personal: no audit entry
        changed, unchanged = await _bulk_favorite(db, workspace_id, actor_id, ids, action)
    return LeadBulkResult(
        matched=len(ids),
        changed=changed,
        unchanged=unchanged,
        failed=len(failures),
        failures=failures[:MAX_FAILURES_SHOWN],
    )
