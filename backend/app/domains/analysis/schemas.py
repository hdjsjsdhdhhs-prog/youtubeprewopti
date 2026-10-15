from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.analysis.audit import DEFAULT_WEIGHTS, SUBSCORES, AuditProblem, AuditRecommendation, Level
from app.domains.analysis.channel_audit import (
    DEFAULT_THUMBNAILS,
    MAX_THUMBNAILS,
    MIN_THUMBNAILS,
    PerformingPattern,
    RecurringProblem,
)
from app.domains.analysis.models import ScoringKind
from app.domains.feedback.schemas import ScoreOverrideOut
from app.domains.jobs.schemas import JobRunOut


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
    items: int  # distinct images that need an AI call (selected minus already analysed)
    already_analyzed: int  # selected images with a stored audit for this prompt version and model (free)
    cost_per_item_usd: Decimal | None  # None => model price unknown (registry not verified, Q-004)
    estimated_cost_usd: Decimal | None
    pricing_verified: bool
    estimated_input_tokens: int
    estimated_output_tokens: int
    budgets: list[BudgetCheckOut]  # empty => no limits set (only this confirmation applies)
    within_budgets: bool
    confirm_token: str  # echo it when starting the run; any change of selection/model/price => new token


class AnalysisStartRequest(AnalysisEstimateRequest):
    """Start the AI thumbnail audit for exactly the estimate the user confirmed (§76)."""

    confirm_token: str = Field(min_length=1, max_length=64)


class AnalysisStartResult(BaseModel):
    job: JobRunOut | None  # None => nothing to analyse (everything selected is analysed already)
    created: bool  # False => the identical run is already active (returned instead of a duplicate)
    items: int
    already_analyzed: int
    estimated_cost_usd: Decimal | None


# ---------------------------------------------------------------------------
# Scoring profile (thumbnail weights)
# ---------------------------------------------------------------------------
Weight = Annotated[float, Field(ge=0, le=10)]


class ThumbnailWeights(BaseModel):
    """Weights of the subscores in ``overall_score`` (Σ wᵢ·sᵢ / Σ wᵢ). 0 = not counted."""

    model_config = ConfigDict(extra="forbid")
    composition_score: Weight = DEFAULT_WEIGHTS["composition_score"]
    contrast_score: Weight = DEFAULT_WEIGHTS["contrast_score"]
    text_score: Weight = DEFAULT_WEIGHTS["text_score"]
    hierarchy_score: Weight = DEFAULT_WEIGHTS["hierarchy_score"]
    subject_score: Weight = DEFAULT_WEIGHTS["subject_score"]
    color_score: Weight = DEFAULT_WEIGHTS["color_score"]
    mobile_readability: Weight = DEFAULT_WEIGHTS["mobile_readability"]
    topic_relevance: Weight = DEFAULT_WEIGHTS["topic_relevance"]
    visual_impact: Weight = DEFAULT_WEIGHTS["visual_impact"]
    professionalism: Weight = DEFAULT_WEIGHTS["professionalism"]
    redesign_potential: Weight = DEFAULT_WEIGHTS["redesign_potential"]

    @model_validator(mode="after")
    def _some_positive(self) -> ThumbnailWeights:
        if not any(getattr(self, k) > 0 for k in SUBSCORES):
            raise ValueError("at least one weight must be greater than 0")
        return self


class ScoringProfileOut(BaseModel):
    kind: ScoringKind
    version: int  # 0 = built-in defaults (nothing saved yet)
    is_default: bool
    weights: ThumbnailWeights


# ---------------------------------------------------------------------------
# Audit results
# ---------------------------------------------------------------------------
class ThumbnailAnalysisOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    image_asset_id: int
    provider: str  # "mock" => synthetic demo result
    model_key: str
    detail: str
    reused_from_id: int | None  # set => copied from a visually identical image (pHash), no AI call
    phash_distance: int | None
    created_at: datetime
    overall_score: Decimal  # deterministic: workspace weights over the subscores (not from the AI)
    composition_score: Decimal
    contrast_score: Decimal
    text_score: Decimal
    hierarchy_score: Decimal
    subject_score: Decimal
    color_score: Decimal
    mobile_readability: Decimal
    topic_relevance: Decimal
    visual_impact: Decimal
    professionalism: Decimal
    redesign_potential: Decimal
    has_face: bool
    face_emotion: str | None
    text_present: bool
    text_word_count_est: int
    focal_point: str
    background_separation: Level
    clutter_level: Level
    improvement_opportunity: Level
    problems: list[AuditProblem]
    recommendations: list[AuditRecommendation]
    visual_summary: str


class AnalyzedVideoOut(BaseModel):
    video_id: int
    youtube_video_id: str
    title: str
    published_at: datetime | None
    view_count: int | None
    channel_id: int
    channel_title: str
    analysis: ThumbnailAnalysisOut
    # human values of this audit (ТЗ §47), shown next to the AI ones; ``overall_score`` stays the AI value
    overrides: list[ScoreOverrideOut] = Field(default_factory=list)
    is_favorite: bool = False  # the video's thumbnail is a favorite of the current user (ТЗ §37)


class AnalysisSort(StrEnum):
    OVERALL = "overall"
    REDESIGN_POTENTIAL = "redesign_potential"
    PUBLISHED_AT = "published_at"
    VIEWS = "views"


class ProblemCount(BaseModel):
    code: str
    count: int


class AnalysisSummary(BaseModel):
    videos_analyzed: int  # videos of the project whose thumbnail has a current audit (all filters off)
    avg_overall: Decimal | None
    opportunity: dict[str, int]  # improvement_opportunity -> videos
    top_problems: list[ProblemCount]  # most frequent problem codes
    mock_results: int  # of videos_analyzed: audited by the mock provider


class ThumbnailAnalysisList(BaseModel):
    items: list[AnalyzedVideoOut]
    total: int  # after filters
    limit: int
    offset: int
    summary: AnalysisSummary
    scoring: ScoringProfileOut


# ---------------------------------------------------------------------------
# Channel audit (Phase 3.6, ТЗ §17)
# ---------------------------------------------------------------------------
class ChannelAnalysisRequest(BaseModel):
    """Estimate of a comparative AI audit of the project's channels. A channel is audited from its newest
    ``videos_per_channel`` non-Shorts videos that already have an AI thumbnail audit (3.5)."""

    model_config = ConfigDict(extra="forbid")
    videos_per_channel: int = Field(default=DEFAULT_THUMBNAILS, ge=MIN_THUMBNAILS, le=MAX_THUMBNAILS)
    detail: Literal["low", "high"] = "low"


class ChannelSelectionPreview(BaseModel):
    channels_in_project: int
    channels_ready: int  # ≥ MIN_THUMBNAILS audited thumbnails => can be audited
    channels_not_enough: int  # 1 … MIN_THUMBNAILS-1 audited thumbnails (audit more thumbnails first)
    channels_without_audits: int  # no audited thumbnail at all
    min_thumbnails: int


class ChannelAnalysisEstimate(BaseModel):
    task: str
    preview: ChannelSelectionPreview
    provider: str | None  # None => AI not configured
    model_key: str | None
    api_model_id: str | None
    detail: str
    videos_per_channel: int
    items: int  # channels that need an AI call (ready minus already audited with the same inputs)
    already_analyzed: int  # ready channels with a stored audit of exactly these inputs (free)
    images: int  # thumbnails sent over all ``items`` calls
    cost_per_item_usd: Decimal | None  # average per channel; None => model price unknown
    estimated_cost_usd: Decimal | None
    pricing_verified: bool
    estimated_input_tokens: int
    estimated_output_tokens: int
    budgets: list[BudgetCheckOut]
    within_budgets: bool
    confirm_token: str


class ChannelAnalysisStartRequest(ChannelAnalysisRequest):
    confirm_token: str = Field(min_length=1, max_length=64)


class RedesignCandidateOut(BaseModel):
    video_id: int
    why: str


class ChannelAnalysisOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    channel_id: int
    provider: str  # "mock" => synthetic demo result
    model_key: str
    detail: str
    created_at: datetime
    video_ids: list[int]  # the compared videos, newest first
    visual_consistency: Decimal
    repeated_template: bool
    outdated_style_score: Decimal  # higher = more outdated
    has_design_system: bool
    visual_opportunity: Level
    patterns: list[PerformingPattern]  # co-occurrence with views, not causation
    recurring_problems: list[RecurringProblem]
    weakest_video_ids: list[int]
    redesign_candidates: list[RedesignCandidateOut]
    summary: str


class ChannelAggregates(BaseModel):
    """Computed by code from the current thumbnail audits of the compared videos with the workspace's
    weights (not by the AI). Bands: weak < 5, medium 5–7, strong ≥ 7."""

    thumbnails_analyzed: int
    avg_score: Decimal | None
    median_score: Decimal | None
    pct_weak: Decimal
    pct_medium: Decimal
    pct_strong: Decimal
    best_video_id: int | None
    worst_video_id: int | None


class ChannelAuditVideo(BaseModel):
    video_id: int
    youtube_video_id: str
    title: str
    published_at: datetime | None
    view_count: int | None
    image_asset_id: int | None
    overall_score: Decimal | None  # current thumbnail audit, workspace weights
    improvement_opportunity: Level | None


class ChannelVisualReport(BaseModel):
    analysis: ChannelAnalysisOut | None  # None => the channel has not been audited yet
    aggregates: ChannelAggregates | None
    videos: list[ChannelAuditVideo]  # the compared videos in the order the model saw them
    scoring: ScoringProfileOut
    overrides: list[ScoreOverrideOut] = Field(default_factory=list)  # human values of ``analysis`` (§47)


class AnalyzedChannelOut(BaseModel):
    channel_id: int
    youtube_channel_id: str
    title: str
    subscriber_count: int | None
    analysis: ChannelAnalysisOut
    aggregates: ChannelAggregates


class ChannelAnalysisSort(StrEnum):
    AVG_SCORE = "avg_score"
    VISUAL_CONSISTENCY = "visual_consistency"
    OUTDATED_STYLE = "outdated_style"
    SUBSCRIBERS = "subscribers"
    ANALYZED_AT = "analyzed_at"


class ChannelAnalysisSummary(BaseModel):
    channels_analyzed: int  # channels of the project with a current channel audit (all filters off)
    opportunity: dict[str, int]  # visual_opportunity -> channels
    mock_results: int


class ChannelAnalysisList(BaseModel):
    items: list[AnalyzedChannelOut]
    total: int
    limit: int
    offset: int
    summary: ChannelAnalysisSummary
    scoring: ScoringProfileOut
