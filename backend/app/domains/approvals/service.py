"""Approval gate (ARCHITECTURE invariant 1: nothing is sent without a human «Approve»).

A decision is bound to the SHA-256 of the exact content it was made on (``content_hash``): editing the
content after an approval makes ``require_approval`` fail until it is approved again. The latest decision
per target counts; older ones stay as history. Offers (Phase 7) and outreach (Phase 8) call
``require_approval`` right before scheduling / sending, inside the same transaction.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ConflictError
from app.domains.approvals.models import Approval, ApprovalDecision, ApprovalTarget
from app.domains.identity.service import audit

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def content_hash(content: Mapping[str, Any]) -> str:
    """Canonical JSON (sorted keys, no whitespace, UTF-8) → SHA-256 hex. Same content ⇒ same hash."""
    raw = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def record_decision(
    db: AsyncSession,
    workspace_id: int,
    actor_id: int,
    target_type: ApprovalTarget,
    target_id: int,
    decision: ApprovalDecision,
    content_sha256: str,
    comment: str = "",
) -> Approval:
    if not _SHA256.match(content_sha256):
        raise AppError("content_hash must be a lowercase SHA-256 hex digest", code="invalid_content_hash")
    row = Approval(
        workspace_id=workspace_id,
        target_type=target_type,
        target_id=target_id,
        decision=decision,
        content_hash=content_sha256,
        comment=comment,
        user_id=actor_id,
    )
    db.add(row)
    await db.flush()
    await audit(
        db,
        f"approval.{decision.value}",
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        entity_type=target_type.value,
        entity_id=target_id,
        diff={"content_hash": content_sha256, "comment": comment},
    )
    return row


async def latest_decision(
    db: AsyncSession, workspace_id: int, target_type: ApprovalTarget, target_id: int
) -> Approval | None:
    return await db.scalar(
        select(Approval)
        .where(
            Approval.workspace_id == workspace_id,
            Approval.target_type == target_type,
            Approval.target_id == target_id,
        )
        .order_by(Approval.created_at.desc(), Approval.id.desc())
        .limit(1)
    )


async def require_approval(
    db: AsyncSession, workspace_id: int, target_type: ApprovalTarget, target_id: int, current_hash: str
) -> Approval:
    """The approval that allows sending exactly this content; raises 409 otherwise."""
    latest = await latest_decision(db, workspace_id, target_type, target_id)
    if latest is None or latest.decision != ApprovalDecision.APPROVED:
        raise ConflictError(
            "Not approved: an explicit human approval is required before sending",
            code="not_approved",
            details={"decision": latest.decision.value if latest is not None else None},
        )
    if latest.content_hash != current_hash:
        raise ConflictError(
            "The content changed after it was approved; approve it again",
            code="approval_outdated",
            details={"approved_hash": latest.content_hash, "current_hash": current_hash},
        )
    return latest
