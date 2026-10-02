"""AI provider protocol (ADR-0007).

Providers only transport: they send a structured-output request and return the raw text plus usage.
Validation against the Pydantic schema, the repair attempt, fallback between models, cost accounting and
budgets live in ``app.domains.ai.runner`` so every provider behaves the same.

Errors are raised as classified ``IntegrationError``; ``MODEL_UNAVAILABLE`` (unknown model ID, missing
capability) makes the runner fall back to the next model of the task route.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

ImageDetail = Literal["low", "high", "auto"]


@dataclass(frozen=True)
class ImageInput:
    data: bytes
    mime: str
    detail: ImageDetail = "low"


@dataclass(frozen=True)
class StructuredRequest:
    model: str  # provider's API model ID
    system: str
    user: str
    schema_name: str
    json_schema: dict[str, Any]
    images: Sequence[ImageInput] = field(default_factory=tuple)
    max_output_tokens: int | None = None


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    image_inputs: int = 0


@dataclass(frozen=True)
class StructuredResponse:
    text: str | None  # JSON text as returned (not validated yet)
    refusal: str | None  # set when the model refused
    usage: Usage
    model: str  # model ID reported by the provider
    response_id: str | None = None
    incomplete_reason: str | None = None  # e.g. "max_output_tokens" (output likely truncated)


MAX_REFERENCE_IMAGES = 4  # OpenAI-compatible /images/edits limit


@dataclass(frozen=True)
class ImageGenRequest:
    """Image generation (``references`` empty) or edit (1–4 reference images, sent as files)."""

    model: str  # provider's API model ID
    prompt: str
    references: Sequence[ImageInput] = field(default_factory=tuple)
    size: str | None = None  # e.g. "1024x1024"; None = model default
    quality: str | None = None
    n: int = 1  # edits always return one image


@dataclass(frozen=True)
class GeneratedImage:
    data: bytes
    mime: str


@dataclass(frozen=True)
class ImageGenResponse:
    images: list[GeneratedImage]
    model: str


class AIProvider(Protocol):
    name: str  # "vibecode" | "openai" | "mock" — matches ``ai_models.provider``
    is_mock: bool

    async def generate_structured(self, request: StructuredRequest) -> StructuredResponse: ...

    async def generate_images(self, request: ImageGenRequest) -> ImageGenResponse:
        """Generate (or, with references, edit) images; bytes are returned, never temporary URLs."""
        ...

    async def list_models(self) -> list[str]:
        """Model IDs available to the configured credentials (used to verify the registry, Q-004)."""
        ...
