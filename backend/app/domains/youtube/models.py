"""Public YouTube data — global entities shared by all projects/workspaces (§49–50)."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from app.core.db import Base, IdMixin, TimestampMixin


class Channel(IdMixin, TimestampMixin, Base):
    __tablename__ = "channels"
    __table_args__ = (
        Index(
            "ix_channels_title_trgm", "title",
            postgresql_using="gin", postgresql_ops={"title": "gin_trgm_ops"},
        ),
    )
    youtube_channel_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    handle: Mapped[str | None] = mapped_column(CITEXT, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    custom_url: Mapped[str | None] = mapped_column(String(300))
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    country: Mapped[str | None] = mapped_column(String(2))
    default_language: Mapped[str | None] = mapped_column(String(20))
    published_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    uploads_playlist_id: Mapped[str | None] = mapped_column(String(64))
    subscriber_count: Mapped[int | None] = mapped_column(BigInteger, index=True)
    subscribers_hidden: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    view_count: Mapped[int | None] = mapped_column(BigInteger, index=True)
    video_count: Mapped[int | None] = mapped_column(Integer, index=True)
    topic_categories: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list, server_default="{}"
    )
    keywords: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list, server_default="{}"
    )
    last_fetched_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/channel/{self.youtube_channel_id}"


class ChannelStatsSnapshot(IdMixin, Base):
    __tablename__ = "channel_stats_snapshots"
    __table_args__ = (UniqueConstraint("channel_id", "captured_on"),)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), nullable=False)
    captured_on: Mapped[date] = mapped_column(Date, nullable=False)
    subscriber_count: Mapped[int | None] = mapped_column(BigInteger)
    view_count: Mapped[int | None] = mapped_column(BigInteger)
    video_count: Mapped[int | None] = mapped_column(Integer)


class Video(IdMixin, TimestampMixin, Base):
    __tablename__ = "videos"
    __table_args__ = (Index("ix_videos_channel_published", "channel_id", "published_at"),)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), nullable=False)
    youtube_video_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    published_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    is_short: Mapped[bool | None] = mapped_column(Boolean)
    category_id: Mapped[str | None] = mapped_column(String(10))
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list, server_default="{}")
    view_count: Mapped[int | None] = mapped_column(BigInteger)
    like_count: Mapped[int | None] = mapped_column(BigInteger)
    comment_count: Mapped[int | None] = mapped_column(BigInteger)
    last_fetched_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")


class VideoStatsSnapshot(IdMixin, Base):
    __tablename__ = "video_stats_snapshots"
    __table_args__ = (UniqueConstraint("video_id", "captured_on"),)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), nullable=False)
    captured_on: Mapped[date] = mapped_column(Date, nullable=False)
    view_count: Mapped[int | None] = mapped_column(BigInteger)
    like_count: Mapped[int | None] = mapped_column(BigInteger)
    comment_count: Mapped[int | None] = mapped_column(BigInteger)
