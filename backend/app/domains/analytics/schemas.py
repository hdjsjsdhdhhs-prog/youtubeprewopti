"""Analytics responses (Phase 9). Every rate is a plain ratio (0–1, 4 decimals); None = nothing to
divide by."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from app.domains.campaigns.models import CampaignStatus
from app.domains.feedback.schemas import FeedbackSummary
from app.domains.offers.models import OfferStyle


class PeriodOut(BaseModel):
    date_from: date | None  # inclusive, UTC
    date_to: date | None  # inclusive, UTC
    project_id: int | None


class Outcomes(BaseModel):
    """What happened after sending (ТЗ §28). Events after the send are recorded by the operator (SMTP reports
    none of them); «opened» is only what was marked by hand — there is no tracking pixel (ADR-0010)."""

    sent: int
    delivered: int
    opened: int
    replied: int  # any reply (replied / positive / negative)
    positive_reply: int
    negative_reply: int
    bounced: int
    opt_out: int
    no_reply: int  # sent, nothing recorded yet
    reply_rate: Decimal | None  # replied / sent
    positive_rate: Decimal | None  # positive / sent
    small_sample: bool  # fewer than ``min_sample`` sent: the rates mean nothing yet


class Best(BaseModel):
    """The top group by reply rate among those with at least ``min_sample`` sent (a ranking, not a cause)."""

    key: str
    name: str
    sent: int
    reply_rate: Decimal | None


# ---------------------------------------------------------------------------
# Overview / Dashboard (ТЗ §39)
# ---------------------------------------------------------------------------
class Tiles(BaseModel):
    total_channels: int  # leads of the workspace / project, all time
    new_channels: int  # leads found in the period (the cohort every count below is about)
    analyzed: int
    selected: int
    generated_samples: int  # leads with at least one generated free sample
    offers_pending_approval: int  # now, in the Approval Queue (not limited to the period)
    sent: int  # leads contacted
    replies: int
    positive_replies: int
    reply_rate: Decimal | None  # replies / sent
    conversion: Decimal | None  # positive replies / sent


class FunnelStep(BaseModel):
    key: str
    label: str
    leads: int
    rate_from_previous: Decimal | None
    rate_from_start: Decimal | None


class CostSummary(BaseModel):
    """AI spend (ТЗ §75): Σ actual cost, the estimate where the actual is unknown (as in budgets)."""

    total_usd: Decimal
    analysis_usd: Decimal  # thumbnail / channel audits, classification, explanations
    generation_usd: Decimal  # free sample images
    offers_usd: Decimal  # offer texts
    other_usd: Decimal
    calls: int
    unpriced_calls: int  # neither actual nor estimated cost known: the totals are a lower bound
    per_lead_usd: Decimal | None  # total / leads found in the period
    per_analyzed_lead_usd: Decimal | None
    per_sent_usd: Decimal | None
    per_reply_usd: Decimal | None
    per_positive_usd: Decimal | None


class Overview(BaseModel):
    period: PeriodOut
    tiles: Tiles
    funnel: list[FunnelStep]
    costs: CostSummary
    best_niche: Best | None
    best_score_range: Best | None
    best_campaign: Best | None
    best_offer_variant: Best | None
    min_sample: int
    notes: list[str]


# ---------------------------------------------------------------------------
# Costs (ТЗ §75)
# ---------------------------------------------------------------------------
class CostRow(BaseModel):
    key: str  # AI task / model key
    provider: str | None = None  # by model only
    calls: int
    failed_calls: int  # error / invalid output / refused
    unpriced_calls: int
    cost_usd: Decimal
    input_tokens: int
    output_tokens: int
    image_inputs: int
    image_outputs: int
    avg_duration_ms: int | None


class CostReport(BaseModel):
    period: PeriodOut
    summary: CostSummary
    by_task: list[CostRow]
    by_model: list[CostRow]


# ---------------------------------------------------------------------------
# Segments (ТЗ §51, §39 «Best Thumbnail Score Range»)
# ---------------------------------------------------------------------------
class NicheRow(BaseModel):
    niche_id: int | None  # None = found by queries without a niche
    name: str
    leads: int
    analyzed: int
    selected: int
    avg_thumbnail_score: Decimal | None
    weak_thumbnails: int  # average thumbnail score below the «weak-thumbnails» threshold
    high_opportunity: int  # Lead Score at or above the «high-potential» threshold
    with_contacts: int  # a usable contact is known
    outcomes: Outcomes  # per contacted lead


class NicheReport(BaseModel):
    period: PeriodOut
    items: list[NicheRow]
    best: Best | None
    min_sample: int
    note: str


class ScoreRangeRow(BaseModel):
    key: str  # "5-6"; "none" = no thumbnail audit yet
    label: str
    low: Decimal | None
    high: Decimal | None
    leads: int
    selected: int
    selection_rate: Decimal | None  # selected / leads
    outcomes: Outcomes


class ScoreRangeReport(BaseModel):
    period: PeriodOut
    items: list[ScoreRangeRow]
    best: Best | None
    min_sample: int
    note: str


# ---------------------------------------------------------------------------
# Campaigns and A/B (ТЗ §28–29)
# ---------------------------------------------------------------------------
class CampaignRow(BaseModel):
    id: int
    name: str
    status: CampaignStatus
    leads: int  # not removed
    approved: int  # offers approved (incl. queued / sent)
    cost_usd: Decimal  # offer drafts written for the campaign's leads
    cost_per_reply_usd: Decimal | None
    outcomes: Outcomes  # per sent message


class CampaignReport(BaseModel):
    period: PeriodOut
    items: list[CampaignRow]
    best: Best | None
    min_sample: int
    note: str


ABDimension = Literal["style", "attachment", "channel"]


class ABGroup(BaseModel):
    key: str
    label: str
    style: OfferStyle | None = None
    outcomes: Outcomes


class ABReport(BaseModel):
    period: PeriodOut
    dimension: ABDimension
    campaign_id: int | None
    groups: list[ABGroup]
    # insufficient_data: fewer than two groups with ``min_sample`` sent; no_difference: the gap between the
    # two best is within chance (p ≥ 0.05); leader: the best group's reply rate is higher beyond chance
    verdict: Literal["insufficient_data", "no_difference", "leader"]
    leader: str | None
    p_value: Decimal | None  # two-proportion z-test, best vs runner-up
    min_sample: int
    note: str


# ---------------------------------------------------------------------------
# AI vs human (ТЗ §46)
# ---------------------------------------------------------------------------
class GenerationFeedbackRow(BaseModel):
    key: str  # strategy / model key
    generated: int  # variants that produced an image
    approved: int  # approved or final
    final: int
    rejected: int
    undecided: int
    approval_rate: Decimal | None  # approved / (approved + rejected)


class OfferFeedbackRow(BaseModel):
    style: OfferStyle | None
    label: str
    approve: int
    reject: int
    skip: int
    approval_rate: Decimal | None  # approve / (approve + reject)
    avg_personalization_approved: Decimal | None
    avg_personalization_rejected: Decimal | None
    edits: int  # manual edits recorded
    edited_offers: int


class LeadScoreBand(BaseModel):
    key: str
    label: str
    low: Decimal
    high: Decimal
    selected: int  # manual decisions «shortlist» / «select»
    rejected: int
    selection_rate: Decimal | None  # selected / (selected + rejected)


class AIFeedbackReport(BaseModel):
    period: PeriodOut
    generation_by_strategy: list[GenerationFeedbackRow]
    generation_by_model: list[GenerationFeedbackRow]
    offers_by_style: list[OfferFeedbackRow]
    lead_decisions_by_score: list[LeadScoreBand]
    scores: FeedbackSummary  # human corrections of AI scores (all time)
