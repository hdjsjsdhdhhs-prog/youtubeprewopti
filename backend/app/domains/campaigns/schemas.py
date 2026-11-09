from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.core.state_machine import LeadStatus
from app.domains.analysis.schemas import BudgetCheckOut
from app.domains.campaigns.models import (
    DEFAULT_CAMPAIGN_DAILY_LIMIT,
    DEFAULT_MAX_FAILURES,
    DEFAULT_SEND_DAYS,
    DEFAULT_SEND_FROM,
    DEFAULT_SEND_TO,
    DEFAULT_TIMEZONE,
    MAX_CAMPAIGN_DAILY_LIMIT,
    MAX_FAILURES_LIMIT,
    CampaignLeadStatus,
    CampaignStatus,
)
from app.domains.leads.schemas import LeadSelection
from app.domains.offers.models import OfferStatus, OfferStyle, OutreachChannel
from app.domains.outreach.models import MessageStatus

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
Instructions = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
Language = Annotated[str, StringConstraints(pattern=r"^[a-z]{2}(-[A-Za-z]{2,4})?$")]
Weekday = Annotated[int, Field(ge=1, le=7)]

# why a campaign cannot be started yet (CampaignOut.issues)
Issue = Literal[
    "no_leads",  # no active lead
    "email_account_required",  # email is allowed but there is no active account to send from
]


def _check_zone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown time zone '{value}'") from exc
    return value


def _unique[T](values: list[T]) -> list[T]:
    if len(set(values)) != len(values):
        raise ValueError("values must be unique")
    return values


class _Settings(BaseModel):
    """Shared validation of the create / patch bodies."""

    @field_validator("timezone", check_fields=False)
    @classmethod
    def _zone(cls, v: str | None) -> str | None:
        return _check_zone(v) if v is not None else v

    @field_validator("styles", "channels", "send_days", check_fields=False)
    @classmethod
    def _distinct(cls, v: list[Any] | None) -> list[Any] | None:
        return _unique(v) if v is not None else v


class CampaignIn(_Settings):
    """ТЗ §29. ``styles`` — the A/B(/C) offer styles compared (each lead gets one, balanced);
    ``channels`` — allowed channels in order of preference (manual = the operator sends it himself);
    ``email_account_ids`` — senders (empty = any active account); window in ``timezone``."""

    model_config = ConfigDict(extra="forbid")
    name: Name
    description: Description = ""
    project_id: int | None = None
    template_id: int | None = None
    instructions: Instructions = ""
    language: Language | None = None
    styles: list[OfferStyle] = Field(
        default_factory=lambda: [OfferStyle.PROFESSIONAL], min_length=1, max_length=3
    )
    channels: list[OutreachChannel] = Field(
        default_factory=lambda: [OutreachChannel.EMAIL], min_length=1, max_length=4
    )
    email_account_ids: list[int] = Field(default_factory=list, max_length=20)
    send_days: list[Weekday] = Field(
        default_factory=lambda: list(DEFAULT_SEND_DAYS), min_length=1, max_length=7
    )
    send_from: time = DEFAULT_SEND_FROM
    send_to: time = DEFAULT_SEND_TO
    timezone: str = DEFAULT_TIMEZONE
    starts_at: AwareDatetime | None = None
    daily_limit: int = Field(default=DEFAULT_CAMPAIGN_DAILY_LIMIT, ge=1, le=MAX_CAMPAIGN_DAILY_LIMIT)
    max_consecutive_failures: int = Field(default=DEFAULT_MAX_FAILURES, ge=1, le=MAX_FAILURES_LIMIT)


class CampaignPatch(_Settings):
    """Only the fields sent change; ``email_account_ids`` replaces the list; ``starts_at`` = null clears."""

    model_config = ConfigDict(extra="forbid")
    name: Name | None = None
    description: Description | None = None
    project_id: int | None = None
    template_id: int | None = None
    instructions: Instructions | None = None
    language: Language | None = None
    styles: list[OfferStyle] | None = Field(default=None, min_length=1, max_length=3)
    channels: list[OutreachChannel] | None = Field(default=None, min_length=1, max_length=4)
    email_account_ids: list[int] | None = Field(default=None, max_length=20)
    send_days: list[Weekday] | None = Field(default=None, min_length=1, max_length=7)
    send_from: time | None = None
    send_to: time | None = None
    timezone: str | None = None
    starts_at: AwareDatetime | None = None
    daily_limit: int | None = Field(default=None, ge=1, le=MAX_CAMPAIGN_DAILY_LIMIT)
    max_consecutive_failures: int | None = Field(default=None, ge=1, le=MAX_FAILURES_LIMIT)


class CampaignAccountRef(BaseModel):
    id: int
    name: str
    from_email: str
    status: str


class CampaignCounts(BaseModel):
    """Leads of the campaign by outcome and the stage of the active ones."""

    leads: int  # all but removed
    active: int
    sent: int
    failed: int
    rejected: int
    skipped: int
    removed: int
    stages: dict[str, int]  # CampaignStage → count (active leads)
    replied: int  # sent leads with a reply (any kind)
    opted_out: int


class CampaignOut(BaseModel):
    id: int
    name: str
    description: str
    status: CampaignStatus
    project_id: int | None
    project_name: str | None
    template_id: int | None
    template_name: str | None
    instructions: str
    language: str | None
    styles: list[OfferStyle]
    channels: list[OutreachChannel]
    email_accounts: list[CampaignAccountRef]
    send_days: list[int]
    send_from: time
    send_to: time
    timezone: str
    starts_at: datetime | None
    daily_limit: int
    sent_today: int
    max_consecutive_failures: int
    consecutive_failures: int
    paused_reason: str | None
    in_window_now: bool
    next_window_at: datetime
    issues: list[Issue]  # empty = can be started
    target_filters: dict[str, Any]
    counts: CampaignCounts
    started_at: datetime | None
    completed_at: datetime | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CampaignList(BaseModel):
    items: list[CampaignOut]
    counts: dict[str, int]  # campaigns per status


# ---------------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------------
# where an active lead is (derived from its offer / message), or its outcome
CampaignStage = Literal[
    "no_offer",  # nothing written yet
    "drafting",  # drafts are being generated
    "generation_failed",  # no draft came out: generate again
    "review",  # waiting in the Approval Queue
    "approved",  # approved, waiting to be queued (campaign not running / outside the run)
    "queued",  # the message waits for the window / limits / the account
    "manual",  # waits for the operator to send it himself (Telegram, VK, manual)
    "send_cancelled",  # approved, but its message was cancelled: send it by hand or edit
    "sent",
    "replied",
    "failed",
    "rejected",
    "skipped",
    "removed",
]


class CampaignLeadOut(BaseModel):
    lead_id: int
    channel_id: int
    channel_title: str
    lead_status: LeadStatus
    style: OfferStyle
    label: str
    status: CampaignLeadStatus
    stage: CampaignStage
    reason: str | None
    offer_id: int | None
    offer_status: OfferStatus | None
    message_id: int | None
    message_status: MessageStatus | None
    sent_at: datetime | None
    added_at: datetime


class CampaignLeadList(BaseModel):
    items: list[CampaignLeadOut]
    total: int
    limit: int
    offset: int


class CampaignLeadsIn(BaseModel):
    """«Add to campaign» (ТЗ §35): chosen leads or «all filtered» — like the leads bulk actions."""

    model_config = ConfigDict(extra="forbid")
    selection: LeadSelection


SkipReason = Literal[
    "already_in_campaign",
    "other_campaign",  # active in another campaign
    "lead_status",  # closed, or already contacted (sent / replied / declined)
]


class CampaignLeadsResult(BaseModel):
    matched: int
    added: int
    skipped: dict[str, int]  # SkipReason → count
    styles: dict[str, int]  # A/B assignment of the added leads


# ---------------------------------------------------------------------------
# Offer generation (estimate → confirm → start)
# ---------------------------------------------------------------------------
GenerationSkip = Literal[
    "no_contact",  # no usable contact for the campaign's channels
    "lead_status",  # the lead's status does not allow writing an offer
    "generation_running",  # a thumbnail generation of the lead is running
    "offer_generating",  # its drafts are being written
]


class CampaignGenerationEstimate(BaseModel):
    campaign_id: int
    lead_ids: list[int]  # leads that get an offer now (one draft each, in their A/B style)
    styles: dict[str, int]  # style → leads
    skipped: dict[str, int]  # GenerationSkip → leads
    remaining: int  # more leads to write for in a next run (batch limit)
    provider: str | None
    model_key: str | None
    api_model_id: str | None
    cost_per_offer_usd: Decimal | None
    estimated_cost_usd: Decimal | None  # = the sum of the jobs' hard caps
    pricing_verified: bool
    budgets: list[BudgetCheckOut]
    within_budgets: bool
    confirm_token: str


class CampaignGenerationStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_token: str = Field(min_length=1, max_length=64)


class CampaignGenerationResult(BaseModel):
    campaign: CampaignOut
    started: int  # offers queued for generation
    job_ids: list[int]


# ---------------------------------------------------------------------------
# A/B results (ТЗ §28): counts only, no causal conclusions
# ---------------------------------------------------------------------------
class VariantStats(BaseModel):
    style: OfferStyle
    label: str
    leads: int  # assigned (not removed)
    approved: int  # offers approved (incl. queued / sent)
    sent: int
    delivered: int
    opened: int
    replied: int  # any reply (replied / positive / negative)
    positive_reply: int
    negative_reply: int
    no_reply: int  # sent, nothing recorded yet
    bounced: int
    opt_out: int
    reply_rate: Decimal | None  # replied / sent
    small_sample: bool  # fewer than ``min_sample`` sent: differences mean nothing yet


class CampaignStats(BaseModel):
    campaign_id: int
    variants: list[VariantStats]
    min_sample: int
    note: str  # always shown: what these numbers can and cannot tell
