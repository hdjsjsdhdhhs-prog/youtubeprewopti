"""Comparative AI audit of a channel's recent thumbnails (Phase 3.6, ТЗ §17, AI_ARCHITECTURE §3).

The model sees 3–12 recent thumbnails of one channel *together* (plus their individual audits and view
counts) and answers channel-level questions: one repeated template, outdated look, a design system, which
patterns go with more views (an observation, not causation), recurring problems, the weakest videos.

Videos are referred to by their position in the prompt (``video_index`` 1…N); the code maps positions back
to video ids and drops positions that do not exist. Aggregates (average / median quality, share of weak /
medium / strong thumbnails, best / worst) are **not** asked from the model: they are computed in SQL from
the current thumbnail audits with the workspace's weights (``channel_service.py``).
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.domains.analysis.audit import Level, ProblemCode, Score

MIN_THUMBNAILS = 3  # fewer audited thumbnails => nothing to compare
MAX_THUMBNAILS = 12  # AI_ARCHITECTURE §3: 6–12 recent thumbnails, low detail
DEFAULT_THUMBNAILS = 8

# Bands of the deterministic overall thumbnail score (1–10) used by the channel aggregates.
WEAK_BELOW = 5.0  # overall < 5 => weak
STRONG_FROM = 7.0  # overall ≥ 7 => strong; in between => medium

VideoIndex = Annotated[int, Field(ge=1, le=MAX_THUMBNAILS)]


class PerformingPattern(BaseModel):
    """A visual pattern that co-occurs with more views in this channel. Correlation only."""

    model_config = ConfigDict(extra="forbid")
    pattern: str = Field(max_length=300)
    evidence: str = Field(max_length=400)  # which videos / views it is based on


class RecurringProblem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: ProblemCode
    frequency: Level  # how many of the shown thumbnails have it: low = a few, high = most
    description: str = Field(max_length=400)


class RedesignCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    video_index: VideoIndex
    why: str = Field(max_length=400)


class ChannelVisualAudit(BaseModel):
    """Structured output of ``channel_analysis`` (prompt ``channel_analysis/v1``)."""

    model_config = ConfigDict(extra="forbid")

    visual_consistency: Score  # 1 = every thumbnail looks different, 10 = one recognisable style
    repeated_template: bool  # the same layout is reused (with only text / photo swapped)
    outdated_style: Score  # EXCEPTION: higher = MORE outdated (10 = clearly old-fashioned)
    has_design_system: bool  # consistent fonts, palette, branding elements across thumbnails
    better_performing_patterns: list[PerformingPattern] = Field(max_length=5)
    recurring_problems: list[RecurringProblem] = Field(max_length=8)
    weakest_video_indexes: list[VideoIndex] = Field(max_length=5)
    best_redesign_candidates: list[RedesignCandidate] = Field(max_length=5)
    visual_opportunity: Level
    summary: str = Field(max_length=800)


def map_indexes(indexes: list[int], video_ids: list[int]) -> list[int]:
    """1-based prompt positions -> video ids, in the model's order; unknown and repeated positions dropped."""
    out: list[int] = []
    for i in indexes:
        if 1 <= i <= len(video_ids) and video_ids[i - 1] not in out:
            out.append(video_ids[i - 1])
    return out
