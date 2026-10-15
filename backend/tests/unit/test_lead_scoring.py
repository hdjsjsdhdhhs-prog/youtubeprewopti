"""Phase 3.7: deterministic, explainable Lead / Priority Score (pure functions)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domains.leads import scoring as s
from app.domains.leads.schemas import LeadWeights, PriorityWeights

NOW = datetime(2026, 10, 14, 12, tzinfo=UTC)


def full_inputs(**kw) -> s.LeadInputs:
    base = dict(
        subscribers=1_000_000,
        country="US",
        language="en-US",
        has_metrics=True,
        avg_views=600_000,
        median_views=500_000,
        last_video_at=NOW - timedelta(days=2),
        videos_30d=6,
        views_trend=0.5,
        subscriber_growth_30d=0.05,
        thumbnails_audited=8,
        avg_thumbnail_score=Decimal("1.00"),
        avg_redesign_potential=Decimal("10.00"),
        visual_opportunity="high",
        niche_ids=frozenset({1, 2}),
    )
    return s.LeadInputs(**(base | kw))


def test_defaults_sum_to_100_and_contactability_is_off():
    assert sum(s.DEFAULT_LEAD_WEIGHTS.values()) == 100
    assert sum(s.DEFAULT_PRIORITY_WEIGHTS.values()) == 100
    assert s.DEFAULT_LEAD_WEIGHTS["contactability"] == 0 == s.DEFAULT_PRIORITY_WEIGHTS["contactability"]
    assert set(s.DEFAULT_LEAD_WEIGHTS) == set(s.COMPONENTS) == set(LeadWeights().component_weights())


def test_perfect_lead_scores_100_and_explains_points_out_of_max():
    ilp = s.IdealLeadCriteria(min_subscribers=10_000, min_activity="high", max_thumbnail_score=6, niches=[2])
    comps = s.compute_components(full_inputs(), ilp, {1: 10}, NOW)
    b = s.score(comps, LeadWeights().component_weights(), PriorityWeights().component_weights())
    assert b.total == Decimal("100.00") and b.priority == Decimal("100.00")
    # contactability: contacts never scanned => no data, but its weight is 0 => full coverage
    assert b.data_coverage == Decimal("100.00")
    by_key = {c["key"]: c for c in b.components}
    assert [c["key"] for c in b.components] == list(s.COMPONENTS)
    assert (
        by_key["thumbnail_opportunity"]["points"] == "25.00"
        and by_key["thumbnail_opportunity"]["max"] == "25.00"
    )
    assert by_key["contactability"]["has_data"] is False
    assert "ещё не собирались" in by_key["contactability"]["reason"]
    assert "совпало 4 из 4" in by_key["channel_fit"]["reason"]


def test_missing_data_scores_zero_and_lowers_coverage():
    comps = s.compute_components(s.LeadInputs(), None, {}, NOW)
    assert all(c.fraction is None for c in comps.values())
    b = s.score(comps, LeadWeights().component_weights(), PriorityWeights().component_weights())
    assert b.total == 0 and b.priority == 0 and b.data_coverage == 0
    assert all(c["reason"].startswith("нет данных") for c in b.components)


def test_thumbnail_opportunity_uses_quality_gap_redesign_and_channel_audit():
    weak = s.thumbnail_opportunity(
        full_inputs(
            avg_thumbnail_score=Decimal("4.00"),
            avg_redesign_potential=Decimal("7.00"),
            visual_opportunity=None,
        )
    )
    # 0.6 * (6/9) + 0.4 * (6/9)
    assert weak.fraction == pytest.approx(6 / 9)
    with_audit = s.thumbnail_opportunity(
        full_inputs(
            avg_thumbnail_score=Decimal("4.00"),
            avg_redesign_potential=Decimal("7.00"),
            visual_opportunity="low",
        )
    )
    assert with_audit.fraction == pytest.approx(0.5 * 6 / 9 + 0.25 * 6 / 9 + 0.25 * 0.2)
    strong = s.thumbnail_opportunity(
        full_inputs(
            avg_thumbnail_score=Decimal("9.50"),
            avg_redesign_potential=Decimal("1.00"),
            visual_opportunity=None,
        )
    )
    assert strong.fraction < 0.05
    assert s.thumbnail_opportunity(full_inputs(thumbnails_audited=0)).fraction is None


@pytest.mark.parametrize(
    ("subs", "expected"),
    [(None, None), (500, 0.0), (1_000, 0.0), (31_623, 0.5), (1_000_000, 1.0), (10**8, 1.0)],
)
def test_audience_log_scale(subs, expected):
    c = s.audience(s.LeadInputs(subscribers=subs))
    assert c.fraction == (None if expected is None else pytest.approx(expected, abs=1e-3))


def test_hidden_subscribers_are_no_data():
    assert s.audience(s.LeadInputs(subscribers=50_000, subscribers_hidden=True)).fraction is None


def test_activity_levels_and_component():
    assert s.activity_level(None, None, NOW) is None
    assert s.activity_level(NOW - timedelta(days=10), 4, NOW) == s.ActivityLevel.HIGH
    assert s.activity_level(NOW - timedelta(days=10), 3, NOW) == s.ActivityLevel.MEDIUM
    assert s.activity_level(NOW - timedelta(days=29), 9, NOW) == s.ActivityLevel.MEDIUM
    assert s.activity_level(NOW - timedelta(days=45), 0, NOW) == s.ActivityLevel.LOW
    c = s.activity(s.LeadInputs(last_video_at=NOW - timedelta(days=20), videos_30d=2), NOW)
    assert c.fraction == pytest.approx(0.6 * 0.6 + 0.4 * 0.5)
    assert c.inputs["level"] == "medium" and "20 дн." in c.reason
    assert s.activity(s.LeadInputs(last_video_at=NOW - timedelta(days=400), videos_30d=0), NOW).fraction == 0


def test_growth_combines_views_trend_and_subscriber_snapshots():
    assert s.growth(s.LeadInputs()).fraction is None
    assert s.growth(s.LeadInputs(views_trend=0.0)).fraction == pytest.approx(0.5)
    both = s.growth(s.LeadInputs(views_trend=-0.25, subscriber_growth_30d=0.02))
    assert both.fraction == pytest.approx((0.25 + 0.7) / 2)


def test_niche_value_takes_best_matching_node():
    i = s.LeadInputs(niche_ids=frozenset({1, 2, 3}))
    assert s.niche_value(i, {}).fraction is None
    assert s.niche_value(i, {9: 10}).fraction is None
    c = s.niche_value(i, {1: 4, 3: 8, 9: 10})
    assert c.fraction == pytest.approx(0.8) and c.inputs == {"taxonomy_node_id": 3, "value": 8}


def test_channel_fit_counts_unknown_as_not_met_contacts_included():
    ilp = s.IdealLeadCriteria(
        min_subscribers=10_000,
        max_subscribers=100_000,
        min_avg_views=20_000,
        max_thumbnail_score=6,
        countries=["us"],
        languages=["EN"],
        contact_types=["telegram"],
    )
    i = s.LeadInputs(subscribers=50_000, avg_views=5_000, country="US", language="en-GB")
    c = s.channel_fit(i, ilp, NOW)
    # met: subscribers, country, language; missed: views; unknown: thumbnail score, contacts (not scanned yet)
    assert c.fraction == pytest.approx(3 / 6)
    assert c.inputs["missed"] == ["средние просмотры ≥ 20 000"]
    assert c.inputs["unknown"] == ["балл превью ≤ 6", "контакт: telegram"]
    assert s.channel_fit(i, None, NOW).fraction is None
    assert s.channel_fit(i, s.IdealLeadCriteria(contact_types=["email"]), NOW).fraction == 0.0


def test_channel_fit_contact_criterion_uses_usable_contacts_of_scanned_channels():
    ilp = s.IdealLeadCriteria(contact_types=["telegram", "email"])
    scanned_none = s.LeadInputs(contacts_scanned=True)
    c = s.channel_fit(scanned_none, ilp, NOW)
    assert c.fraction == 0.0 and c.inputs["missed"] == ["контакт: telegram или email"]
    has_email = s.LeadInputs(contacts_scanned=True, contact_types=frozenset({"email"}))
    c = s.channel_fit(has_email, ilp, NOW)
    assert c.fraction == 1.0 and not c.inputs.get("unknown")
    # the types of a channel that was never scanned are not trusted
    unscanned = s.LeadInputs(contact_types=frozenset({"email"}))
    assert s.channel_fit(unscanned, ilp, NOW).fraction == 0.0


def test_contactability_component_scanned_vs_never_scanned():
    never = s.contactability(s.LeadInputs())
    assert never.fraction is None and "ещё не собирались" in never.reason

    nothing = s.contactability(s.LeadInputs(contacts_scanned=True))
    assert nothing.fraction == 0.0 and nothing.inputs == {"score": "0.00", "types": []}
    assert "не найдены" in nothing.reason

    both = s.contactability(
        s.LeadInputs(contacts_scanned=True, contact_types=frozenset({"email", "telegram"}))
    )
    assert both.fraction == pytest.approx(0.6) and both.inputs["score"] == "60.00"
    assert both.inputs["types"] == ["email", "telegram"]
    assert "60/100" in both.reason and "email +30" in both.reason and "Telegram +30" in both.reason

    custom = s.contactability(
        s.LeadInputs(contacts_scanned=True, contact_types=frozenset({"vk"})),
        {"email": 0, "telegram": 0, "website": 0, "vk": 100, "other": 0},
    )
    assert custom.fraction == 1.0


def test_contactability_counts_towards_the_lead_score_only_with_weight():
    i = full_inputs(contacts_scanned=True, contact_types=frozenset({"email", "telegram", "website"}))
    comps = s.compute_components(i, None, {}, NOW)
    default = s.score(comps, LeadWeights().component_weights(), PriorityWeights().component_weights())
    assert comps["contactability"].fraction == pytest.approx(0.8)
    assert default.points["contactability"] == 0  # default weight 0: contacts never change a score unasked
    weighted = s.score(
        comps,
        LeadWeights(contactability=20).component_weights(),
        PriorityWeights().component_weights(),
    )
    assert weighted.points["contactability"] > 0


def test_custom_weights_normalise_to_100_and_priority_is_independent():
    comps = s.compute_components(
        full_inputs(
            avg_thumbnail_score=Decimal("10.00"),
            avg_redesign_potential=Decimal("1.00"),
            visual_opportunity="low",
        ),
        None,
        {},
        NOW,
    )
    only_thumb = {k: 0.0 for k in s.COMPONENTS} | {"thumbnail_opportunity": 50.0}
    only_audience = {k: 0.0 for k in s.COMPONENTS} | {"audience": 3.0}
    b = s.score(comps, only_thumb, only_audience)
    assert b.total == Decimal("5.00")  # 0.25 * 0.2 = 0.05 of the max
    assert b.points["thumbnail_opportunity"] == Decimal("2.50")
    assert b.priority == Decimal("100.00")
    with pytest.raises(ValueError):
        s.weighted({}, {k: 0.0 for k in s.COMPONENTS})


def test_weight_schemas_validate():
    with pytest.raises(ValidationError):
        LeadWeights(**{k: 0 for k in s.COMPONENTS})
    with pytest.raises(ValidationError):
        LeadWeights(audience=101)
    with pytest.raises(ValidationError):
        LeadWeights(niche_values={1: 11})
    assert LeadWeights.model_validate({"niche_values": {"7": 5}}).niche_values == {7: 5}
    with pytest.raises(ValidationError):
        s.IdealLeadCriteria(min_subscribers=10, max_subscribers=5)
    with pytest.raises(ValidationError):
        s.IdealLeadCriteria(unknown=1)
