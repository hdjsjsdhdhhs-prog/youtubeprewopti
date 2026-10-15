"""Contactability Score (ТЗ §19): points per available contact type, weights configurable per workspace
(``scoring_profiles`` kind ``contactability``). Pure functions, no database.

Score = min(100, Σ points of the types the channel has at least one usable contact of). A type counts once,
however many contacts of it there are; Instagram counts as «other» (the ТЗ example lists Email, Telegram,
Website, VK and Other). Usable = present in the channel's texts (or added by hand), not invalid, not opted out
and not in the workspace suppression list.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.contacts.models import ContactType

WEIGHT_KEYS: tuple[str, ...] = ("email", "telegram", "website", "vk", "other")
DEFAULT_CONTACTABILITY_WEIGHTS: dict[str, float] = {
    "email": 30,
    "telegram": 30,
    "website": 20,
    "vk": 10,
    "other": 10,
}
WEIGHT_KEY: dict[ContactType, str] = {
    ContactType.EMAIL: "email",
    ContactType.TELEGRAM: "telegram",
    ContactType.WEBSITE: "website",
    ContactType.VK: "vk",
    ContactType.INSTAGRAM: "other",
    ContactType.OTHER: "other",
}
TYPE_LABELS: dict[str, str] = {
    "email": "email",
    "telegram": "Telegram",
    "website": "сайт",
    "vk": "VK",
    "other": "другие соцсети",
}

Points = Annotated[float, Field(ge=0, le=100)]


class ContactabilityWeights(BaseModel):
    """Points per available contact type; the score is capped at 100."""

    model_config = ConfigDict(extra="forbid")
    email: Points = DEFAULT_CONTACTABILITY_WEIGHTS["email"]
    telegram: Points = DEFAULT_CONTACTABILITY_WEIGHTS["telegram"]
    website: Points = DEFAULT_CONTACTABILITY_WEIGHTS["website"]
    vk: Points = DEFAULT_CONTACTABILITY_WEIGHTS["vk"]
    other: Points = DEFAULT_CONTACTABILITY_WEIGHTS["other"]

    @model_validator(mode="after")
    def _some_positive(self) -> ContactabilityWeights:
        if not any(getattr(self, k) > 0 for k in WEIGHT_KEYS):
            raise ValueError("at least one weight must be greater than 0")
        return self

    def as_dict(self) -> dict[str, float]:
        return {k: float(getattr(self, k)) for k in WEIGHT_KEYS}


def weight_keys(types: Iterable[ContactType | str]) -> list[str]:
    """Weight keys present among ``types``, in ``WEIGHT_KEYS`` order."""
    keys = {WEIGHT_KEY[ContactType(t)] for t in types}
    return [k for k in WEIGHT_KEYS if k in keys]


def contactability_score(types: Iterable[ContactType | str], weights: Mapping[str, float]) -> Decimal:
    total = sum(weights.get(k, 0.0) for k in weight_keys(types))
    return Decimal(str(min(100.0, total))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def describe(types: Iterable[ContactType | str], weights: Mapping[str, float]) -> str:
    """«email +30, Telegram +30» for the explanation of the Lead Score component."""
    return ", ".join(f"{TYPE_LABELS[k]} +{weights.get(k, 0.0):g}" for k in weight_keys(types))
