"""AI thumbnail audit: output schema, problem vocabulary and the deterministic overall score
(AI_ARCHITECTURE §2, ТЗ §6–7).

The model returns subscores and machine-readable problems; it is never asked for the overall score.
``overall_score`` = Σ wᵢ·sᵢ / Σ wᵢ over the workspace's active thumbnail scoring profile (``scoring.py``).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Score = Annotated[float, Field(ge=1, le=10)]
Level = Literal["low", "medium", "high"]

# Order = order of the subscore columns everywhere (DB, API, UI). Names follow ТЗ §7.
SUBSCORES: tuple[str, ...] = (
    "composition_score",
    "contrast_score",
    "text_score",
    "hierarchy_score",
    "subject_score",
    "color_score",
    "mobile_readability",
    "topic_relevance",
    "visual_impact",
    "professionalism",
    "redesign_potential",
)


class ProblemCode(StrEnum):
    """Fixed vocabulary: filterable in SQL and used by the generation brief (Phase 5).
    Adding a code is backwards compatible; renaming one needs a new prompt version."""

    WEAK_HIERARCHY = "weak_hierarchy"
    NO_FOCAL_POINT = "no_focal_point"
    LOW_SUBJECT_SEPARATION = "low_subject_separation"
    WEAK_SUBJECT = "weak_subject"
    SMALL_TEXT = "small_text"
    TOO_MUCH_TEXT = "too_much_text"
    UNREADABLE_TEXT = "unreadable_text"
    LOW_CONTRAST = "low_contrast"
    DULL_COLORS = "dull_colors"
    CLASHING_COLORS = "clashing_colors"
    CLUTTERED = "cluttered"
    POOR_COMPOSITION = "poor_composition"
    POOR_MOBILE_READABILITY = "poor_mobile_readability"
    WEAK_EMOTION = "weak_emotion"
    LOW_IMAGE_QUALITY = "low_image_quality"
    OFF_TOPIC = "off_topic"
    GENERIC_LOOK = "generic_look"
    OUTDATED_STYLE = "outdated_style"
    OTHER = "other"


class AuditProblem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: ProblemCode
    severity: Level
    description: str = Field(max_length=400)


class AuditRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: ProblemCode  # the problem this recommendation fixes
    action: str = Field(max_length=300)
    rationale: str = Field(max_length=400)


class ThumbnailAudit(BaseModel):
    """Structured output of ``thumbnail_analysis`` (prompt ``thumbnail_analysis/v1``). Subscores 1–10,
    higher = better, except ``redesign_potential`` (higher = more to gain from a redesign)."""

    model_config = ConfigDict(extra="forbid")

    composition_score: Score
    contrast_score: Score
    text_score: Score
    hierarchy_score: Score
    subject_score: Score
    color_score: Score
    mobile_readability: Score
    topic_relevance: Score
    visual_impact: Score
    professionalism: Score
    redesign_potential: Score

    has_face: bool
    face_emotion: str | None = Field(default=None, max_length=60)
    text_present: bool
    text_word_count_est: int = Field(ge=0, le=60)
    focal_point: str = Field(max_length=200)
    background_separation: Level
    clutter_level: Level
    problems: list[AuditProblem] = Field(max_length=8)
    recommendations: list[AuditRecommendation] = Field(max_length=8)
    visual_summary: str = Field(max_length=600)
    improvement_opportunity: Level


# ---------------------------------------------------------------------------
# Deterministic overall score
# ---------------------------------------------------------------------------
# Equal weights over the quality subscores. ``redesign_potential`` measures the *opportunity* (it is high
# for weak thumbnails), so it is not part of the quality score by default; lead scoring uses it (3.7).
DEFAULT_WEIGHTS: dict[str, float] = {name: 1.0 for name in SUBSCORES} | {"redesign_potential": 0.0}
_Q = Decimal("0.01")


def overall_score(scores: dict[str, float | Decimal], weights: dict[str, float]) -> Decimal:
    """Σ wᵢ·sᵢ / Σ wᵢ, rounded to 2 decimals. Raises ``ValueError`` if all weights are zero."""
    total = sum((Decimal(str(weights.get(k, 0.0))) for k in SUBSCORES), Decimal(0))
    if total <= 0:
        raise ValueError("at least one subscore weight must be positive")
    acc = sum((Decimal(str(weights.get(k, 0.0))) * Decimal(str(scores[k])) for k in SUBSCORES), Decimal(0))
    return (acc / total).quantize(_Q, rounding=ROUND_HALF_UP)
