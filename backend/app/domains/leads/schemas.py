from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.core.state_machine import LeadStatus
from app.domains.analysis.audit import Level
from app.domains.analysis.models import ScoringKind
from app.domains.leads.scoring import (
    COMPONENTS,
    DEFAULT_LEAD_WEIGHTS,
    DEFAULT_PRIORITY_WEIGHTS,
    ActivityLevel,
    IdealLeadCriteria,
)
from app.domains.youtube.schemas import ChannelOut

ComponentWeight = Annotated[float, Field(ge=0, le=100)]
NicheValue = Annotated[float, Field(ge=0, le=10)]


class _ComponentWeights(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _some_positive(self) -> _ComponentWeights:
        if not any(getattr(self, k) > 0 for k in COMPONENTS):
            raise ValueError("at least one weight must be greater than 0")
        return self

    def component_weights(self) -> dict[str, float]:
        return {k: float(getattr(self, k)) for k in COMPONENTS}


class LeadWeights(_ComponentWeights):
    """Lead Score: maximum points per component (the score is normalised to 0–100 whatever their sum) and
    the niche value table (taxonomy node id → 0–10; a channel takes the best value of its niches, a
    topic without its own value inherits its niche's)."""

    thumbnail_opportunity: ComponentWeight = DEFAULT_LEAD_WEIGHTS["thumbnail_opportunity"]
    channel_fit: ComponentWeight = DEFAULT_LEAD_WEIGHTS["channel_fit"]
    audience: ComponentWeight = DEFAULT_LEAD_WEIGHTS["audience"]
    views: ComponentWeight = DEFAULT_LEAD_WEIGHTS["views"]
    activity: ComponentWeight = DEFAULT_LEAD_WEIGHTS["activity"]
    growth: ComponentWeight = DEFAULT_LEAD_WEIGHTS["growth"]
    contactability: ComponentWeight = DEFAULT_LEAD_WEIGHTS["contactability"]
    niche_value: ComponentWeight = DEFAULT_LEAD_WEIGHTS["niche_value"]
    niche_values: dict[int, NicheValue] = Field(default_factory=dict, max_length=500)


class PriorityWeights(_ComponentWeights):
    """Priority Score (§53): relative weights over the same components."""

    thumbnail_opportunity: ComponentWeight = DEFAULT_PRIORITY_WEIGHTS["thumbnail_opportunity"]
    channel_fit: ComponentWeight = DEFAULT_PRIORITY_WEIGHTS["channel_fit"]
    audience: ComponentWeight = DEFAULT_PRIORITY_WEIGHTS["audience"]
    views: ComponentWeight = DEFAULT_PRIORITY_WEIGHTS["views"]
    activity: ComponentWeight = DEFAULT_PRIORITY_WEIGHTS["activity"]
    growth: ComponentWeight = DEFAULT_PRIORITY_WEIGHTS["growth"]
    contactability: ComponentWeight = DEFAULT_PRIORITY_WEIGHTS["contactability"]
    niche_value: ComponentWeight = DEFAULT_PRIORITY_WEIGHTS["niche_value"]


class LeadProfileOut(BaseModel):
    kind: ScoringKind
    version: int  # 0 = built-in defaults
    is_default: bool
    weights: LeadWeights


class PriorityProfileOut(BaseModel):
    kind: ScoringKind
    version: int
    is_default: bool
    weights: PriorityWeights


class RescoreResult(BaseModel):
    leads_created: int  # channels of the workspace's projects that had no lead yet
    leads_analyzed: int  # DISCOVERED → ANALYZED (thumbnails audited)
    scores_changed: int  # leads whose score changed (a new score row)
    scores_unchanged: int


class LeadProfileSaved(LeadProfileOut):
    rescored: RescoreResult


class PriorityProfileSaved(PriorityProfileOut):
    rescored: RescoreResult


# ---------------------------------------------------------------------------
# Ideal Lead Profile
# ---------------------------------------------------------------------------
ProfileName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class IdealLeadProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: ProfileName
    criteria: IdealLeadCriteria = Field(default_factory=IdealLeadCriteria)
    is_default: bool = True


class IdealLeadProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: ProfileName | None = None
    criteria: IdealLeadCriteria | None = None
    is_default: bool | None = None  # true = make it the profile Channel Fit uses; false = use none


class IdealLeadProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    criteria: IdealLeadCriteria
    is_default: bool
    created_at: datetime
    updated_at: datetime


class IdealLeadProfileSaved(IdealLeadProfileOut):
    rescored: RescoreResult


# ---------------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------------
class ScoreComponentOut(BaseModel):
    key: str
    label: str
    points: Decimal  # Lead Score points …
    max: Decimal  # … out of (the component's lead weight)
    fraction: float | None  # 0–1; None = no data
    has_data: bool
    reason: str
    inputs: dict[str, Any]
    priority_weight: Decimal


class LeadScoreOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    total: Decimal  # Lead Score 0–100
    priority_score: Decimal
    data_coverage: Decimal  # % of the maximum backed by data
    avg_thumbnail_score: Decimal | None
    thumbnail_opportunity: Decimal
    channel_fit: Decimal
    audience: Decimal
    views: Decimal
    activity: Decimal
    growth: Decimal
    contactability: Decimal
    niche_value: Decimal
    lead_profile_version: int
    priority_profile_version: int
    thumbnail_profile_version: int
    ideal_lead_profile_id: int | None
    components: list[ScoreComponentOut]
    created_at: datetime  # computed at


class LeadOut(BaseModel):
    id: int
    status: LeadStatus
    status_changed_at: datetime
    created_at: datetime
    created_from_project_id: int | None
    channel: ChannelOut
    activity_level: ActivityLevel | None
    visual_opportunity: Level | None  # current comparative channel audit
    score: LeadScoreOut | None  # None = not scored yet (run «Пересчитать»)


class StatusChangeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    from_status: LeadStatus | None
    to_status: LeadStatus
    actor_user_id: int | None  # None = automatic
    reason: str
    created_at: datetime


class LeadDetail(LeadOut):
    history: list[StatusChangeOut]  # newest first
    score_history: list[LeadScoreHistoryItem]  # newest first (current included), at most 20
    ideal_lead_profile: IdealLeadProfileOut | None  # the one the current score used


class LeadScoreHistoryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    total: Decimal
    priority_score: Decimal
    data_coverage: Decimal
    created_at: datetime


class LeadSort(StrEnum):
    LEAD_SCORE = "lead_score"
    PRIORITY = "priority"
    THUMBNAIL_SCORE = "thumbnail_score"
    THUMBNAIL_OPPORTUNITY = "thumbnail_opportunity"
    VISUAL_OPPORTUNITY = "visual_opportunity"
    VIEWS = "views"
    SUBSCRIBERS = "subscribers"
    ACTIVITY = "activity"
    CONTACTABILITY = "contactability"
    LAST_VIDEO = "last_video"
    CREATED = "created"


class LeadFilters(BaseModel):
    """ТЗ §9: all filters AND-ed. Score filters exclude leads without a score / without audited
    thumbnails; channel filters exclude channels without metrics (as on the channels page)."""

    model_config = ConfigDict(extra="forbid")
    project_id: int | None = None
    channel_id: int | None = None
    status: list[LeadStatus] = Field(default_factory=list)
    q: str | None = None
    min_thumbnail_score: Decimal | None = Field(default=None, ge=1, le=10)
    max_thumbnail_score: Decimal | None = Field(default=None, ge=1, le=10)
    min_lead_score: Decimal | None = Field(default=None, ge=0, le=100)
    max_lead_score: Decimal | None = Field(default=None, ge=0, le=100)
    min_priority: Decimal | None = Field(default=None, ge=0, le=100)
    min_subscribers: int | None = Field(default=None, ge=0)
    max_subscribers: int | None = Field(default=None, ge=0)
    min_avg_views: int | None = Field(default=None, ge=0)
    activity: ActivityLevel | None = None  # at least this level
    max_days_since_last_upload: int | None = Field(default=None, ge=0)
    visual_opportunity: Level | None = None
    niche: list[int] = Field(default_factory=list)


class LeadSummary(BaseModel):
    leads: int  # all leads of the workspace (or the project), filters off
    scored: int
    by_status: dict[str, int]
    avg_lead_score: Decimal | None


class LeadList(BaseModel):
    items: list[LeadOut]
    total: int  # after filters
    limit: int
    offset: int
    summary: LeadSummary
    lead_profile: LeadProfileOut
    priority_profile: PriorityProfileOut


class LeadSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: int | None = None  # None = all projects of the workspace


LeadDetail.model_rebuild()
