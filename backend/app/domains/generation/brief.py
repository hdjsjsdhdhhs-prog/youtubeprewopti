"""Generation brief (ТЗ §12, §14; AI_ARCHITECTURE §7): deterministic, no AI.

Generation must fix what the analysis found, not draw at random. The brief turns the machine-readable
problem codes of the current thumbnail audit (and the recurring problems of the channel audit) into concrete
design directives, adds the channel context, the palette, the owner's instructions, the selected references
and what the owner approved / rejected before. Every variant gets the same brief plus its own composition
strategy, so the variants differ in a controlled way (Variant A, B, C …).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, Field

from app.domains.analysis.audit import ProblemCode

LABELS = "ABCDEF"
MAX_FIXES = 6
MAX_RECOMMENDATIONS = 3
_RANK = {"high": 0, "medium": 1, "low": 2}

# problem code -> what the new thumbnail must do instead (English: image models follow it best)
PROBLEM_FIXES: dict[ProblemCode, str] = {
    ProblemCode.WEAK_HIERARCHY: (
        "one clear visual hierarchy: a single dominant subject first, then at most one short headline"
    ),
    ProblemCode.NO_FOCAL_POINT: "one obvious focal point the eye lands on first",
    ProblemCode.LOW_SUBJECT_SEPARATION: (
        "the subject clearly separated from the background (rim light, depth of field, contrasting "
        "backdrop)"
    ),
    ProblemCode.WEAK_SUBJECT: "a large, expressive main subject filling 40-60% of the frame",
    ProblemCode.SMALL_TEXT: (
        "if there is text: at most 3 words in very large bold letters, readable on a phone"
    ),
    ProblemCode.TOO_MUCH_TEXT: "minimal text: at most 3 words, or none",
    ProblemCode.UNREADABLE_TEXT: "thick, simple, high-contrast lettering with an outline or shadow",
    ProblemCode.LOW_CONTRAST: "strong tonal contrast between subject, text and background",
    ProblemCode.DULL_COLORS: "vivid, saturated accent colours",
    ProblemCode.CLASHING_COLORS: "a harmonious palette of 2-3 colours with one accent colour",
    ProblemCode.CLUTTERED: "a clean composition: remove secondary objects, keep generous empty space",
    ProblemCode.POOR_COMPOSITION: "a balanced composition (rule of thirds, clear foreground and background)",
    ProblemCode.POOR_MOBILE_READABILITY: "must read at 120 px wide: few large shapes, nothing small or fine",
    ProblemCode.WEAK_EMOTION: "a clear, strong facial expression if a person is shown",
    ProblemCode.LOW_IMAGE_QUALITY: "crisp, sharp, professional lighting and a high-end finish",
    ProblemCode.OFF_TOPIC: "imagery that obviously matches the video topic",
    ProblemCode.GENERIC_LOOK: "distinctive and specific to this video, not stock-like",
    ProblemCode.OUTDATED_STYLE: "a modern, current YouTube thumbnail style",
}

# Composition strategy per variant: the variants explore different directions on purpose.
STRATEGIES: tuple[tuple[str, str], ...] = (
    (
        "subject_closeup",
        "close-up of the main subject with a strong emotion, a bold short headline on one side",
    ),
    (
        "object_contrast",
        "the key object or result of the video in the centre, high contrast, little or no text",
    ),
    ("minimal", "minimalist: a single subject on a clean bold-coloured background, at most one word"),
    ("dynamic_depth", "dynamic diagonal composition with clear foreground, subject and background layers"),
    ("cinematic", "dramatic cinematic lighting and mood, premium look"),
    ("high_energy", "bright, playful, high-energy colours and expressive shapes"),
)


class BriefFix(BaseModel):
    code: str
    severity: str  # high | medium | low (channel audit: frequency)
    source: Literal["thumbnail", "channel"]
    problem: str  # as the analysis described it
    directive: str  # what the new thumbnail does instead


class BriefReference(BaseModel):
    id: int
    title: str
    style: str
    description: str


class BriefHistory(BaseModel):
    previous_requests: int = 0
    approved: list[str] = Field(default_factory=list)  # strategies of approved / final variants of this lead
    rejected: list[str] = Field(default_factory=list)


class GenerationBrief(BaseModel):
    """Stored on the request (``generation_requests.brief``): exactly the data the prompts were built from."""

    channel_title: str
    niches: list[str]
    video_title: str | None
    base_image: Literal["current_thumbnail", "parent_variant"] | None  # image 1 sent to the model
    fixes: list[BriefFix]
    recommendations: list[str]  # actions from the thumbnail audit (analyst's words)
    channel_summary: str | None
    palette: list[str]  # dominant colours of the current thumbnail ("#rrggbb")
    instructions: str
    references: list[BriefReference]
    history: BriefHistory


def build_fixes(
    thumbnail_problems: Sequence[dict[str, str]], channel_problems: Sequence[dict[str, str]]
) -> list[BriefFix]:
    """Problems of the current thumbnail first (by severity), then recurring channel problems; one fix per
    code, at most ``MAX_FIXES``; ``other`` has no generic directive and is skipped."""
    seen: set[str] = set()
    fixes: list[BriefFix] = []
    groups: tuple[tuple[Literal["thumbnail", "channel"], Sequence[dict[str, str]], str], ...] = (
        ("thumbnail", thumbnail_problems, "severity"),
        ("channel", channel_problems, "frequency"),
    )
    for source, problems, level_key in groups:
        for p in sorted(problems, key=lambda p: _RANK.get(p.get(level_key, "low"), 3)):
            code = p.get("code", "")
            if code in seen or code not in PROBLEM_FIXES:
                continue
            seen.add(code)
            fixes.append(
                BriefFix(
                    code=code,
                    severity=p.get(level_key, "low"),
                    source=source,
                    problem=p.get("description", "")[:300],
                    directive=PROBLEM_FIXES[ProblemCode(code)],
                )
            )
    return fixes[:MAX_FIXES]


def strategy_for(index: int) -> tuple[str, str, str]:
    """(label, strategy key, strategy text) of the index-th variant."""
    key, text = STRATEGIES[index % len(STRATEGIES)]
    return LABELS[index], key, text


def _bullets(lines: Sequence[str], empty: str) -> str:
    return "\n".join(f"- {line}" for line in lines) if lines else f"- {empty}"


def prompt_variables(brief: GenerationBrief, label: str, strategy: str) -> dict[str, str]:
    """Variables of ``thumbnail_generation/v1``."""
    base = {
        "current_thumbnail": "Image 1 is the CURRENT thumbnail of this video: keep its topic, "
        "the people and the key objects recognisable, but redesign it to fix the problems below. Do not copy its layout.",  # noqa: E501
        "parent_variant": "Image 1 is a variant the owner liked: keep its idea, composition and "
        "style, and make a fresh variation of it (not a copy).",
        None: "No current thumbnail is attached: design from the description.",
    }[brief.base_image]
    refs = [
        f"{r.title} ({r.style or 'style not set'}): {r.description or 'no description'}"
        for r in brief.references
    ]
    first_ref = 2 if brief.base_image else 1
    references = (
        f"Images {first_ref}..{first_ref + len(refs) - 1} are style references chosen by the owner: "
        "borrow their style, colour and mood, never copy them.\n" + _bullets(refs, "")
        if refs
        else "- none"
    )
    history: list[str] = []
    if brief.history.approved:
        history.append("the owner liked these directions before: " + ", ".join(brief.history.approved))
    if brief.history.rejected:
        history.append("the owner rejected these directions before: " + ", ".join(brief.history.rejected))
    return {
        "label": label,
        "strategy": strategy,
        "channel_title": brief.channel_title,
        "niches": ", ".join(brief.niches) or "not specified",
        "video_title": brief.video_title or "(no specific video)",
        "base_image": base,
        "fixes": _bullets([f"{f.directive} (fixes: {f.code})" for f in brief.fixes], "no analysed problems"),
        "recommendations": _bullets(brief.recommendations, "none"),
        "channel_style": brief.channel_summary or "not analysed",
        "palette": ", ".join(brief.palette) or "not measured",
        "instructions": brief.instructions or "none",
        "references": references,
        "history": _bullets(history, "no earlier decisions"),
    }
