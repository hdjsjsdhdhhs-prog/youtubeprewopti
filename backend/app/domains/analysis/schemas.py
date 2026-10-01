from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MetricRange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min: float | None = None
    max: float | None = None

    @model_validator(mode="after")
    def _ordered(self) -> MetricRange:
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("min must not be greater than max")
        return self


class MetricBounds(BaseModel):
    """Ranges over ``image_metrics`` (see ``app.domains.media.imaging`` for scales)."""

    model_config = ConfigDict(extra="forbid")
    luminance_mean: MetricRange | None = None  # 0..1
    contrast_rms: MetricRange | None = None  # 0..0.5
    colorfulness: MetricRange | None = None  # 0..~150
    sharpness_laplacian: MetricRange | None = None  # ≥ 0
    edge_density: MetricRange | None = None  # 0..1
    saliency_center_ratio: MetricRange | None = None  # 0..1 (0.25 = evenly spread)


class ThumbnailPrefilter(BaseModel):
    """Which thumbnails of a project go to the (paid) AI audit (AI_PIPELINE: prefilter step).

    Stored as ``search_projects.prefilter_settings``; ``{}`` = these defaults. Selection: channels of the
    project (optionally narrowed by the project's saved channel filters) → their newest
    ``videos_per_channel`` videos after the video conditions → thumbnails with current metrics inside
    the metric ranges."""

    model_config = ConfigDict(extra="forbid")
    apply_channel_filters: bool = True  # use the project's saved channel filters (filter_settings)
    videos_per_channel: int = Field(default=6, ge=1, le=50)
    exclude_shorts: bool = True
    max_video_age_days: int | None = Field(default=None, ge=1, le=3650)
    min_video_views: int | None = Field(default=None, ge=0)
    metrics: MetricBounds = Field(default_factory=MetricBounds)


class PrefilterPreview(BaseModel):
    channels_in_project: int
    channels_matched: int  # after the project's channel filters (if applied)
    videos_considered: int  # newest N per matched channel after the video conditions
    thumbnails_not_ready: int  # of those: not downloaded or no current metrics (run ingestion first)
    excluded_by_metrics: int
    thumbnails_selected: int  # ready and inside the metric ranges => would be analysed
    sample_video_ids: list[int]  # up to 24 selected videos (for a visual check)


class AnalysisEstimateRequest(BaseModel):
    """Estimate for an AI thumbnail audit of the project. ``prefilter`` overrides the saved settings
    (preview before saving); omitted = the project's ``prefilter_settings``."""

    model_config = ConfigDict(extra="forbid")
    prefilter: ThumbnailPrefilter | None = None
    detail: Literal["low", "high"] = "low"  # low for bulk (AI_ARCHITECTURE §2 cost control)


class BudgetCheckOut(BaseModel):
    budget_id: int
    description: str
    limit_usd: Decimal | None
    max_ai_operations: int | None
    spent_usd: Decimal
    operations: int
    would_exceed: bool
    reason: str | None


class AnalysisEstimate(BaseModel):
    task: str
    preview: PrefilterPreview
    provider: str | None  # None => AI not configured
    model_key: str | None
    api_model_id: str | None
    detail: str
    items: int
    cost_per_item_usd: Decimal | None  # None => model price unknown (registry not verified, Q-004)
    estimated_cost_usd: Decimal | None
    pricing_verified: bool
    estimated_input_tokens: int
    estimated_output_tokens: int
    budgets: list[BudgetCheckOut]  # empty => no limits set (only this confirmation applies)
    within_budgets: bool
    confirm_token: str  # echo it when starting the run; any change of selection/model/price => new token
