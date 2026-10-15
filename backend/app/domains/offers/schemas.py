from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.core.state_machine import LeadStatus
from app.domains.analysis.schemas import BudgetCheckOut
from app.domains.approvals.models import ApprovalDecision
from app.domains.contacts.schemas import ContactOut
from app.domains.jobs.schemas import JobRunOut
from app.domains.offers.drafting import (
    GuardIssue,
    OfferClaim,
    OfferInputs,
    OfferStructure,
    OfferTemplateRef,
    PersonalizationSource,
)
from app.domains.offers.models import (
    MAX_OFFER_VARIANTS,
    OfferStatus,
    OfferStyle,
    OfferVariantStatus,
    OutreachChannel,
)

Text500 = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
Instructions = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

# blockers of «Approve» (OfferOut.blockers)
Blocker = Literal[
    "status",  # the offer is not waiting for approval
    "no_variant",  # no ready draft is selected
    "guard_issues",  # the selected text has guard issues (invented numbers, metrics …)
    "contact_required",
    "contact_unusable",  # opted out / invalid / suppressed / no longer published
    "channel_required",
    "channel_mismatch",  # e.g. email channel with a Telegram contact
    "lead_closed",  # the lead is rejected or opted out
]


# ---------------------------------------------------------------------------
# Generation (estimate → confirm → start)
# ---------------------------------------------------------------------------
class OfferIn(BaseModel):
    """What to write. ``attachment_variant_id`` — the free sample (an approved / final generated variant of
    the lead); default = the lead's final sample; ``attach_sample=false`` = no attachment."""

    model_config = ConfigDict(extra="forbid")
    variant_count: int = Field(default=MAX_OFFER_VARIANTS, ge=1, le=MAX_OFFER_VARIANTS)
    instructions: Instructions = ""
    template_id: int | None = None
    language: Annotated[str, StringConstraints(pattern=r"^[a-z]{2}(-[A-Za-z]{2,4})?$")] | None = None
    attach_sample: bool = True
    attachment_variant_id: int | None = None


class OfferStart(OfferIn):
    confirm_token: str = Field(min_length=1, max_length=64)


class SampleOut(BaseModel):
    """A generated thumbnail that can be attached as the free sample."""

    variant_id: int
    generation_request_id: int
    label: str
    strategy: str
    status: str  # approved | final
    image_url: str | None
    video_title: str | None


class OfferEstimate(BaseModel):
    lead_id: int
    lead_status: LeadStatus
    offer_id: int | None  # the lead's offer that gets a new round («Regenerate»); None = a new offer
    round: int
    provider: str | None
    model_key: str | None
    api_model_id: str | None
    variant_count: int
    styles: list[OfferStyle]
    cost_per_variant_usd: Decimal | None
    estimated_cost_usd: Decimal | None  # = the job's hard cost cap
    pricing_verified: bool
    inputs: OfferInputs  # the facts, the sample, the template and the instructions
    prompt_preview: str  # the prompt of variant A exactly as it will be sent
    samples: list[SampleOut]
    budgets: list[BudgetCheckOut]
    within_budgets: bool
    confirm_token: str


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
class OfferVariantOut(BaseModel):
    id: int
    round: int
    label: str
    style: OfferStyle
    status: OfferVariantStatus
    is_selected: bool
    subject: str
    body: str
    cta: str
    structure: OfferStructure | None
    claims: list[OfferClaim]
    guard_issues: list[GuardIssue]
    personalization_score: Decimal | None
    personalization_sources: list[PersonalizationSource]
    model_key: str | None
    cost_usd: Decimal | None
    error: str | None
    edited: bool
    edited_by: int | None
    edited_at: datetime | None
    created_at: datetime


class ApprovalOut(BaseModel):
    decision: ApprovalDecision
    content_hash: str
    is_current: bool  # made on exactly the current content
    comment: str
    user_id: int | None
    created_at: datetime


class OfferOut(BaseModel):
    id: int
    lead_id: int
    channel_id: int
    status: OfferStatus
    round: int
    variant_count: int
    language: str
    instructions: str
    template: OfferTemplateRef | None
    inputs: OfferInputs
    contact: ContactOut | None
    channel: OutreachChannel | None
    attachment: SampleOut | None
    manually_edited: bool
    scheduled_at: datetime | None
    skipped_at: datetime | None
    model_key: str | None
    estimated_cost_usd: Decimal | None
    spent_usd: Decimal
    error: str | None
    job: JobRunOut | None
    provider_is_mock: bool
    selected_variant_id: int | None
    personalization_score: Decimal | None  # of the selected variant
    content_hash: str  # what «Approve» must confirm
    blockers: list[Blocker]
    can_approve: bool
    approval: ApprovalOut | None  # the latest decision
    variants: list[OfferVariantOut]  # newest round first, then by label
    created_at: datetime
    updated_at: datetime


class OfferStartResult(BaseModel):
    offer: OfferOut
    lead_status: LeadStatus


class LeadOffers(BaseModel):
    lead_id: int
    lead_status: LeadStatus
    items: list[OfferOut]  # newest first
    contacts: list[ContactOut]  # the channel's contacts (usable first)
    samples: list[SampleOut]


# ---------------------------------------------------------------------------
# Edit / decisions / schedule
# ---------------------------------------------------------------------------
class OfferEdit(BaseModel):
    """ТЗ §23. Only the fields sent are changed; ``contact_id`` / ``attachment_variant_id`` = null clears.
    Text fields edit the selected variant (after ``variant_id`` if both are sent) and mark the offer
    ``manually_edited``. Any change of the content of an approved / scheduled offer sends it back to the
    queue: the approval no longer matches."""

    model_config = ConfigDict(extra="forbid")
    variant_id: int | None = None
    subject: Annotated[str, StringConstraints(strip_whitespace=True, max_length=150)] | None = None
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)] | None = (
        None
    )
    cta: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None
    contact_id: int | None = None
    channel: OutreachChannel | None = None
    attachment_variant_id: int | None = None


class OfferDecisionIn(BaseModel):
    """``approve`` must echo ``content_hash`` of the offer the human looked at (changed meanwhile => 409)."""

    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject", "skip"]
    comment: Text500 = ""
    content_hash: Sha256 | None = None

    @model_validator(mode="after")
    def _hash_for_approve(self) -> OfferDecisionIn:
        if self.decision == "approve" and self.content_hash is None:
            raise ValueError("content_hash is required to approve")
        return self


class OfferScheduleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scheduled_at: AwareDatetime


# ---------------------------------------------------------------------------
# Approval Queue (ТЗ §22)
# ---------------------------------------------------------------------------
class QueueChannel(BaseModel):
    id: int
    title: str
    handle: str | None
    url: str
    avatar_url: str | None
    subscriber_count: int | None


class QueueLead(BaseModel):
    id: int
    status: LeadStatus
    lead_score: Decimal | None
    priority_score: Decimal | None
    contactability_score: Decimal | None


class QueueThumbnail(BaseModel):
    video_id: int
    title: str
    image_url: str | None
    overall_score: Decimal | None
    problem_codes: list[str]


class QueueAnalysis(BaseModel):
    summary: str | None
    visual_opportunity: str | None
    problem_codes: list[str]


class QueueCard(BaseModel):
    offer: OfferOut
    channel: QueueChannel
    lead: QueueLead
    thumbnail: QueueThumbnail | None  # the current thumbnail the sample redesigns (else the weakest one)
    analysis: QueueAnalysis | None
    contacts: list[ContactOut]
    samples: list[SampleOut]  # generated thumbnails that can be attached instead


QueueStatus = Literal["pending_approval", "approved", "scheduled", "draft", "rejected", "failed"]


class ApprovalQueue(BaseModel):
    items: list[QueueCard]
    total: int
    limit: int
    offset: int
    counts: dict[str, int]  # offers per status in the workspace


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
class OfferTemplateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class OfferTemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int  # this version
    key: str
    version: int
    name: str
    body: str
    created_at: datetime
