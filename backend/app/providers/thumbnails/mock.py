"""Offline thumbnail provider for demo mode and tests. Images are labelled ``ImageSource.DEMO``."""

from __future__ import annotations

import hashlib
import io

from PIL import Image, ImageDraw

from app.domains.media.models import ImageSource


def mock_thumbnail_bytes(url: str, size: tuple[int, int] = (320, 180)) -> bytes:
    """Deterministic JPEG derived from the URL (same URL => same bytes => same image asset)."""
    h = hashlib.sha256(url.encode("utf-8")).digest()
    w, hgt = size
    im = Image.new("RGB", size, (h[0], h[1], h[2]))
    draw = ImageDraw.Draw(im)
    for i in range(4):  # a few blocks so perceptual hashes differ between URLs
        x, y = h[3 + i] * w // 256, h[7 + i] * hgt // 256
        draw.rectangle((x, y, x + w // 4, y + hgt // 4), fill=(h[11 + i], h[15 + i], h[19 + i]))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


class MockThumbnailFetcher:
    name = "mock"
    image_source = ImageSource.DEMO

    async def fetch(self, url: str) -> bytes:
        return mock_thumbnail_bytes(url)
