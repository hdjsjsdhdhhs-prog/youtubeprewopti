from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, ForeignKey, Integer, String, Text
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
