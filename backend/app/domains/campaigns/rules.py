"""Campaign send rules used by the dispatcher and the send job (ТЗ §27 «campaign limit», §59).

* **Window** — a campaign sends only on its weekdays between ``send_from`` and ``send_to`` in its own time
  zone and not before ``starts_at``; ``next_window_start`` is the earliest moment a message may go.
* **Daily limit** — campaign messages sent (or being sent) since the start of the campaign's *local* day.
* **Pause** — messages of a paused campaign stay queued (nothing is cancelled); resume sends them.
* **Auto-pause** — ``max_consecutive_failures`` final failures in a row (bounces included) pause the
  campaign with a reason; a successful send resets the counter.

Only ``campaigns.models`` and ``outreach.models`` are imported: the dispatcher uses this module directly.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.campaigns.models import Campaign, CampaignStatus
from app.domains.outreach.models import Message, MessageStatus

WINDOW_SEARCH_DAYS = 8  # one full week + today


def _zone(campaign: Campaign) -> ZoneInfo:
    return ZoneInfo(campaign.timezone)


def _at(day: date, t: time, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, t, tzinfo=tz)


def in_window(campaign: Campaign, now: datetime) -> bool:
    if campaign.starts_at is not None and now < campaign.starts_at:
        return False
    local = now.astimezone(_zone(campaign))
    if local.isoweekday() not in campaign.send_days:
        return False
    return campaign.send_from <= local.time().replace(tzinfo=None) < campaign.send_to


def next_window_start(campaign: Campaign, now: datetime) -> datetime:
    """The earliest moment ≥ ``now`` (and ≥ ``starts_at``) inside the window, in UTC."""
    base = max(now, campaign.starts_at) if campaign.starts_at is not None else now
    tz = _zone(campaign)
    local = base.astimezone(tz)
    for offset in range(WINDOW_SEARCH_DAYS):
        day = local.date() + timedelta(days=offset)
        if day.isoweekday() not in campaign.send_days:
            continue
        start, end = _at(day, campaign.send_from, tz), _at(day, campaign.send_to, tz)
        if local >= end:
            continue
        return max(local, start).astimezone(UTC)
    return base.astimezone(UTC)  # unreachable with ≥ 1 send day; a safe fallback


def local_day_start(campaign: Campaign, now: datetime) -> datetime:
    local = now.astimezone(_zone(campaign))
    return _at(local.date(), time(0, 0), _zone(campaign)).astimezone(UTC)


async def sent_today(db: AsyncSession, campaigns: Sequence[Campaign], now: datetime) -> dict[int, int]:
    """Campaign messages sent (or being sent) since the start of each campaign's local day."""
    out: dict[int, int] = {}
    for c in campaigns:
        n = await db.scalar(
            select(func.count())
            .select_from(Message)
            .where(
                Message.campaign_id == c.id,
                Message.status.in_([MessageStatus.SENT, MessageStatus.SENDING]),
                func.coalesce(Message.sent_at, Message.updated_at) >= local_day_start(c, now),
            )
        )
        out[c.id] = int(n or 0)
    return out


def blocked_reason(campaign: Campaign, now: datetime, used_today: int) -> str | None:
    """Why a queued message of this campaign must wait right now (None = it may go)."""
    if campaign.status == CampaignStatus.PAUSED:
        return "campaign_paused"
    if campaign.status in (CampaignStatus.COMPLETED, CampaignStatus.ARCHIVED):
        return "campaign_closed"
    if not in_window(campaign, now):
        return "outside_window"
    if used_today >= campaign.daily_limit:
        return "campaign_daily_limit"
    return None


WAIT_MESSAGES = {
    "campaign_paused": "Кампания на паузе: письмо ждёт в очереди.",
    "campaign_closed": "Кампания завершена.",
    "outside_window": "Вне окна отправки кампании: письмо уйдёт в ближайшее окно.",
    "campaign_daily_limit": "Дневной лимит кампании исчерпан: письмо уйдёт завтра.",
}


def after_send(campaign: Campaign, ok: bool) -> bool:
    """Count a final result of a campaign message; returns True if the campaign was auto-paused now."""
    if ok:
        campaign.consecutive_failures = 0
        return False
    campaign.consecutive_failures += 1
    if (
        campaign.status == CampaignStatus.RUNNING
        and campaign.consecutive_failures >= campaign.max_consecutive_failures
    ):
        campaign.status = CampaignStatus.PAUSED
        campaign.paused_reason = (
            f"автопауза: {campaign.consecutive_failures} писем подряд не отправлены (ошибки / недоставки)"
        )
        return True
    return False
