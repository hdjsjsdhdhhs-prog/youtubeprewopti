from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

from app.core.config import Settings
from app.core.errors import IntegrationError, IntegrationErrorCode
from app.providers.youtube.base import (
    LIST_COST,
    MAX_PAGE_SIZE,
    SEARCH_COST,
    ChannelInfo,
    SearchHit,
    SearchPage,
    SearchParams,
    SearchType,
    VideoInfo,
    YouTubeProvider,
)
from app.providers.youtube.http import YouTubeDataApiProvider
from app.providers.youtube.mock import MockYouTubeProvider

__all__ = [
    "LIST_COST", "MAX_PAGE_SIZE", "SEARCH_COST", "ChannelInfo", "MockYouTubeProvider", "SearchHit",
    "SearchPage", "SearchParams", "SearchType", "VideoInfo", "YouTubeDataApiProvider", "YouTubeProvider",
    "not_configured_error", "open_youtube_provider",
]


def not_configured_error() -> IntegrationError:
    return IntegrationError(
        IntegrationErrorCode.NOT_CONFIGURED,
        "YouTube API is not configured: set YTL_YOUTUBE_API_KEY (or YTL_YOUTUBE_PROVIDER=mock for offline).",
        retryable=False, provider="youtube",
    )


@asynccontextmanager
async def open_youtube_provider(settings: Settings) -> AsyncIterator[YouTubeProvider]:
    """Provider selected by ``YTL_YOUTUBE_PROVIDER`` (owns its HTTP client for the duration)."""
    mode = settings.effective_youtube_provider
    if mode == "mock":
        yield MockYouTubeProvider()
        return
    if mode is None or settings.youtube_api_key is None:
        raise not_configured_error()
    async with httpx.AsyncClient(
        timeout=settings.http_timeout_seconds, headers={"User-Agent": "ytlead/0.1 (+discovery)"}
    ) as client:
        yield YouTubeDataApiProvider(client, settings.youtube_api_key.get_secret_value())
