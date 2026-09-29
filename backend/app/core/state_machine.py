"""Minimal explicit state machine (ADR-0011).

Transitions are declared as ``{from_state: {allowed_to_states}}``. Persistence, history
and audit are the responsibility of the domain service that owns the entity.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from app.core.errors import InvalidTransitionError


class StateMachine[S: StrEnum]:
    def __init__(self, name: str, transitions: Mapping[S, set[S]]) -> None:
        self.name = name
        self._transitions: dict[S, frozenset[S]] = {k: frozenset(v) for k, v in transitions.items()}

    def allowed(self, current: S) -> frozenset[S]:
        return self._transitions.get(current, frozenset())

    def can(self, current: S, target: S) -> bool:
        return target in self.allowed(current)

    def assert_can(self, current: S, target: S) -> None:
        if not self.can(current, target):
            raise InvalidTransitionError(
                f"{self.name}: transition {current.value} → {target.value} is not allowed",
                details={"from": current.value, "to": target.value},
            )

    def is_terminal(self, state: S) -> bool:
        return not self.allowed(state)


# ---------------------------------------------------------------------------
# Lead lifecycle (§10). FILTERED is intentionally not a stored state (ADR-0011).
# ---------------------------------------------------------------------------
class LeadStatus(StrEnum):
    DISCOVERED = "discovered"
    ANALYZED = "analyzed"
    SHORTLISTED = "shortlisted"
    SELECTED = "selected"
    GENERATION_PENDING = "generation_pending"
    GENERATED = "generated"
    OFFER_DRAFT = "offer_draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    SENT = "sent"
    REPLIED = "replied"
    DECLINED = "declined"
    OPTED_OUT = "opted_out"
    FAILED = "failed"
    REJECTED = "rejected"


_L = LeadStatus
_ANY_ACTIVE_EXIT = {_L.REJECTED, _L.OPTED_OUT}

LEAD_MACHINE: StateMachine[LeadStatus] = StateMachine(
    "lead",
    {
        _L.DISCOVERED: {_L.ANALYZED, _L.SHORTLISTED, _L.SELECTED} | _ANY_ACTIVE_EXIT,
        _L.ANALYZED: {_L.SHORTLISTED, _L.SELECTED} | _ANY_ACTIVE_EXIT,
        _L.SHORTLISTED: {_L.SELECTED, _L.ANALYZED} | _ANY_ACTIVE_EXIT,
        _L.SELECTED: {_L.GENERATION_PENDING, _L.OFFER_DRAFT, _L.SHORTLISTED} | _ANY_ACTIVE_EXIT,
        _L.GENERATION_PENDING: {_L.GENERATED, _L.FAILED, _L.SELECTED} | _ANY_ACTIVE_EXIT,
        _L.GENERATED: {_L.OFFER_DRAFT, _L.GENERATION_PENDING} | _ANY_ACTIVE_EXIT,
        _L.OFFER_DRAFT: {_L.PENDING_APPROVAL, _L.GENERATION_PENDING} | _ANY_ACTIVE_EXIT,
        _L.PENDING_APPROVAL: {_L.APPROVED, _L.OFFER_DRAFT} | _ANY_ACTIVE_EXIT,
        _L.APPROVED: {_L.SCHEDULED, _L.SENT, _L.FAILED, _L.PENDING_APPROVAL} | _ANY_ACTIVE_EXIT,
        _L.SCHEDULED: {_L.SENT, _L.FAILED, _L.APPROVED} | _ANY_ACTIVE_EXIT,
        _L.SENT: {_L.REPLIED, _L.DECLINED, _L.OPTED_OUT},
        _L.REPLIED: {_L.DECLINED, _L.OPTED_OUT},
        _L.FAILED: {_L.APPROVED, _L.SELECTED} | _ANY_ACTIVE_EXIT,
        _L.REJECTED: {_L.SHORTLISTED},  # operator may restore a rejected lead
        _L.DECLINED: set(),
        _L.OPTED_OUT: set(),
    },
)


# ---------------------------------------------------------------------------
# Job runs (§31)
# ---------------------------------------------------------------------------
class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"
    CANCELLED = "cancelled"


_J = JobStatus
JOB_MACHINE: StateMachine[JobStatus] = StateMachine(
    "job_run",
    {
        _J.QUEUED: {_J.RUNNING, _J.CANCELLED, _J.FAILED},
        _J.RUNNING: {_J.COMPLETED, _J.FAILED, _J.RETRYING, _J.CANCELLED},
        _J.RETRYING: {_J.RUNNING, _J.CANCELLED, _J.FAILED},
        _J.COMPLETED: set(),
        _J.FAILED: set(),
        _J.CANCELLED: set(),
    },
)

ACTIVE_JOB_STATUSES = (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.RETRYING)
