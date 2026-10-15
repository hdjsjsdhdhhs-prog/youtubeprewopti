"""Offer drafting without the database (ТЗ §20–21, §28; AI_ARCHITECTURE §6).

* **Facts** — everything the offer may rely on is a numbered fact (F1, F2 …) built by the code from stored
  data: channel, niches, concrete videos, problems of their thumbnail audits, the channel audit, the free
  sample and what it fixes. The model gets only these facts and must say which ones it used.
* **Guard** (§20 «не придумывать цифры») — deterministic, on the exact text that would be sent: every
  number must occur in the facts (small counts ≤ 10 such as «3 варианта» are allowed), no CTR / conversion /
  percent statements, no guarantees, every claim of the draft must point to existing facts. Issues block
  the approval until the text is fixed (edited or regenerated).
* **Personalization score** (§21) — deterministic 0–100: 70 % = which kinds of concrete facts were used
  (channel / video facts only count when the text really names them), 30 % = uniqueness against the
  workspace's other offers (word 3-gram overlap). The used facts are listed next to the score.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domains.offers.models import LABELS, STYLES, OfferStyle

FactKind = Literal[
    "channel", "niche", "video", "thumbnail_problem", "recommendation", "channel_audit", "sample"
]
# weight of a fact kind in the personalization score (sum = 100: one fact of every kind = full marks)
KIND_WEIGHTS: dict[str, int] = {
    "channel": 10,
    "niche": 10,
    "video": 15,
    "thumbnail_problem": 20,
    "recommendation": 10,
    "channel_audit": 15,
    "sample": 20,
}
LITERAL_KINDS = frozenset({"channel", "video"})  # must be named in the text to count
FACTS_SHARE = Decimal("0.7")
UNIQUENESS_SHARE = Decimal("0.3")
SMALL_COUNT_MAX = 10
MAX_FACTS = 40
_Q2 = Decimal("0.01")

FactId = Annotated[int, Field(ge=1, le=MAX_FACTS)]


class OfferFact(BaseModel):
    id: int
    kind: FactKind
    field: str  # where it comes from, e.g. ``video.title``, ``thumbnail_analysis.problem``
    value: str
    ref: dict[str, int] = Field(default_factory=dict)  # ids (video_id, …) for traceability


class OfferSample(BaseModel):
    """The free sample attached to the offer (a generated variant) and what it fixes."""

    variant_id: int
    label: str
    strategy: str
    video_title: str | None
    fixes: list[str]  # problem codes the generation brief addressed


class OfferTemplateRef(BaseModel):
    id: int
    name: str
    version: int
    body: str


class OfferInputs(BaseModel):
    """Stored on the offer (``offers.inputs``): exactly what the drafts of the current round were written
    from. The guard and the score of edited texts use the same snapshot."""

    channel_title: str
    language: str
    facts: list[OfferFact]
    sample: OfferSample | None
    template: OfferTemplateRef | None
    instructions: str


# ---------------------------------------------------------------------------
# Structured output of ``offer_generation/v1``
# ---------------------------------------------------------------------------
class OfferStructure(BaseModel):
    """ТЗ §20: OBSERVATION → PROBLEM → SPECIFIC INSIGHT → VALUE → FREE SAMPLE → LOW-FRICTION CTA."""

    model_config = ConfigDict(extra="forbid")
    observation: str = Field(max_length=600)
    problem: str = Field(max_length=600)
    insight: str = Field(max_length=600)
    value: str = Field(max_length=600)
    free_sample: str = Field(max_length=600)
    cta: str = Field(max_length=600)


class OfferClaim(BaseModel):
    """A factual statement of the text about the channel and the facts it is based on."""

    model_config = ConfigDict(extra="forbid")
    text: str = Field(max_length=300)
    fact_ids: list[FactId] = Field(max_length=5)


class OfferDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str = Field(max_length=150)
    body: str = Field(max_length=4000)
    cta: str = Field(max_length=300)
    structure: OfferStructure
    facts_used: list[FactId] = Field(max_length=20)
    claims: list[OfferClaim] = Field(max_length=10)


# ---------------------------------------------------------------------------
# Prompt variables
# ---------------------------------------------------------------------------
STYLE_RULES: dict[OfferStyle, str] = {
    OfferStyle.PROFESSIONAL: (
        "professional and calm: a full message of 90-160 words that follows the six steps of the "
        "structure in order"
    ),
    OfferStyle.SHORT: "as short as possible: at most 60 words, 3-4 short sentences, no greetings padding",
    OfferStyle.FREE_SAMPLE: (
        "built around the free sample: open with what the sample changes and why it was made for this "
        "exact video, 70-120 words"
    ),
}
LANGUAGE_NAMES = {"ru": "Russian", "en": "English", "uk": "Ukrainian", "de": "German", "es": "Spanish"}


def style_for(index: int) -> tuple[str, OfferStyle]:
    return LABELS[index], STYLES[index]


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code.split("-")[0].lower(), code)


def facts_text(facts: Sequence[OfferFact]) -> str:
    return "\n".join(f"F{f.id} [{f.kind}] {f.field}: {f.value}" for f in facts) or "(no facts)"


def prompt_variables(inputs: OfferInputs, label: str, style: OfferStyle) -> dict[str, str]:
    """Variables of ``offer_generation/v1``."""
    s = inputs.sample
    sample = (
        f"Variant {s.label} ({s.strategy})"
        + (f' for the video "{s.video_title}"' if s.video_title else "")
        + (f"; it fixes: {', '.join(s.fixes)}" if s.fixes else "")
        + ". It is attached to the message."
        if s is not None
        else "No free sample is attached: do not promise an attachment; offer to make one."
    )
    template = (
        f'"{inputs.template.name}" (v{inputs.template.version}):\n{inputs.template.body}'
        if inputs.template is not None
        else "none"
    )
    return {
        "label": label,
        "style": style.value,
        "style_rules": STYLE_RULES[style],
        "language": language_name(inputs.language),
        "channel_title": inputs.channel_title,
        "facts": facts_text(inputs.facts),
        "sample": sample,
        "template": template,
        "instructions": inputs.instructions or "none",
    }


# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------
class GuardIssue(BaseModel):
    code: Literal["unsupported_number", "metric_claim", "guarantee", "unsupported_claim", "empty_text"]
    text: str  # the offending fragment
    message: str


_NUMBER = re.compile(r"\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?")
_PERCENT_AFTER = re.compile(r"^\s*(%|процент)", re.IGNORECASE)
_METRICS = re.compile(
    r"\bCTR\b|click[- ]?through|кликабельн\w*|конверси\w*|%|\bпроцент\w*|\bpercent\w*", re.IGNORECASE
)
_GUARANTEE = re.compile(r"гарантир\w*|guarantee\w*", re.IGNORECASE)


def _norm_number(raw: str) -> str:
    s = re.sub(r"[ \u00a0\u202f]", "", raw).replace(",", ".")
    try:
        d = Decimal(s).normalize()
    except ArithmeticError:  # pragma: no cover - the regex only matches numbers
        return s
    return format(d, "f")


def numbers_in(text: str) -> list[tuple[str, str, int]]:
    """(raw, normalized, end position) of every number in ``text``."""
    return [(m.group(0), _norm_number(m.group(0)), m.end()) for m in _NUMBER.finditer(text)]


def allowed_numbers(inputs: OfferInputs) -> set[str]:
    sources = [f.value for f in inputs.facts] + [inputs.channel_title, inputs.instructions]
    if inputs.template is not None:
        sources.append(inputs.template.body)
    return {n for src in sources for _, n, _ in numbers_in(src)}


def _small_count(norm: str) -> bool:
    return norm.isdigit() and int(norm) <= SMALL_COUNT_MAX


def guard(
    inputs: OfferInputs, subject: str, body: str, cta: str, claims: Sequence[OfferClaim] | None
) -> list[GuardIssue]:
    """Issues of the text that would be sent. ``claims`` None = the text was edited by hand (the AI's
    claims no longer describe it; only the text checks apply)."""
    issues: list[GuardIssue] = []
    if not body.strip():
        issues.append(GuardIssue(code="empty_text", text="", message="Текст сообщения пустой."))
    allowed = allowed_numbers(inputs)
    seen: set[str] = set()
    for part in (subject, body, cta):
        for raw, norm, end in numbers_in(part):
            percent = bool(_PERCENT_AFTER.match(part[end:]))
            if norm in allowed or (_small_count(norm) and not percent) or norm in seen:
                continue
            seen.add(norm)
            issues.append(
                GuardIssue(
                    code="unsupported_number",
                    text=raw,
                    message=f"Числа «{raw}» нет во входных данных: цифры нельзя придумывать.",
                )
            )
        for m in _METRICS.finditer(part):
            issues.append(
                GuardIssue(
                    code="metric_claim",
                    text=m.group(0),
                    message=f"«{m.group(0)}»: у нас нет данных о CTR, конверсии и процентах канала.",
                )
            )
        for m in _GUARANTEE.finditer(part):
            issues.append(
                GuardIssue(
                    code="guarantee", text=m.group(0), message="Нельзя обещать гарантированный результат."
                )
            )
    if claims is not None:
        known = {f.id for f in inputs.facts}
        for c in claims:
            if not c.fact_ids or not set(c.fact_ids) <= known:
                issues.append(
                    GuardIssue(
                        code="unsupported_claim",
                        text=c.text[:200],
                        message="Утверждение не опирается на известные факты о канале.",
                    )
                )
    return issues


# ---------------------------------------------------------------------------
# Personalization score
# ---------------------------------------------------------------------------
class PersonalizationSource(BaseModel):
    fact_id: int
    kind: str
    field: str
    value: str
    counted: bool  # False: listed by the model but not named in the text (channel / video facts)


_WORD = re.compile(r"\w+", re.UNICODE)


def _words(text: str) -> list[str]:
    return [w.casefold() for w in _WORD.findall(text)]


def shingles(text: str, n: int = 3) -> set[tuple[str, ...]]:
    w = _words(text)
    return {tuple(w[i : i + n]) for i in range(max(0, len(w) - n + 1))}


def max_overlap(body: str, others: Iterable[str]) -> float:
    """Highest Jaccard similarity of word 3-grams between ``body`` and any of ``others`` (0 = unique)."""
    mine = shingles(body)
    if not mine:
        return 0.0
    best = 0.0
    for other in others:
        theirs = shingles(other)
        if theirs:
            best = max(best, len(mine & theirs) / len(mine | theirs))
    return best


def _named(fact: OfferFact, text: str) -> bool:
    needle = fact.value.casefold().strip().strip("«»\"'")[:40]
    return bool(needle) and needle in text.casefold()


def personalization(
    inputs: OfferInputs, facts_used: Iterable[int], text: str, others: Iterable[str]
) -> tuple[Decimal, list[PersonalizationSource]]:
    by_id = {f.id: f for f in inputs.facts}
    sources: list[PersonalizationSource] = []
    kinds: set[str] = set()
    for fid in dict.fromkeys(facts_used):
        fact = by_id.get(fid)
        if fact is None:
            continue  # the model referred to a fact that does not exist
        counted = fact.kind not in LITERAL_KINDS or _named(fact, text)
        if counted:
            kinds.add(fact.kind)
        sources.append(
            PersonalizationSource(
                fact_id=fact.id, kind=fact.kind, field=fact.field, value=fact.value, counted=counted
            )
        )
    facts_part = Decimal(min(100, sum(KIND_WEIGHTS[k] for k in kinds)))
    uniqueness = Decimal(str(round(100 * (1 - max_overlap(text, others)), 2)))
    score = (FACTS_SHARE * facts_part + UNIQUENESS_SHARE * uniqueness).quantize(_Q2)
    return min(Decimal(100), max(Decimal(0), score)), sources
