"""Image validation, normalization metadata and perceptual hashing (no AI)."""

from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
from PIL import Image, UnidentifiedImageError

ALLOWED_FORMATS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
MAX_PIXELS = 40_000_000


class InvalidImageError(ValueError):
    pass


@dataclass(frozen=True)
class ImageInfo:
    format: str
    ext: str
    mime: str
    width: int
    height: int


def inspect_image(data: bytes) -> ImageInfo:
    """Validate by content signature (not filename) and return basic metadata."""
    if not data:
        raise InvalidImageError("empty image")
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
        with Image.open(io.BytesIO(data)) as im:
            fmt = im.format or ""
            width, height = im.size
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise InvalidImageError(f"not a valid image: {exc}") from exc
    if fmt not in ALLOWED_FORMATS:
        raise InvalidImageError(f"unsupported image format: {fmt or 'unknown'}")
    if width * height > MAX_PIXELS:
        raise InvalidImageError("image too large")
    ext = ALLOWED_FORMATS[fmt]
    return ImageInfo(format=fmt, ext=ext, mime=Image.MIME.get(fmt, "application/octet-stream"),
                     width=width, height=height)


def _dct_matrix(n: int) -> np.ndarray:
    k = np.arange(n)
    m = np.cos(np.pi * (2 * k[None, :] + 1) * k[:, None] / (2 * n))
    m[0, :] *= 1 / np.sqrt(2)
    return m * np.sqrt(2 / n)


_DCT32 = _dct_matrix(32)


def phash(data: bytes) -> int:
    """64-bit DCT perceptual hash (same idea as imagehash.phash), returned as signed int64."""
    with Image.open(io.BytesIO(data)) as im:
        gray = im.convert("L").resize((32, 32), Image.Resampling.LANCZOS)
    pixels = np.asarray(gray, dtype=np.float64)
    dct = _DCT32 @ pixels @ _DCT32.T
    low = dct[:8, :8]
    med = np.median(low.flatten()[1:])  # exclude DC term
    bits = (low > med).flatten()
    value = 0
    for b in bits:
        value = (value << 1) | int(b)
    # store as signed BIGINT
    return value - (1 << 64) if value >= (1 << 63) else value


def hamming(a: int, b: int) -> int:
    return bin((a ^ b) & 0xFFFFFFFFFFFFFFFF).count("1")
