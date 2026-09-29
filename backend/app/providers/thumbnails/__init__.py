from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

from app.core.config import Settings
from app.providers.thumbnails.base import ThumbnailFetcher
from app.providers.thumbnails.http import HttpThumbnailFetcher
from app.providers.thumbnails.mock import MockThumbnailFetcher

__all__ = ["HttpThumbnailFetcher", "MockThumbnailFetcher", "ThumbnailFetcher", "open_thumbnail_fetcher"]


@asynccontextmanager
async def open_thumbnail_fetcher(settings: Settings) -> AsyncIterator[ThumbnailFetcher]:
    """Fetcher selected by ``YTL_THUMBNAIL_FETCHER`` (owns its HTTP client for the duration)."""
    if settings.effective_thumbnail_fetcher == "mock":
        yield MockThumbnailFetcher()
        return
    async with httpx.AsyncClient(
        timeout=settings.http_timeout_seconds, headers={"User-Agent": "ytlead/0.1 (+thumbnail fetcher)"}
    ) as client:
        yield HttpThumbnailFetcher(client, max_bytes=settings.max_upload_bytes)
