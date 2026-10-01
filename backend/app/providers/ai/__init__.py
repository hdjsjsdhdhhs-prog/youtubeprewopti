from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.core.config import Settings
from app.core.errors import IntegrationError, IntegrationErrorCode
from app.providers.ai.base import (
    AIProvider,
    ImageDetail,
    ImageInput,
    StructuredRequest,
    StructuredResponse,
    Usage,
)
from app.providers.ai.mock import MockAIProvider

__all__ = [
    "AIProvider", "ImageDetail", "ImageInput", "MockAIProvider", "StructuredRequest", "StructuredResponse",
    "Usage", "open_ai_provider",
]


@asynccontextmanager
async def open_ai_provider(settings: Settings) -> AsyncIterator[AIProvider]:
    """Provider selected by ``YTL_AI_PROVIDER`` (owns its HTTP client for the duration)."""
    mode = settings.effective_ai_provider
    if mode == "mock":
        yield MockAIProvider()
        return
    if mode != "openai" or settings.openai_api_key is None:
        raise IntegrationError(
            IntegrationErrorCode.NOT_CONFIGURED,
            "AI provider is not configured: set YTL_OPENAI_API_KEY (or YTL_AI_PROVIDER=mock).",
            retryable=False, provider="openai",
        )
    from openai import AsyncOpenAI  # imported lazily: the SDK stays inside the adapter

    from app.providers.ai.openai_responses import OpenAIResponsesProvider

    client = AsyncOpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
        timeout=settings.openai_timeout_seconds,
        max_retries=settings.openai_max_retries,
    )
    try:
        yield OpenAIResponsesProvider(client)
    finally:
        await client.close()
