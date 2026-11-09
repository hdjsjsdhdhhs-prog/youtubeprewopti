"""Phase 8.2 pure rules: the send window (weekdays, hours, time zone, starts_at), the campaign's local day,
auto-pause after failures in a row, balanced A/B assignment, the contact / channel choice of a campaign."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, time

from app.domains.campaigns import rules
from app.domains.campaigns.models import CAMPAIGN_MACHINE, Campaign, CampaignStatus
from app.domains.campaigns.service import pick_style
from app.domains.contacts.models import ContactType
from app.domains.offers.models import OfferStyle, OutreachChannel
from app.domains.offers.service import campaign_contact, label_of


def _c(**kw) -> Campaign:
    base = {
        "send_days": [1, 2, 3, 4, 5],
        "send_from": time(10, 0),
        "send_to": time(18, 0),
        "timezone": "Europe/Moscow",  # UTC+3, no DST
        "starts_at": None,
        "daily_limit": 5,
        "status": CampaignStatus.RUNNING,
        "consecutive_failures": 0,
        "max_consecutive_failures": 3,
    }
    return Campaign(**(base | kw))


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


# 2026-11-09 is a Monday; 2026-11-14 a Saturday
def test_window_uses_the_campaigns_time_zone():
    c = _c()
    assert rules.in_window(c, utc(2026, 11, 9, 7, 0))  # 10:00 Moscow
    assert not rules.in_window(c, utc(2026, 11, 9, 6, 59))  # 09:59 Moscow
    assert not rules.in_window(c, utc(2026, 11, 9, 15, 0))  # 18:00 Moscow: end is exclusive
    assert not rules.in_window(c, utc(2026, 11, 14, 9, 0))  # Saturday


def test_next_window_start():
    c = _c()
    inside = utc(2026, 11, 9, 8, 30)
    assert rules.next_window_start(c, inside) == inside
    assert rules.next_window_start(c, utc(2026, 11, 9, 5, 0)) == utc(2026, 11, 9, 7, 0)  # later today
    assert rules.next_window_start(c, utc(2026, 11, 9, 16, 0)) == utc(2026, 11, 10, 7, 0)  # tomorrow
    assert rules.next_window_start(c, utc(2026, 11, 13, 16, 0)) == utc(2026, 11, 16, 7, 0)  # Fri → Mon
    weekend = _c(send_days=[6])
    assert rules.next_window_start(weekend, utc(2026, 11, 14, 20, 0)) == utc(2026, 11, 21, 7, 0)  # next Sat
    later = _c(starts_at=utc(2026, 11, 11, 12, 0))
    assert not rules.in_window(later, utc(2026, 11, 9, 8, 0))
    assert rules.next_window_start(later, utc(2026, 11, 9, 8, 0)) == utc(2026, 11, 11, 12, 0)


def test_local_day_and_blocked_reasons():
    c = _c(timezone="Asia/Tokyo")  # UTC+9
    assert rules.local_day_start(c, utc(2026, 11, 9, 16, 0)) == utc(2026, 11, 9, 15, 0)  # 00:00 Tokyo
    c = _c()
    open_ = utc(2026, 11, 9, 8, 0)
    assert rules.blocked_reason(c, open_, 0) is None
    assert rules.blocked_reason(c, open_, 5) == "campaign_daily_limit"
    assert rules.blocked_reason(c, utc(2026, 11, 14, 8, 0), 0) == "outside_window"
    assert rules.blocked_reason(_c(status=CampaignStatus.PAUSED), open_, 0) == "campaign_paused"
    assert set(rules.WAIT_MESSAGES) == {
        "campaign_paused",
        "campaign_closed",
        "outside_window",
        "campaign_daily_limit",
    }


def test_failures_in_a_row_pause_and_a_success_resets():
    c = _c(max_consecutive_failures=2)
    assert not rules.after_send(c, ok=False) and c.consecutive_failures == 1
    assert not rules.after_send(c, ok=True) and c.consecutive_failures == 0
    assert not rules.after_send(c, ok=False)
    assert rules.after_send(c, ok=False)
    assert c.status == CampaignStatus.PAUSED and "автопауза" in (c.paused_reason or "")
    assert not rules.after_send(c, ok=False)  # already paused: counted, not paused again


def test_campaign_status_machine():
    assert CAMPAIGN_MACHINE.can(CampaignStatus.RUNNING, CampaignStatus.PAUSED)
    assert CAMPAIGN_MACHINE.can(CampaignStatus.PAUSED, CampaignStatus.RUNNING)
    assert not CAMPAIGN_MACHINE.can(CampaignStatus.DRAFT, CampaignStatus.RUNNING)
    assert not CAMPAIGN_MACHINE.can(CampaignStatus.COMPLETED, CampaignStatus.RUNNING)
    assert CAMPAIGN_MACHINE.allowed(CampaignStatus.ARCHIVED) == set()


def test_ab_assignment_is_balanced():
    styles = [OfferStyle.PROFESSIONAL, OfferStyle.SHORT, OfferStyle.FREE_SAMPLE]
    counts: Counter[OfferStyle] = Counter()
    for _ in range(31):
        counts[pick_style(counts, styles)] += 1
    assert max(counts.values()) - min(counts.values()) <= 1 and sum(counts.values()) == 31
    assert (
        pick_style({OfferStyle.PROFESSIONAL: 4}, [OfferStyle.PROFESSIONAL, OfferStyle.SHORT])
        == OfferStyle.SHORT
    )
    assert [label_of(s) for s in styles] == ["A", "B", "C"]


class _Contact:
    def __init__(self, id_: int, type_: ContactType, usable: bool = True) -> None:
        self.id, self.type, self.usable = id_, type_, usable


def test_campaign_contact_follows_the_channel_order():
    tg, mail, dead = (
        _Contact(1, ContactType.TELEGRAM),
        _Contact(2, ContactType.EMAIL),
        _Contact(3, ContactType.EMAIL, False),
    )
    contacts = [tg, mail]
    E, T, M = OutreachChannel.EMAIL, OutreachChannel.TELEGRAM, OutreachChannel.MANUAL
    assert campaign_contact(contacts, [E, T]) == (mail, E)  # type: ignore[arg-type]
    assert campaign_contact(contacts, [T, E]) == (tg, T)  # type: ignore[arg-type]
    assert campaign_contact([dead], [E]) is None  # type: ignore[list-item]
    assert campaign_contact([dead, tg], [E, M]) == (tg, M)  # type: ignore[list-item]
    assert campaign_contact([], [M]) is None
