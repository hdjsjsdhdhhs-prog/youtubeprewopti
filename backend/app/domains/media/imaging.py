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


# ---------------------------------------------------------------------------
# Deterministic image metrics (no AI). Bump METRICS_ALGO_VERSION whenever a formula, a constant or the
# working size changes: stored rows of older versions are recomputed by the next ingestion run.
# ---------------------------------------------------------------------------
METRICS_ALGO_VERSION = 1
WORK_WIDTH = 320  # every image is analysed at this width (aspect kept) => values are comparable
LETTERBOX_LUMA = 24.0  # rows whose brightest pixel is darker than this count as a black bar
LETTERBOX_MIN_BAR = 0.04  # each bar must cover ≥ 4 % of the height (hqdefault.jpg: 12.5 %)
LETTERBOX_MAX_CROP = 0.5  # never crop more than half of the image
EDGE_THRESHOLD = 100.0  # Sobel magnitude (0..~1442 on 0..255 luma) above which a pixel is an "edge"
DOMINANT_COLORS = 5
DOMINANT_MIN_SHARE = 0.01


@dataclass(frozen=True)
class ImageMetricValues:
    work_width: int
    work_height: int
    letterbox_cropped: bool
    luminance_mean: float  # 0..1, mean Rec.601 luma
    contrast_rms: float  # 0..0.5, std of luma (RMS contrast)
    colorfulness: float  # Hasler & Süsstrunk (2003) M; ~0 grey, >100 extremely colourful
    sharpness_laplacian: float  # variance of the 4-neighbour Laplacian of luma (higher = sharper)
    # 0..1, share of pixels on a thinned (non-maximum suppressed) edge with Sobel magnitude > EDGE_THRESHOLD:
    # how busy the picture is. Blur does not change it much (that is what sharpness measures).
    edge_density: float
    saliency_center_ratio: float | None  # 0..1, share of gradient energy in the central 50 %×50 % box
    dominant_colors: list[dict[str, float | str]]  # [{"hex": "#rrggbb", "share": 0.42}, …] by share

    def as_row(self) -> dict[str, object]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def _crop_letterbox(rgb: np.ndarray) -> tuple[np.ndarray, bool]:
    """Remove black bars at the top *and* bottom (YouTube's 4:3 ``hqdefault`` of a 16:9 video)."""
    row_max = (rgb @ np.array([0.299, 0.587, 0.114])).max(axis=1)
    h = rgb.shape[0]
    top = int(np.argmax(row_max >= LETTERBOX_LUMA)) if (row_max >= LETTERBOX_LUMA).any() else h
    bottom = int(np.argmax(row_max[::-1] >= LETTERBOX_LUMA)) if top < h else 0
    min_bar = max(1, round(h * LETTERBOX_MIN_BAR))
    if top >= min_bar and bottom >= min_bar and top + bottom <= h * LETTERBOX_MAX_CROP:
        return rgb[top : h - bottom], True
    return rgb, False


# Neighbour offsets (d_row, d_col) along the quantised gradient direction: 0°, 45°, 90°, 135°
# (rows grow downwards, matching the sign of ``gy``).
_NMS_OFFSETS = ((0, 1), (1, 1), (1, 0), (1, -1))


def _thin_edges(mag: np.ndarray, gx: np.ndarray, gy: np.ndarray) -> np.ndarray:
    """Non-maximum suppression (as in Canny): keep a pixel only if its gradient magnitude is a local
    maximum across the edge, so every edge counts as a 1-px line no matter how blurred it is."""
    direction = (np.round(np.arctan2(gy, gx) / (np.pi / 4)).astype(int)) % 4
    padded = np.pad(mag, 1)
    h, w = mag.shape
    keep = np.zeros(mag.shape, dtype=bool)
    for q, (dr, dc) in enumerate(_NMS_OFFSETS):
        ahead = padded[1 + dr : 1 + dr + h, 1 + dc : 1 + dc + w]
        behind = padded[1 - dr : 1 - dr + h, 1 - dc : 1 - dc + w]
        keep |= (direction == q) & (mag > ahead) & (mag >= behind)  # strict on one side breaks plateau ties
    return keep


def _dominant_colors(im: Image.Image) -> list[dict[str, float | str]]:
    small = im.resize((64, max(1, round(64 * im.height / im.width))), Image.Resampling.BILINEAR)
    quant = small.quantize(colors=DOMINANT_COLORS, method=Image.Quantize.MEDIANCUT)
    palette = quant.getpalette() or []
    counts = quant.getcolors() or []
    total = sum(c for c, _ in counts) or 1
    out: list[dict[str, float | str]] = []
    for count, idx in sorted(counts, key=lambda ci: (-ci[0], ci[1])):
        share = count / total
        if share < DOMINANT_MIN_SHARE:
            continue
        i = int(idx)  # type: ignore[call-overload]  # P-mode colours are palette indices
        r, g, b = palette[i * 3 : i * 3 + 3]
        out.append({"hex": f"#{r:02x}{g:02x}{b:02x}", "share": round(share, 4)})
    return out


def compute_image_metrics(data: bytes) -> ImageMetricValues:
    """Objective metrics of an image (Pillow + NumPy only). Raises ``InvalidImageError``."""
    try:
        with Image.open(io.BytesIO(data)) as im:
            rgb_full = np.asarray(im.convert("RGB"), dtype=np.float64)
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise InvalidImageError(f"not a valid image: {exc}") from exc
    cropped, was_cropped = _crop_letterbox(rgb_full)
    src = Image.fromarray(cropped.astype(np.uint8), "RGB")
    work_h = max(8, round(WORK_WIDTH * src.height / src.width))
    work = src.resize((WORK_WIDTH, work_h), Image.Resampling.LANCZOS)
    rgb = np.asarray(work, dtype=np.float64)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    luma = 0.299 * r + 0.587 * g + 0.114 * b

    rg, yb = r - g, 0.5 * (r + g) - b
    colorfulness = float(np.hypot(rg.std(), yb.std()) + 0.3 * np.hypot(rg.mean(), yb.mean()))

    lap = luma[1:-1, :-2] + luma[1:-1, 2:] + luma[:-2, 1:-1] + luma[2:, 1:-1] - 4 * luma[1:-1, 1:-1]
    gx = (luma[:-2, 2:] + 2 * luma[1:-1, 2:] + luma[2:, 2:]) \
        - (luma[:-2, :-2] + 2 * luma[1:-1, :-2] + luma[2:, :-2])
    gy = (luma[2:, :-2] + 2 * luma[2:, 1:-1] + luma[2:, 2:]) \
        - (luma[:-2, :-2] + 2 * luma[:-2, 1:-1] + luma[:-2, 2:])
    mag = np.hypot(gx, gy)
    edges = _thin_edges(mag, gx, gy) & (mag > EDGE_THRESHOLD)
    mh, mw = mag.shape
    centre = mag[mh // 4 : mh - mh // 4, mw // 4 : mw - mw // 4]
    energy = float(mag.sum())

    return ImageMetricValues(
        work_width=WORK_WIDTH,
        work_height=work_h,
        letterbox_cropped=was_cropped,
        luminance_mean=round(float(luma.mean()) / 255, 6),
        contrast_rms=round(float(luma.std()) / 255, 6),
        colorfulness=round(colorfulness, 4),
        sharpness_laplacian=round(float(lap.var()), 4),
        edge_density=round(float(edges.mean()), 6),
        saliency_center_ratio=round(float(centre.sum()) / energy, 6) if energy > 0 else None,
        dominant_colors=_dominant_colors(work),
    )
