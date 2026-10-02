from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.core.config import Settings
from app.core.errors import IntegrationError, IntegrationErrorCode
from app.providers.ai.base import (
    AIProvider,
    GeneratedImage,
    ImageDetail,
    ImageGenRequest,
    ImageGenResponse,
    ImageInput,
    StructuredRequest,
    StructuredResponse,
    Usage,
)
from app.providers.ai.mock import MockAIProvider

__all__ = [
    "AIProvider", "GeneratedImage", "ImageDetail", "ImageGenRequest", "ImageGenResponse", "ImageInput",
    "MockAIProvider", "StructuredRequest", "StructuredResponse", "Usage", "open_ai_provider",
]

NOT_CONFIGURED_HINT = "set YTL_VIBECODE_API_KEY (or YTL_OPENAI_API_KEY, or YTL_AI_PROVIDER=mock)"


@asynccontextmanager
async def open_ai_provider(settings: Settings) -> AsyncIterator[AIProvider]:
    """Provider selected by ``YTL_AI_PROVIDER`` (owns its HTTP client for the duration)."""
    mode = settings.effective_ai_provider
    if mode == "mock":
        yield MockAIProvider()
        return
    key = {"vibecode": settings.vibecode_api_key, "openai": settings.openai_api_key}.get(mode or "")
    if mode is None or key is None:
        raise IntegrationError(
            IntegrationErrorCode.NOT_CONFIGURED,
            f"AI provider is not configured: {NOT_CONFIGURED_HINT}.",
            retryable=False, provider=mode or "ai",
        )
    from openai import AsyncOpenAI  # imported lazily: the SDK stays inside the adapter

    from app.providers.ai.openai_responses import OpenAIResponsesProvider

    if mode == "vibecode":
        client = AsyncOpenAI(
            api_key=key.get_secret_value(),
            base_url=settings.vibecode_base_url,
            timeout=settings.vibecode_timeout_seconds,
            max_retries=settings.vibecode_max_retries,
        )
        provider = OpenAIResponsesProvider(
            client, name="vibecode", image_timeout_seconds=settings.vibecode_image_timeout_seconds,
            inline_image_bytes=True,  # vibecode image URLs expire after a few hours
        )
    else:
        client = AsyncOpenAI(
            api_key=key.get_secret_value(),
            timeout=settings.openai_timeout_seconds,
            max_retries=settings.openai_max_retries,
        )
        provider = OpenAIResponsesProvider(client)
    try:
        yield provider
    finally:
        await client.close()
