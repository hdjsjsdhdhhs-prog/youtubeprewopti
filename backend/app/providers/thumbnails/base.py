"""Thumbnail download provider protocol (ADR-0008: thumbnails come from i.ytimg.com, no API quota)."""

from __future__ import annotations

from typing import Protocol

from app.domains.media.models import ImageSource


class ThumbnailFetcher(Protocol):
    name: str
    image_source: ImageSource  # how downloaded images are labelled (mock => DEMO)

    async def fetch(self, url: str) -> bytes:
        """Return the image bytes or raise ``IntegrationError`` (classified, with ``retryable``)."""
        ...
