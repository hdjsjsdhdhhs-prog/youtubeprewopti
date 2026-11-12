"""Phase 9: pure analytics helpers — funnel levels, buckets, outcome counts and the honest A/B comparison
(no leader without enough data or with a difference within chance, ТЗ §28)."""

from __future__ import annotations

from decimal import Decimal

from app.core.state_machine import LeadStatus as L
from app.domains.analytics import stats
from app.domains.outreach.models import MessageEventType as E


def test_lead_level_uses_history_and_messages():
    assert stats.lead_level([L.DISCOVERED], None) == stats.FOUND
    assert stats.lead_level([L.ANALYZED, L.REJECTED], None) == stats.ANALYZED  # rejection keeps the progress
    assert stats.lead_level([L.SELECTED, L.GENERATED, L.OFFER_DRAFT], None) == stats.OFFER
    assert stats.lead_level([L.APPROVED, L.FAILED], None) == stats.APPROVED
    # sent, no answer yet / an answer / a positive answer
    assert stats.lead_level([L.SENT], set()) == stats.SENT
    assert stats.lead_level([L.SENT], {E.DELIVERED, E.REPLIED}) == stats.REPLIED
    assert stats.lead_level([L.SENT], {E.POSITIVE_REPLY}) == stats.POSITIVE
    # a decline recorded on the lead counts as an answer
    assert stats.lead_level([L.SENT, L.DECLINED], set()) == stats.REPLIED


def test_lead_level_without_a_sent_message_stops_at_approved():
    # a status says «sent» but no message was sent (e.g. data repaired by hand): not counted as contacted
    assert stats.lead_level([L.SENT, L.REPLIED], None) == stats.APPROVED


def test_buckets_cover_the_whole_range():
    b = stats.THUMBNAIL_BUCKETS
    assert stats.bucket_of(None, b) is None
    assert stats.bucket_of(Decimal("1"), b).key == "1-3"
    assert stats.bucket_of(Decimal("2.99"), b).key == "1-3"
    assert stats.bucket_of(Decimal("3"), b).key == "3-4"
    assert stats.bucket_of(Decimal("10"), b).key == "8-10"  # the upper edge belongs to the last bucket
    assert stats.bucket_of(Decimal("0.5"), b) is None
    assert stats.bucket_of(Decimal("100"), stats.LEAD_SCORE_BANDS).key == "80-100"
    assert stats.bucket_of(Decimal("0"), stats.LEAD_SCORE_BANDS).key == "0-20"


def test_count_outcomes():
    c = stats.count_outcomes(
        [set(), {E.DELIVERED}, {E.DELIVERED, E.POSITIVE_REPLY}, {E.NEGATIVE_REPLY}, {E.BOUNCED}, {E.OPT_OUT}]
    )
    assert (c.sent, c.delivered, c.replied, c.positive_reply, c.negative_reply) == (6, 2, 2, 1, 1)
    assert (c.bounced, c.opt_out) == (1, 1)
    assert c.no_reply == 2  # nothing recorded / only delivered


def test_rates():
    assert stats.rate(1, 3) == Decimal("0.3333")
    assert stats.rate(1, 0) is None
    assert stats.per_unit(Decimal("0.3"), 4) == Decimal("0.075000")
    assert stats.per_unit(Decimal("1"), 0) is None


def test_compare_needs_two_groups_with_enough_data():
    assert stats.compare([("a", 20, 29), ("b", 1, 29)], 30).verdict == "insufficient_data"
    cmp = stats.compare([("a", 25, 40), ("b", 0, 10)], 30)  # only «a» has enough sent
    assert (cmp.verdict, cmp.leader) == ("insufficient_data", None)


def test_compare_does_not_name_a_leader_within_chance():
    cmp = stats.compare([("a", 7, 40), ("b", 5, 40)], 30)
    assert cmp.verdict == "no_difference"
    assert cmp.leader == "a"  # who is ahead, for display only
    assert cmp.p_value is not None and cmp.p_value > Decimal("0.05")


def test_compare_names_a_leader_beyond_chance():
    cmp = stats.compare([("a", 2, 40), ("b", 20, 40), ("c", 1, 5)], 30)
    assert (cmp.verdict, cmp.leader) == ("leader", "b")
    assert cmp.p_value is not None and cmp.p_value < Decimal("0.05")


def test_two_proportion_p():
    assert stats.two_proportion_p(0, 0, 1, 10) is None
    assert stats.two_proportion_p(0, 30, 0, 30) == 1.0
    p = stats.two_proportion_p(10, 50, 20, 50)
    assert p is not None and abs(p - stats.two_proportion_p(20, 50, 10, 50)) < 1e-12  # symmetric
    assert 0.02 < p < 0.04  # z ≈ 2.18


def test_best_of_ignores_small_groups():
    assert stats.best_of([("a", 5, 10), ("b", 1, 40)], 30) == "b"
    assert stats.best_of([("a", 5, 10)], 30) is None
