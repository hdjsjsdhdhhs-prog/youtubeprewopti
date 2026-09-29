"""State machine base and the declared lead / job_run lifecycles (ADR-0011)."""

from __future__ import annotations

from enum import StrEnum

import pytest

from app.core.errors import InvalidTransitionError
from app.core.state_machine import (
    ACTIVE_JOB_STATUSES,
    JOB_MACHINE,
    LEAD_MACHINE,
    JobStatus,
    LeadStatus,
    StateMachine,
)


class Light(StrEnum):
    RED = "red"
    GREEN = "green"
    YELLOW = "yellow"
    BROKEN = "broken"


MACHINE = StateMachine(
    "light", {Light.RED: {Light.GREEN}, Light.GREEN: {Light.YELLOW}, Light.YELLOW: {Light.RED}}
)


def test_allowed_and_can():
    assert MACHINE.allowed(Light.RED) == frozenset({Light.GREEN})
    assert MACHINE.can(Light.RED, Light.GREEN)
    assert not MACHINE.can(Light.RED, Light.YELLOW)
    assert not MACHINE.can(Light.RED, Light.RED)  # self-transitions must be declared explicitly


def test_undeclared_state_is_terminal():
    assert MACHINE.allowed(Light.BROKEN) == frozenset()
    assert MACHINE.is_terminal(Light.BROKEN)
    assert not MACHINE.is_terminal(Light.RED)


def test_assert_can_raises_with_details():
    MACHINE.assert_can(Light.GREEN, Light.YELLOW)
    with pytest.raises(InvalidTransitionError) as exc:
        MACHINE.assert_can(Light.GREEN, Light.RED)
    err = exc.value
    assert err.code == "invalid_transition" and err.status_code == 409
    assert err.details == {"from": "green", "to": "red"}
    assert "light" in err.message


def test_transitions_are_copied_and_immutable():
    source = {Light.RED: {Light.GREEN}}
    m = StateMachine("copy", source)
    source[Light.RED].add(Light.YELLOW)
    assert not m.can(Light.RED, Light.YELLOW)
    assert isinstance(m.allowed(Light.RED), frozenset)


@pytest.mark.parametrize(("machine", "states"), [(LEAD_MACHINE, LeadStatus), (JOB_MACHINE, JobStatus)])
def test_every_state_is_declared_and_targets_are_known(machine, states):
    for state in states:
        assert state in machine._transitions, f"{machine.name}: {state} has no transition entry"
        assert machine.allowed(state) <= set(states)
        assert state not in machine.allowed(state)


@pytest.mark.parametrize(
    ("machine", "start"), [(LEAD_MACHINE, LeadStatus.DISCOVERED), (JOB_MACHINE, JobStatus.QUEUED)]
)
def test_every_state_is_reachable_from_the_start(machine, start):
    seen, frontier = {start}, [start]
    while frontier:
        for nxt in machine.allowed(frontier.pop()):
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    assert seen == set(type(start))


def test_lead_terminal_states_and_human_in_the_loop():
    terminal = {s for s in LeadStatus if LEAD_MACHINE.is_terminal(s)}
    assert terminal == {LeadStatus.DECLINED, LeadStatus.OPTED_OUT}
    # Nothing is sent without approval: SENT is reachable only from APPROVED / SCHEDULED.
    assert {s for s in LeadStatus if LEAD_MACHINE.can(s, LeadStatus.SENT)} == {
        LeadStatus.APPROVED,
        LeadStatus.SCHEDULED,
    }
    assert {s for s in LeadStatus if LEAD_MACHINE.can(s, LeadStatus.APPROVED)} <= {
        LeadStatus.PENDING_APPROVAL,
        LeadStatus.SCHEDULED,
        LeadStatus.FAILED,
    }
    # Opt-out is accepted from every state of the active funnel. REJECTED is a side state (restorable by
    # the operator); sending to an opted-out contact is additionally blocked by the suppression list.
    funnel = {s for s in LeadStatus if not LEAD_MACHINE.is_terminal(s)} - {LeadStatus.REJECTED}
    assert all(LEAD_MACHINE.can(s, LeadStatus.OPTED_OUT) for s in funnel)
    assert LEAD_MACHINE.allowed(LeadStatus.REJECTED) == {LeadStatus.SHORTLISTED}


def test_job_lifecycle():
    assert {s for s in JobStatus if JOB_MACHINE.is_terminal(s)} == {
        JobStatus.COMPLETED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    }
    assert set(ACTIVE_JOB_STATUSES) == {s for s in JobStatus if not JOB_MACHINE.is_terminal(s)}
    assert not JOB_MACHINE.can(JobStatus.QUEUED, JobStatus.COMPLETED)
    assert JOB_MACHINE.can(JobStatus.RETRYING, JobStatus.RUNNING)
    assert all(JOB_MACHINE.can(s, JobStatus.CANCELLED) for s in ACTIVE_JOB_STATUSES)
