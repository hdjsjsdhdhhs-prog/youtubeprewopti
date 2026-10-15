"""Phase 4 pure logic: system tag rules, tag name keys, manual statuses, approval content hash."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.core.errors import AppError
from app.core.state_machine import LEAD_MACHINE, LeadStatus
from app.domains.approvals.service import content_hash
from app.domains.leads import tags
from app.domains.leads.schemas import LeadSelection, LeadWeights
from app.domains.leads.scoring import LeadInputs, compute_components, score
from app.domains.leads.workflow import MANUAL_STATUSES, check_manual

NOW = datetime(2026, 10, 14, tzinfo=UTC)


def _inputs(**kw) -> LeadInputs:
    return LeadInputs(**kw)


def test_system_tag_rules():
    assert tags.system_tag_keys(_inputs(), Decimal(0), NOW) == set()
    weak = _inputs(avg_thumbnail_score=Decimal("4.99"), thumbnails_audited=3)
    assert tags.system_tag_keys(weak, Decimal(10), NOW) == {"weak_thumbnails"}
    assert tags.system_tag_keys(_inputs(avg_thumbnail_score=Decimal(5)), Decimal(10), NOW) == set()
    assert tags.system_tag_keys(_inputs(), Decimal(60), NOW) == {"high_potential"}
    active = _inputs(last_video_at=NOW - timedelta(days=3), videos_30d=5)
    assert tags.system_tag_keys(active, Decimal(60), NOW) == {"high_potential", "hot_lead"}
    assert tags.system_tag_keys(active, Decimal("59.99"), NOW) == set()  # hot needs high potential too
    assert tags.system_tag_keys(_inputs(visual_opportunity="high"), Decimal(0), NOW) == {"visual_opportunity"}
    assert tags.system_tag_keys(_inputs(visual_opportunity="medium"), Decimal(0), NOW) == set()
    assert set(tags.SYSTEM_TAGS) == {"weak_thumbnails", "high_potential", "hot_lead", "visual_opportunity"}


def test_tag_name_key_and_reserved_names():
    assert tags.name_key("  Hot   Lead ") == "hot lead"
    assert tags.name_key("ФИНАНСЫ") == tags.name_key("финансы")
    assert "hot-lead" in tags.RESERVED_NAMES and "weak-thumbnails" in tags.RESERVED_NAMES


def test_manual_statuses_are_reachable_and_guarded():
    assert {
        LeadStatus.ANALYZED,
        LeadStatus.SHORTLISTED,
        LeadStatus.SELECTED,
        LeadStatus.REJECTED,
    } == MANUAL_STATUSES
    for status in MANUAL_STATUSES:
        check_manual(status)
    for status in set(LeadStatus) - MANUAL_STATUSES:
        with pytest.raises(AppError) as e:
            check_manual(status)
        assert e.value.code == "status_not_manual"
    # the human workflow paths of ТЗ §10
    assert LEAD_MACHINE.can(LeadStatus.ANALYZED, LeadStatus.SHORTLISTED)
    assert LEAD_MACHINE.can(LeadStatus.SHORTLISTED, LeadStatus.SELECTED)
    assert LEAD_MACHINE.can(LeadStatus.SHORTLISTED, LeadStatus.ANALYZED)
    assert LEAD_MACHINE.can(LeadStatus.REJECTED, LeadStatus.SHORTLISTED)
    assert not LEAD_MACHINE.can(LeadStatus.REJECTED, LeadStatus.SELECTED)


def test_selection_needs_exactly_one_source():
    assert LeadSelection(lead_ids=[1, 2]).filters is None
    assert LeadSelection(filters={}, exclude_ids=[3]).lead_ids is None
    for bad in ({}, {"lead_ids": [1], "filters": {}}, {"lead_ids": []}):
        with pytest.raises(ValueError):
            LeadSelection.model_validate(bad)


def test_content_hash_is_canonical():
    a = content_hash({"subject": "Привет", "body": "x", "n": 1})
    assert a == content_hash({"n": 1, "body": "x", "subject": "Привет"})
    assert a != content_hash({"subject": "Привет", "body": "x ", "n": 1})
    assert len(a) == 64 and a == a.lower()


def test_human_thumbnail_scores_are_explained():
    i = _inputs(
        thumbnails_audited=4,
        avg_thumbnail_score=Decimal("4.00"),
        avg_redesign_potential=Decimal("6.0"),
        thumbnails_human_scored=2,
    )
    comps = compute_components(i, None, {}, NOW)
    reason = comps["thumbnail_opportunity"].reason
    assert "ручная оценка у 2" in reason
    w = LeadWeights().component_weights()
    assert score(comps, w, w).total > 0
