"""Pure helpers of the analytics domain (no database): rates, lead funnel levels, score buckets, outcome
counts and the honest A/B comparison (ТЗ §28: no conclusions where the data is insufficient)."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from app.core.state_machine import LeadStatus
from app.domains.outreach.models import MessageEventType

RATE_Q = Decimal("0.0001")
USD_Q = Decimal("0.000001")
SIGNIFICANCE = 0.05  # two-sided p-value below which a difference is reported as more than noise

REPLY_EVENTS = frozenset(
    {MessageEventType.REPLIED, MessageEventType.POSITIVE_REPLY, MessageEventType.NEGATIVE_REPLY}
)
ANSWERED_EVENTS = REPLY_EVENTS | {MessageEventType.OPT_OUT, MessageEventType.BOUNCED}


def rate(part: int, whole: int) -> Decimal | None:
    return (Decimal(part) / Decimal(whole)).quantize(RATE_Q) if whole else None


def per_unit(cost: Decimal, units: int) -> Decimal | None:
    return (cost / Decimal(units)).quantize(USD_Q) if units else None


# ---------------------------------------------------------------------------
# Lead funnel
# ---------------------------------------------------------------------------
# How far a lead got. Levels are nested: a lead at level N has passed every lower level. The status
# history (every transition, ADR-0011) and the current status give 0–5; messages give 6–8.
FOUND, ANALYZED, SHORTLISTED, SELECTED, OFFER, APPROVED, SENT, REPLIED, POSITIVE = range(9)
STATUS_LEVEL: dict[LeadStatus, int] = {
    LeadStatus.DISCOVERED: FOUND,
    LeadStatus.ANALYZED: ANALYZED,
    LeadStatus.SHORTLISTED: SHORTLISTED,
    LeadStatus.SELECTED: SELECTED,
    LeadStatus.GENERATION_PENDING: SELECTED,
    LeadStatus.GENERATED: SELECTED,
    LeadStatus.OFFER_DRAFT: OFFER,
    LeadStatus.PENDING_APPROVAL: OFFER,
    LeadStatus.APPROVED: APPROVED,
    LeadStatus.SCHEDULED: APPROVED,
    LeadStatus.SENT: SENT,
    LeadStatus.REPLIED: REPLIED,
    LeadStatus.DECLINED: REPLIED,  # a refusal is an answer
    # exits say nothing about progress: the earlier statuses in the history do
    LeadStatus.FAILED: FOUND,
    LeadStatus.REJECTED: FOUND,
    LeadStatus.OPTED_OUT: FOUND,
}
FUNNEL: tuple[tuple[int, str, str], ...] = (
    (FOUND, "found", "Найдено"),
    (ANALYZED, "analyzed", "Проанализировано"),
    (SELECTED, "selected", "Выбрано"),
    (OFFER, "offer", "Оффер написан"),
    (APPROVED, "approved", "Подтверждено"),
    (SENT, "sent", "Отправлено"),
    (REPLIED, "replied", "Ответили"),
    (POSITIVE, "positive", "Положительный ответ"),
)


def lead_level(statuses: Iterable[LeadStatus], events: set[MessageEventType] | None) -> int:
    """``events`` — union of the events of the lead's *sent* messages (None = nothing was sent)."""
    statuses = list(statuses)
    level = max((STATUS_LEVEL[s] for s in statuses), default=FOUND)
    if events is not None:
        level = max(level, SENT)
        if events & REPLY_EVENTS or {LeadStatus.REPLIED, LeadStatus.DECLINED} & set(statuses):
            level = max(level, REPLIED)
        if MessageEventType.POSITIVE_REPLY in events:
            level = POSITIVE
    else:
        level = min(level, APPROVED)  # without a sent message the lead cannot be further than approved
    return level


# ---------------------------------------------------------------------------
# Buckets
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Bucket:
    key: str
    label: str
    low: Decimal
    high: Decimal  # exclusive, except the last bucket

    def contains(self, value: Decimal, *, last: bool) -> bool:
        return self.low <= value < self.high or (last and value == self.high)


def _buckets(edges: Sequence[int]) -> tuple[Bucket, ...]:
    pairs = zip(edges, edges[1:], strict=False)
    return tuple(Bucket(f"{lo}-{hi}", f"{lo}–{hi}", Decimal(lo), Decimal(hi)) for lo, hi in pairs)


# average thumbnail score of the channel, 1–10 (ТЗ §39 «Best Thumbnail Score Range»)
THUMBNAIL_BUCKETS = _buckets((1, 3, 4, 5, 6, 7, 8, 10))
# Lead Score 0–100 at the moment of a manual decision (ТЗ §46)
LEAD_SCORE_BANDS = _buckets((0, 20, 40, 60, 80, 100))


def bucket_of(value: Decimal | None, buckets: Sequence[Bucket]) -> Bucket | None:
    if value is None:
        return None
    for i, b in enumerate(buckets):
        if b.contains(value, last=i == len(buckets) - 1):
            return b
    return None


# ---------------------------------------------------------------------------
# Outcomes of sent messages / contacted leads
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Counts:
    sent: int = 0
    delivered: int = 0
    opened: int = 0
    replied: int = 0
    positive_reply: int = 0
    negative_reply: int = 0
    bounced: int = 0
    opt_out: int = 0
    no_reply: int = 0


def count_outcomes(units: Iterable[set[MessageEventType]]) -> Counts:
    """One event set per sent unit (a message, or a contacted lead: the union of its messages)."""
    n = {k: 0 for k in Counts.__dataclass_fields__}
    for ev in units:
        n["sent"] += 1
        n["delivered"] += MessageEventType.DELIVERED in ev
        n["opened"] += MessageEventType.OPENED in ev
        n["replied"] += bool(ev & REPLY_EVENTS)
        n["positive_reply"] += MessageEventType.POSITIVE_REPLY in ev
        n["negative_reply"] += MessageEventType.NEGATIVE_REPLY in ev
        n["bounced"] += MessageEventType.BOUNCED in ev
        n["opt_out"] += MessageEventType.OPT_OUT in ev
        n["no_reply"] += not ev & ANSWERED_EVENTS
    return Counts(**n)


# ---------------------------------------------------------------------------
# Comparison of groups (A/B, niches, campaigns …)
# ---------------------------------------------------------------------------
Verdict = Literal["insufficient_data", "no_difference", "leader"]


def two_proportion_p(x1: int, n1: int, x2: int, n2: int) -> float | None:
    """Two-sided p-value of the two-proportion z-test (normal approximation); None = not computable."""
    if not n1 or not n2:
        return None
    pooled = (x1 + x2) / (n1 + n2)
    var = pooled * (1 - pooled) * (1 / n1 + 1 / n2)
    if var <= 0:
        return 1.0  # both 0 % or both 100 %: no difference to speak of
    z = (x1 / n1 - x2 / n2) / math.sqrt(var)
    return math.erfc(abs(z) / math.sqrt(2))


@dataclass(frozen=True)
class Comparison:
    verdict: Verdict
    leader: str | None  # key of the best group (also for «no_difference»: who is ahead, for display)
    p_value: Decimal | None


def compare(groups: Sequence[tuple[str, int, int]], min_sample: int) -> Comparison:
    """``groups`` = (key, successes, trials). Only groups with at least ``min_sample`` trials take part;
    fewer than two such groups => ``insufficient_data``. The leader is the highest rate; it is a
    ``leader`` only if its difference from the runner-up is significant, otherwise ``no_difference``."""
    eligible = sorted(
        (g for g in groups if g[2] >= min_sample), key=lambda g: (g[1] / g[2], g[2]), reverse=True
    )
    if len(eligible) < 2:
        return Comparison("insufficient_data", None, None)
    (k1, x1, n1), (_, x2, n2) = eligible[0], eligible[1]
    p = two_proportion_p(x1, n1, x2, n2)
    p_dec = Decimal(str(round(p, 4))) if p is not None else None
    if p is not None and p < SIGNIFICANCE and x1 / n1 > x2 / n2:
        return Comparison("leader", k1, p_dec)
    return Comparison("no_difference", k1, p_dec)


def best_of(groups: Sequence[tuple[str, int, int]], min_sample: int) -> str | None:
    """Key of the group with the highest rate among those with at least ``min_sample`` trials (None =
    no group has enough data). For «best niche / campaign» tiles: a ranking, not a causal claim."""
    eligible = [g for g in groups if g[2] >= min_sample]
    if not eligible:
        return None
    return max(eligible, key=lambda g: (g[1] / g[2], g[2]))[0]
