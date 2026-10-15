"""Deterministic, explainable Lead Score and Priority Score (ТЗ §8, §52–53; AI_ARCHITECTURE §4).

Pure functions — no database, no AI. Every component yields a *fraction* 0…1 (or ``None`` = no data) and a
short reason; a score multiplies the fractions by the workspace's weights:

* **Lead Score** — weight = the component's maximum points (defaults sum to 100, e.g. «Thumbnail
  Opportunity 23/25»); ``total = Σ points / Σ max · 100``.
* **Priority Score** (§53, «smart prioritization») — a second, independent set of weights over the same
  fractions, ``Σ wᵢ·fᵢ / Σ wᵢ · 100``; «commercial potential» of the ТЗ is the niche value component.

A component without data scores 0 points and says so («нет данных»): a lead is never ranked up because
something is unknown. ``data_coverage`` = share of the Lead Score maximum backed by data.

Contactability (Phase 6) is the ТЗ §19 Contactability Score of the channel's usable contacts
(``contacts.scoring``) / 100; no data until the channel's texts were scanned, and a scan that found nothing
is real data (0, «not_found»). Its default weight in the Lead / Priority Score stays 0: giving it points
changes every lead's score, so it is the workspace's choice (scoring settings).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.domains.contacts import scoring as contact_scoring

COMPONENTS: tuple[str, ...] = (
    "thumbnail_opportunity",
    "channel_fit",
    "audience",
    "views",
    "activity",
    "growth",
    "contactability",
    "niche_value",
)

# Maximum points per component; the example of ТЗ §8 (25 / 20 / 20 / 20 / 15) adapted to the components
# that have data before Phase 6 (contactability 0).
DEFAULT_LEAD_WEIGHTS: dict[str, float] = {
    "thumbnail_opportunity": 25,
    "channel_fit": 20,
    "audience": 15,
    "views": 10,
    "activity": 15,
    "growth": 5,
    "contactability": 0,
    "niche_value": 10,
}
# ТЗ §53: thumbnail opportunity + channel fit + views + activity + contactability + commercial potential.
DEFAULT_PRIORITY_WEIGHTS: dict[str, float] = {
    "thumbnail_opportunity": 30,
    "channel_fit": 20,
    "audience": 0,
    "views": 20,
    "activity": 20,
    "growth": 0,
    "contactability": 0,
    "niche_value": 10,
}

LABELS: dict[str, str] = {
    "thumbnail_opportunity": "Thumbnail Opportunity",
    "channel_fit": "Channel Fit",
    "audience": "Audience",
    "views": "Views",
    "activity": "Activity",
    "growth": "Growth Signals",
    "contactability": "Contactability",
    "niche_value": "Niche Value",
}

# Scales (documented in AI_ARCHITECTURE §4).
AUDIENCE_RANGE = (1_000, 1_000_000)  # subscribers: 1k → 0, 1M → 1 (log scale)
VIEWS_RANGE = (500, 500_000)  # median views of the stored recent videos: 500 → 0, 500k → 1 (log scale)
UPLOADS_FOR_FULL_FREQUENCY = 4  # videos in the last 30 days
RECENCY_STEPS: tuple[tuple[int, float], ...] = ((7, 1.0), (14, 0.85), (30, 0.6), (60, 0.3), (90, 0.15))
OPPORTUNITY_FRACTION = {"low": 0.2, "medium": 0.6, "high": 1.0}
_Q2 = Decimal("0.01")


class ActivityLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


ACTIVITY_ORDER = {ActivityLevel.LOW: 0, ActivityLevel.MEDIUM: 1, ActivityLevel.HIGH: 2}
HIGH_ACTIVITY_DAYS = 14  # last upload within … and at least UPLOADS_FOR_FULL_FREQUENCY uploads in 30 days
MEDIUM_ACTIVITY_DAYS = 30


def activity_level(
    last_video_at: datetime | None, videos_30d: int | None, now: datetime
) -> ActivityLevel | None:
    """«Activity: active / high» of ТЗ §9 and §52 (the SQL twin is ``service.activity_level_sql``)."""
    if last_video_at is None:
        return None
    days = (now - last_video_at).total_seconds() / 86400
    if days <= HIGH_ACTIVITY_DAYS and (videos_30d or 0) >= UPLOADS_FOR_FULL_FREQUENCY:
        return ActivityLevel.HIGH
    if days <= MEDIUM_ACTIVITY_DAYS:
        return ActivityLevel.MEDIUM
    return ActivityLevel.LOW


# ---------------------------------------------------------------------------
# Ideal Lead Profile (§52)
# ---------------------------------------------------------------------------
CountryCode = Annotated[str, StringConstraints(to_upper=True, pattern=r"^[A-Za-z]{2}$")]
LanguageCode = Annotated[
    str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=2, max_length=20)
]


class IdealLeadCriteria(BaseModel):
    """What a perfect lead looks like; Channel Fit = share of the set criteria the channel meets. An
    unset criterion is ignored; a set one without data (e.g. thumbnails not audited yet) counts as not met.
    ``contact_types`` — the channel has a usable contact of any of these types (no data until its contacts
    were scanned)."""

    model_config = ConfigDict(extra="forbid")
    min_subscribers: int | None = Field(default=None, ge=0)
    max_subscribers: int | None = Field(default=None, ge=0)
    min_avg_views: int | None = Field(default=None, ge=0)
    min_activity: ActivityLevel | None = None
    max_days_since_last_upload: int | None = Field(default=None, ge=0, le=3650)
    max_thumbnail_score: float | None = Field(default=None, ge=1, le=10)  # average of audited thumbnails
    niches: list[int] = Field(default_factory=list, max_length=50)  # any of them (with descendants)
    countries: list[CountryCode] = Field(default_factory=list, max_length=50)
    languages: list[LanguageCode] = Field(default_factory=list, max_length=20)
    contact_types: list[Literal["email", "telegram"]] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def _ordered(self) -> IdealLeadCriteria:
        if (
            self.min_subscribers is not None
            and self.max_subscribers is not None
            and self.min_subscribers > self.max_subscribers
        ):
            raise ValueError("min_subscribers must not be greater than max_subscribers")
        return self


# ---------------------------------------------------------------------------
# Inputs and components
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LeadInputs:
    """Everything the score depends on, gathered by ``service.gather_inputs``."""

    subscribers: int | None = None
    subscribers_hidden: bool = False
    country: str | None = None
    language: str | None = None
    has_metrics: bool = False
    avg_views: int | None = None
    median_views: int | None = None
    last_video_at: datetime | None = None
    videos_30d: int | None = None
    views_trend: float | None = None  # newer half of the window vs older half: +0.5 = +50 %
    subscriber_growth_30d: float | None = None  # from stats snapshots, per 30 days: 0.05 = +5 %
    thumbnails_audited: int = 0
    avg_thumbnail_score: Decimal | None = None  # workspace thumbnail weights; human overrides win (§47)
    avg_redesign_potential: Decimal | None = None
    thumbnails_human_scored: int = 0  # of thumbnails_audited: with a human overall / redesign value
    visual_opportunity: str | None = None  # current comparative channel audit (3.6)
    niche_ids: frozenset[int] = field(default_factory=frozenset)  # discovery niches + their ancestors
    contacts_scanned: bool = False  # the channel's texts were scanned for contacts (Phase 6)
    contact_types: frozenset[str] = field(default_factory=frozenset)  # types of usable contacts (workspace)


@dataclass(frozen=True)
class Component:
    key: str
    fraction: float | None  # 0…1; None = no data
    reason: str
    inputs: dict[str, Any] = field(default_factory=dict)


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _log_scale(value: float, lo: float, hi: float) -> float:
    if value <= lo:
        return 0.0
    return _clamp(math.log10(value / lo) / math.log10(hi / lo))


def _n(x: int | float | None) -> str:
    if x is None:
        return "—"
    return f"{int(round(x)):,}".replace(",", " ")


def _pct(x: float) -> str:
    return f"{x * 100:+.0f} %"


def _days_since(at: datetime | None, now: datetime) -> int | None:
    return None if at is None else max(0, int((now - at).total_seconds() // 86400))


def thumbnail_opportunity(i: LeadInputs) -> Component:
    if not i.thumbnails_audited or i.avg_thumbnail_score is None or i.avg_redesign_potential is None:
        return Component("thumbnail_opportunity", None, "нет данных: превью канала ещё не проанализированы")
    avg = float(i.avg_thumbnail_score)
    redesign = float(i.avg_redesign_potential)
    gap = _clamp((10 - avg) / 9)
    potential = _clamp((redesign - 1) / 9)
    inputs: dict[str, Any] = {
        "thumbnails_audited": i.thumbnails_audited,
        "avg_thumbnail_score": round(avg, 2),
        "avg_redesign_potential": round(redesign, 2),
        "visual_opportunity": i.visual_opportunity,
    }
    reason = (
        f"средний балл превью {avg:.2f}/10 по {i.thumbnails_audited} превью, "
        f"потенциал редизайна {redesign:.1f}/10"
    )
    if i.thumbnails_human_scored:
        inputs["thumbnails_human_scored"] = i.thumbnails_human_scored
        reason += f" (ручная оценка у {i.thumbnails_human_scored})"
    if i.visual_opportunity in OPPORTUNITY_FRACTION:
        f = 0.5 * gap + 0.25 * potential + 0.25 * OPPORTUNITY_FRACTION[i.visual_opportunity]
        reason += f", визуальная возможность канала: {i.visual_opportunity}"
    else:
        f = 0.6 * gap + 0.4 * potential
        reason += "; аудита канала нет"
    return Component("thumbnail_opportunity", _clamp(f), reason, inputs)


def audience(i: LeadInputs) -> Component:
    if i.subscribers_hidden or i.subscribers is None:
        return Component("audience", None, "нет данных: число подписчиков скрыто или неизвестно")
    lo, hi = AUDIENCE_RANGE
    return Component(
        "audience",
        _log_scale(i.subscribers, lo, hi),
        f"{_n(i.subscribers)} подписчиков (шкала {_n(lo)} → {_n(hi)})",
        {"subscribers": i.subscribers},
    )


def views(i: LeadInputs) -> Component:
    if i.median_views is None:
        return Component("views", None, "нет данных: метрики просмотров ещё не посчитаны")
    lo, hi = VIEWS_RANGE
    return Component(
        "views",
        _log_scale(i.median_views, lo, hi),
        f"медиана просмотров {_n(i.median_views)} (шкала {_n(lo)} → {_n(hi)})",
        {"median_views": i.median_views, "avg_views": i.avg_views},
    )


def activity(i: LeadInputs, now: datetime) -> Component:
    days = _days_since(i.last_video_at, now)
    if days is None:
        return Component("activity", None, "нет данных: дата последнего видео неизвестна")
    recency = next((f for limit, f in RECENCY_STEPS if days <= limit), 0.0)
    uploads = i.videos_30d or 0
    frequency = _clamp(uploads / UPLOADS_FOR_FULL_FREQUENCY)
    level = activity_level(i.last_video_at, i.videos_30d, now)
    return Component(
        "activity",
        _clamp(0.6 * recency + 0.4 * frequency),
        f"последнее видео {days} дн. назад, {uploads} видео за 30 дней",
        {"days_since_last_upload": days, "videos_30d": uploads, "level": level.value if level else None},
    )


def growth(i: LeadInputs) -> Component:
    parts: list[float] = []
    reasons: list[str] = []
    if i.views_trend is not None:
        parts.append(_clamp(0.5 + i.views_trend))
        reasons.append(f"тренд просмотров {_pct(i.views_trend)} (новые видео к старым)")
    if i.subscriber_growth_30d is not None:
        parts.append(_clamp(0.5 + 10 * i.subscriber_growth_30d))
        reasons.append(f"подписчики {_pct(i.subscriber_growth_30d)} за 30 дней")
    if not parts:
        return Component("growth", None, "нет данных: мало видео для тренда и нет истории подписчиков")
    return Component(
        "growth",
        sum(parts) / len(parts),
        ", ".join(reasons),
        {"views_trend": i.views_trend, "subscriber_growth_30d": i.subscriber_growth_30d},
    )


def contactability(i: LeadInputs, weights: Mapping[str, float] | None = None) -> Component:
    """ТЗ §19 Contactability Score / 100; ``inputs.score`` is the score itself (stored on the lead score)."""
    if not i.contacts_scanned:
        return Component("contactability", None, "нет данных: контакты канала ещё не собирались")
    w = weights if weights is not None else contact_scoring.DEFAULT_CONTACTABILITY_WEIGHTS
    value = contact_scoring.contactability_score(i.contact_types, w)
    types = sorted(i.contact_types)
    if not types:
        return Component(
            "contactability", 0.0, "публичные контакты не найдены (not_found)", {"score": "0.00", "types": []}
        )
    return Component(
        "contactability",
        _clamp(float(value) / 100),
        f"Contactability {float(value):g}/100: {contact_scoring.describe(types, w)}",
        {"score": str(value), "types": types},
    )


def niche_value(i: LeadInputs, values: Mapping[int, float]) -> Component:
    if not values:
        return Component("niche_value", None, "нет данных: ценность ниш не задана")
    matched = {n: values[n] for n in i.niche_ids if n in values}
    if not matched:
        return Component("niche_value", None, "нет данных: у ниш канала нет оценки ценности")
    node, best = max(matched.items(), key=lambda kv: (kv[1], -kv[0]))
    return Component(
        "niche_value",
        _clamp(best / 10),
        f"ценность ниши {best:g}/10",
        {"taxonomy_node_id": node, "value": best},
    )


def _language_matches(channel_language: str | None, wanted: list[str]) -> bool:
    if channel_language is None:
        return False
    lang = channel_language.lower()
    return any(lang == w or lang.startswith(f"{w}-") for w in wanted)


def channel_fit(i: LeadInputs, ilp: IdealLeadCriteria | None, now: datetime) -> Component:
    if ilp is None:
        return Component("channel_fit", None, "нет данных: профиль идеального лида не задан")
    checks: list[tuple[str, bool | None]] = []  # (criterion, met; None = no data)
    subs = None if i.subscribers_hidden else i.subscribers
    if ilp.min_subscribers is not None or ilp.max_subscribers is not None:
        lo, hi = ilp.min_subscribers, ilp.max_subscribers
        in_range = None if subs is None else (lo is None or subs >= lo) and (hi is None or subs <= hi)
        checks.append(
            (f"подписчики {_n(lo) if lo is not None else '0'}–{_n(hi) if hi is not None else '∞'}", in_range)
        )
    if ilp.min_avg_views is not None:
        checks.append(
            (
                f"средние просмотры ≥ {_n(ilp.min_avg_views)}",
                None if i.avg_views is None else i.avg_views >= ilp.min_avg_views,
            )
        )
    if ilp.min_activity is not None:
        level = activity_level(i.last_video_at, i.videos_30d, now)
        checks.append(
            (
                f"активность ≥ {ilp.min_activity.value}",
                None if level is None else ACTIVITY_ORDER[level] >= ACTIVITY_ORDER[ilp.min_activity],
            )
        )
    if ilp.max_days_since_last_upload is not None:
        days = _days_since(i.last_video_at, now)
        checks.append(
            (
                f"последнее видео ≤ {ilp.max_days_since_last_upload} дн.",
                None if days is None else days <= ilp.max_days_since_last_upload,
            )
        )
    if ilp.max_thumbnail_score is not None:
        avg = i.avg_thumbnail_score
        checks.append(
            (
                f"балл превью ≤ {ilp.max_thumbnail_score:g}",
                None if avg is None else float(avg) <= ilp.max_thumbnail_score,
            )
        )
    if ilp.niches:
        checks.append(("ниша из профиля", bool(i.niche_ids & set(ilp.niches)) if i.niche_ids else None))
    if ilp.countries:
        checks.append(
            ("страна из профиля", None if i.country is None else i.country.upper() in ilp.countries)
        )
    if ilp.languages:
        checks.append(
            (
                "язык из профиля",
                None if i.language is None else _language_matches(i.language, ilp.languages),
            )
        )
    if ilp.contact_types:
        checks.append(
            (
                "контакт: " + " или ".join(ilp.contact_types),
                bool(i.contact_types & set(ilp.contact_types)) if i.contacts_scanned else None,
            )
        )
    if not checks:
        return Component("channel_fit", None, "нет данных: в профиле идеального лида нет критериев")
    met = [name for name, ok in checks if ok]
    missed = [name for name, ok in checks if ok is False]
    unknown = [name for name, ok in checks if ok is None]
    parts = [f"совпало {len(met)} из {len(checks)} критериев"]
    if missed:
        parts.append("не совпало: " + ", ".join(missed))
    if unknown:
        parts.append("нет данных: " + ", ".join(unknown))
    return Component(
        "channel_fit",
        len(met) / len(checks),
        "; ".join(parts),
        {"met": met, "missed": missed, "unknown": unknown},
    )


def compute_components(
    i: LeadInputs,
    ilp: IdealLeadCriteria | None,
    niche_values: Mapping[int, float],
    now: datetime,
    contact_weights: Mapping[str, float] | None = None,
) -> dict[str, Component]:
    comps = [
        thumbnail_opportunity(i),
        channel_fit(i, ilp, now),
        audience(i),
        views(i),
        activity(i, now),
        growth(i),
        contactability(i, contact_weights),
        niche_value(i, niche_values),
    ]
    return {c.key: c for c in comps}


# ---------------------------------------------------------------------------
# Scores
# ---------------------------------------------------------------------------
def _d(x: float) -> Decimal:
    return Decimal(str(x)).quantize(_Q2, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class ScoreBreakdown:
    total: Decimal  # Lead Score 0…100
    priority: Decimal  # Priority Score 0…100
    points: dict[str, Decimal]  # Lead Score points per component (max = its lead weight)
    data_coverage: Decimal  # % of the Lead Score maximum backed by data
    components: list[dict[str, Any]]  # explanation, in COMPONENTS order


def weighted(fractions: Mapping[str, float | None], weights: Mapping[str, float]) -> Decimal:
    """Σ wᵢ·fᵢ / Σ wᵢ · 100 (no data = 0)."""
    total_w = sum(weights.get(k, 0.0) for k in COMPONENTS)
    if total_w <= 0:
        raise ValueError("at least one weight must be positive")
    acc = sum(weights.get(k, 0.0) * (fractions.get(k) or 0.0) for k in COMPONENTS)
    return _d(100 * acc / total_w)


def score(
    comps: Mapping[str, Component], lead_weights: Mapping[str, float], priority_weights: Mapping[str, float]
) -> ScoreBreakdown:
    fractions = {k: comps[k].fraction for k in COMPONENTS}
    points = {k: _d(lead_weights.get(k, 0.0) * (fractions[k] or 0.0)) for k in COMPONENTS}
    max_total = sum(lead_weights.get(k, 0.0) for k in COMPONENTS)
    covered = sum(lead_weights.get(k, 0.0) for k in COMPONENTS if fractions[k] is not None)
    explanation: list[dict[str, Any]] = []
    for k in COMPONENTS:
        fraction = fractions[k]
        explanation.append(
            {
                "key": k,
                "label": LABELS[k],
                "points": str(points[k]),
                "max": str(_d(lead_weights.get(k, 0.0))),
                "fraction": None if fraction is None else round(fraction, 4),
                "has_data": fraction is not None,
                "reason": comps[k].reason,
                "inputs": comps[k].inputs,
                "priority_weight": str(_d(priority_weights.get(k, 0.0))),
            }
        )
    return ScoreBreakdown(
        total=weighted(fractions, lead_weights),
        priority=weighted(fractions, priority_weights),
        points=points,
        data_coverage=_d(100 * covered / max_total) if max_total > 0 else Decimal(0),
        components=explanation,
    )
