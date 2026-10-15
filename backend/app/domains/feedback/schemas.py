from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.core.state_machine import LeadStatus
from app.domains.feedback.models import ScoreTarget
from app.domains.leads.results import RescoreResult

Reason = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]


class ScoreOverrideIn(BaseModel):
    """Set (or change) the human value of one score. ``ai_value`` is taken by the server from the current
    AI / code value — never from the client. Fields per target: ``thumbnail_analysis`` — ``overall_score``
    and the 11 subscores (1–10); ``channel_analysis`` — ``visual_consistency``, ``outdated_style_score``
    (1–10); ``lead`` — ``total``, ``priority_score`` (0–100)."""

    model_config = ConfigDict(extra="forbid")
    target_type: ScoreTarget
    target_id: int = Field(ge=1)
    field: str = Field(min_length=1, max_length=40)
    human_value: Decimal = Field(ge=0, le=100, decimal_places=2)
    reason: Reason = ""


class ScoreOverrideOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    target_type: ScoreTarget
    target_id: int
    field: str
    ai_value: Decimal  # the AI / code value when the human value was (last) set
    human_value: Decimal
    reason: str
    user_id: int | None
    created_at: datetime
    updated_at: datetime


class ScoreOverrideSaved(ScoreOverrideOut):
    rescored: RescoreResult | None  # None = the field does not feed the Lead Score


class OverrideRemoved(BaseModel):
    rescored: RescoreResult | None


class OverrideStats(BaseModel):
    """Current human values of one field vs the AI / code values they replaced (ТЗ §46)."""

    target_type: ScoreTarget
    field: str
    overrides: int
    avg_ai: Decimal
    avg_human: Decimal
    avg_delta: Decimal  # human − AI: < 0 => the AI overestimates
    ai_overestimates: int  # human < AI
    ai_underestimates: int  # human > AI


class DecisionStats(BaseModel):
    """Manual lead decisions and the Lead Score the code gave the lead at that moment."""

    status: LeadStatus
    decisions: int
    avg_lead_score: Decimal | None


class FeedbackSummary(BaseModel):
    overrides: list[OverrideStats]
    lead_decisions: list[DecisionStats]
    events: int  # all feedback events of the workspace
