from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.core.state_machine import LeadStatus
from app.domains.analysis.schemas import BudgetCheckOut
from app.domains.generation.brief import GenerationBrief
from app.domains.generation.models import MAX_VARIANTS, GenerationStatus, VariantStatus
from app.domains.jobs.schemas import JobRunOut

MAX_REFERENCES = 3  # + the current thumbnail / parent variant = the provider's limit of 4 input images
Text500 = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
Instructions = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]


# ---------------------------------------------------------------------------
# References (ТЗ §43)
# ---------------------------------------------------------------------------
class ReferenceIn(BaseModel):
    """``image_base64`` — the file (JPEG / PNG / WebP, ≤ 10 MB) as base64 (optionally a ``data:`` URL)."""

    model_config = ConfigDict(extra="forbid")
    image_base64: str = Field(min_length=16, max_length=14_000_000)
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    description: Text500 = ""
    style: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] = ""
    category: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] = ""
    tags: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]] = Field(
        default_factory=list, max_length=20
    )


class ReferencePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)] | None = (
        None
    )
    description: Text500 | None = None
    style: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    category: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    tags: (
        list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]] | None
    ) = Field(default=None, max_length=20)


class ReferenceOut(BaseModel):
    id: int
    image_asset_id: int
    image_url: str
    width: int
    height: int
    title: str
    description: str
    style: str
    category: str
    tags: list[str]
    created_at: datetime


# ---------------------------------------------------------------------------
# Generation (ТЗ §12–13)
# ---------------------------------------------------------------------------
class GenerationIn(BaseModel):
    """What to generate for a lead. ``source_video_id`` — the video whose current thumbnail is redesigned
    (default: the weakest audited thumbnail of the channel); ``parent_variant_id`` — «create more like this»
    (the variant replaces the current thumbnail as image 1)."""

    model_config = ConfigDict(extra="forbid")
    variant_count: int = Field(default=4, ge=1, le=MAX_VARIANTS)
    instructions: Instructions = ""
    reference_ids: list[int] = Field(default_factory=list, max_length=MAX_REFERENCES)
    source_video_id: int | None = None
    use_source_image: bool = True  # send the current thumbnail to the model (edit) or describe only
    parent_variant_id: int | None = None
    model_key: str | None = Field(default=None, max_length=60)  # None = first model of the route
    size: Literal["1536x1024", "1024x1024", "1024x1536", "auto"] | None = "1536x1024"
    quality: Literal["low", "medium", "high", "auto"] | None = None


class GenerationStart(GenerationIn):
    confirm_token: str = Field(min_length=1, max_length=64)


class SourceCandidate(BaseModel):
    video_id: int
    title: str
    published_at: datetime | None
    view_count: int | None
    image_url: str | None
    overall_score: Decimal | None  # current audit, workspace weights
    problem_codes: list[str]


class ImageModelOption(BaseModel):
    key: str
    api_model_id: str
    price_per_image_usd: Decimal | None
    pricing_verified: bool


class GenerationEstimate(BaseModel):
    lead_id: int
    lead_status: LeadStatus
    provider: str | None
    model_key: str | None
    api_model_id: str | None
    models: list[ImageModelOption]  # enabled image models of the active provider (route order)
    variant_count: int
    price_per_image_usd: Decimal | None
    estimated_cost_usd: Decimal | None  # = the job's hard cost cap
    pricing_verified: bool
    source: SourceCandidate | None  # the thumbnail being redesigned
    source_candidates: list[SourceCandidate]
    brief: GenerationBrief
    prompt_preview: str  # the prompt of variant A exactly as it will be sent
    budgets: list[BudgetCheckOut]
    within_budgets: bool
    confirm_token: str


class VariantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    label: str
    strategy: str
    status: VariantStatus
    image_asset_id: int | None
    image_url: str | None
    prompt: str
    model_key: str
    api_model_id: str | None
    settings: dict[str, object]
    source_image_ids: list[int]
    parent_id: int | None
    cost_usd: Decimal | None
    error: str | None
    decided_by: int | None
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime


class GenerationOut(BaseModel):
    id: int
    lead_id: int
    status: GenerationStatus
    source_video_id: int | None
    source_image_url: str | None
    parent_variant_id: int | None
    instructions: str
    reference_ids: list[int]
    variant_count: int
    model_key: str
    size: str | None
    quality: str | None
    brief: GenerationBrief
    estimated_cost_usd: Decimal | None
    spent_usd: Decimal  # Σ cost of the variants
    error: str | None
    job: JobRunOut | None
    provider_is_mock: bool
    variants: list[VariantOut]
    created_at: datetime


class GenerationStartResult(BaseModel):
    generation: GenerationOut
    lead_status: LeadStatus


class ComparisonOriginal(BaseModel):
    """The original side of «Original vs Generated» (ТЗ §42)."""

    video_id: int
    title: str
    image_url: str | None
    overall_score: Decimal | None
    problems: list[dict[str, object]]
    recommendations: list[dict[str, object]]
    visual_summary: str | None


class LeadGenerations(BaseModel):
    lead_id: int
    lead_status: LeadStatus
    final_variant_id: int | None  # «final free sample» of the lead
    items: list[GenerationOut]  # newest first
    originals: list[ComparisonOriginal]  # current thumbnails the generations redesigned


class VariantDecision(BaseModel):
    """approve / reject / final («use as final free sample»: one per lead, the previous final becomes
    approved) / reset (back to ready)."""

    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject", "final", "reset"]
    comment: Text500 = ""
