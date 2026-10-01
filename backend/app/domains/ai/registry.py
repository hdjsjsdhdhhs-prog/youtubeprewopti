"""Model registry and task routing (ADR-0007 §2–3).

Business code asks for a *task*; the route of the task is an ordered list of registry keys. The first
enabled model of the active provider that has the task's capabilities is used; the runner falls back to
the next one when the provider reports the model as unavailable.

Default rows are inserted on first use and never overwritten (prices / IDs can be edited through the API),
except ``vision-standard.api_model_id`` which follows ``YTL_OPENAI_VISION_MODEL`` when it is set.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domains.ai.models import AIModel


class Capability(StrEnum):
    TEXT = "text"
    VISION = "vision"
    JSON_SCHEMA = "json_schema"
    IMAGE_GENERATE = "image_generate"
    IMAGE_EDIT = "image_edit"


class AITask(StrEnum):
    THUMBNAIL_ANALYSIS = "thumbnail_analysis"
    CHANNEL_ANALYSIS = "channel_analysis"
    CLASSIFICATION = "classification"
    LEAD_EXPLANATION = "lead_explanation"
    OFFER_GENERATION = "offer_generation"


@dataclass(frozen=True)
class TaskSpec:
    capabilities: frozenset[Capability]
    route: tuple[str, ...]
    # Pre-flight estimate per call (prompt + structured answer), refined from ai_calls once real data exists.
    est_input_tokens: int
    est_output_tokens: int


_VISION = frozenset({Capability.TEXT, Capability.VISION, Capability.JSON_SCHEMA})
_TEXT = frozenset({Capability.TEXT, Capability.JSON_SCHEMA})
_VISION_ROUTE = ("vision-standard", "vision-premium", "mock-vision")

TASKS: dict[AITask, TaskSpec] = {
    AITask.THUMBNAIL_ANALYSIS: TaskSpec(_VISION, _VISION_ROUTE, 1200, 900),
    AITask.CHANNEL_ANALYSIS: TaskSpec(_VISION, _VISION_ROUTE, 3000, 1200),
    AITask.CLASSIFICATION: TaskSpec(_TEXT, ("text-bulk", "vision-standard", "mock-text"), 800, 300),
    AITask.LEAD_EXPLANATION: TaskSpec(_TEXT, ("text-bulk", "vision-standard", "mock-text"), 900, 250),
    AITask.OFFER_GENERATION: TaskSpec(_TEXT, ("text-premium", "vision-standard", "mock-text"), 2000, 900),
}

MOCK_NOTE = "Offline mock, free."
UNVERIFIED_NOTE = "API ID и цены не проверены (Q-004): проверить `python -m app.cli ai-models`."


@dataclass(frozen=True)
class ModelDefault:
    key: str
    provider: str
    api_model_id: str
    capabilities: frozenset[Capability]
    price_input_per_1m: Decimal | None = None
    price_output_per_1m: Decimal | None = None
    pricing_verified: bool = False
    notes: str = UNVERIFIED_NOTE


def default_models(settings: Settings) -> list[ModelDefault]:
    zero = Decimal(0)
    return [
        ModelDefault("vision-standard", "openai", settings.openai_vision_model or "gpt-6-sol", _VISION),
        ModelDefault("vision-premium", "openai", "gpt-6-astra", _VISION),
        ModelDefault("text-bulk", "openai", "gpt-6-luna", _TEXT),
        ModelDefault("text-premium", "openai", "gpt-6-astra", _TEXT),
        ModelDefault("mock-vision", "mock", "mock-vision-1", _VISION, zero, zero, True, MOCK_NOTE),
        ModelDefault("mock-text", "mock", "mock-text-1", _TEXT, zero, zero, True, MOCK_NOTE),
    ]


async def sync_registry(db: AsyncSession, settings: Settings) -> None:
    """Insert missing default models (idempotent; existing rows keep manual edits)."""
    now = datetime.now(UTC)
    for m in default_models(settings):
        await db.execute(
            insert(AIModel)
            .values(
                key=m.key, provider=m.provider, api_model_id=m.api_model_id,
                capabilities=sorted(c.value for c in m.capabilities),
                price_input_per_1m=m.price_input_per_1m, price_output_per_1m=m.price_output_per_1m,
                pricing_verified_at=now if m.pricing_verified else None, notes=m.notes,
            )
            .on_conflict_do_nothing(index_elements=["key"])
        )
    if settings.openai_vision_model:
        await db.execute(
            update(AIModel)
            .where(AIModel.key == "vision-standard", AIModel.api_model_id != settings.openai_vision_model)
            .values(api_model_id=settings.openai_vision_model)
        )


async def resolve_models(db: AsyncSession, settings: Settings, task: AITask) -> list[AIModel]:
    """Candidate models for ``task`` in route order (empty => AI not configured for this task)."""
    provider = settings.effective_ai_provider
    if provider is None:
        return []
    await sync_registry(db, settings)
    spec = TASKS[task]
    rows = {
        m.key: m
        for m in await db.scalars(select(AIModel).where(AIModel.key.in_(spec.route)))
    }
    needed = {c.value for c in spec.capabilities}
    return [
        rows[k] for k in spec.route
        if k in rows and rows[k].enabled and rows[k].provider == provider
        and needed <= set(rows[k].capabilities)
    ]
