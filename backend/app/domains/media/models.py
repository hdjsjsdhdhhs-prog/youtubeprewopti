from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from app.core.db import Base, IdMixin, TimestampMixin
from app.core.types import pg_enum


class ImageSource(StrEnum):
    YOUTUBE_THUMBNAIL = "youtube_thumbnail"
    GENERATED = "generated"
    REFERENCE = "reference"
    UPLOAD = "upload"
    DEMO = "demo"


class FetchStatus(StrEnum):
    PENDING = "pending"
    OK = "ok"
    FAILED = "failed"


class ImageAsset(IdMixin, TimestampMixin, Base):
    """Content-addressed image; the single deduplication point (ADR-0006, §34)."""

    __tablename__ = "image_assets"
    sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    phash: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    storage_backend: Mapped[str] = mapped_column(String(20), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(300), nullable=False)
    mime: Mapped[str] = mapped_column(String(50), nullable=False)
    format: Mapped[str] = mapped_column(String(10), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[ImageSource] = mapped_column(pg_enum(ImageSource, "image_source"), nullable=False)


class Thumbnail(IdMixin, TimestampMixin, Base):
    __tablename__ = "thumbnails"
    video_id: Mapped[int] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    image_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("image_assets.id", ondelete="SET NULL"), index=True
    )
    original_url: Mapped[str] = mapped_column(String(500), nullable=False)
    fetch_status: Mapped[FetchStatus] = mapped_column(
        pg_enum(FetchStatus, "fetch_status"), nullable=False, default=FetchStatus.PENDING,
        server_default=FetchStatus.PENDING.value,
    )
    fetched_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class ImageMetrics(Base):
    """Deterministic ("objective") image metrics, no AI (§6, AI_ARCHITECTURE §2 step 1).

    Computed once per content-addressed asset on a fixed working size, so values are comparable across
    thumbnails of any resolution. ``algo_version`` changes => recomputed by the next ingestion run.
    Text share / face count (Q-009) are deliberately not computed yet.
    """

    __tablename__ = "image_metrics"
    __table_args__ = (
        CheckConstraint("luminance_mean BETWEEN 0 AND 1", name="luminance_range"),
        CheckConstraint("edge_density BETWEEN 0 AND 1", name="edge_density_range"),
        CheckConstraint(
            "saliency_center_ratio IS NULL OR saliency_center_ratio BETWEEN 0 AND 1", name="saliency_range"
        ),
    )
    image_asset_id: Mapped[int] = mapped_column(
        ForeignKey("image_assets.id", ondelete="CASCADE"), primary_key=True
    )
    algo_version: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    work_width: Mapped[int] = mapped_column(Integer, nullable=False)
    work_height: Mapped[int] = mapped_column(Integer, nullable=False)
    letterbox_cropped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    luminance_mean: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    contrast_rms: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    colorfulness: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    sharpness_laplacian: Mapped[float] = mapped_column(Float, nullable=False)
    edge_density: Mapped[float] = mapped_column(Float, nullable=False)
    saliency_center_ratio: Mapped[float | None] = mapped_column(Float)
    dominant_colors: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
