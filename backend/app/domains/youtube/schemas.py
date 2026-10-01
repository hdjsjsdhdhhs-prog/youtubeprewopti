from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.domains.media.models import FetchStatus


class ChannelSort(StrEnum):
    SUBSCRIBERS = "subscribers"
    VIEWS = "views"
    VIDEOS = "videos"
    TITLE = "title"
    PUBLISHED = "published"
    DISCOVERED = "discovered"
    AVG_VIEWS = "avg_views"
    MEDIAN_VIEWS = "median_views"
    LAST_VIDEO = "last_video"
    VIEWS_RATIO = "views_ratio"
    VIDEOS_30D = "videos_30d"


class SortOrder(StrEnum):
    ASC = "asc"
    DESC = "desc"


NonNeg = Annotated[int, Field(ge=0)]


class ChannelFilterSet(BaseModel):
    """Channel filters (§3). Used by ``GET /api/channels`` and stored as a project's ``filter_settings``.

    Metric filters exclude channels without computed metrics (not fetched by discovery yet).
    ``niche`` / ``exclude_niche`` match taxonomy nodes *with their descendants* through the queries a
    channel was discovered by (Phase 3 adds AI classification as a second source)."""

    model_config = ConfigDict(extra="forbid")

    min_subscribers: NonNeg | None = None
    max_subscribers: NonNeg | None = None
    min_avg_views: NonNeg | None = None
    max_avg_views: NonNeg | None = None
    min_median_views: NonNeg | None = None
    min_last_video_views: NonNeg | None = None
    min_avg_views_recent: NonNeg | None = None
    min_videos: NonNeg | None = None
    max_videos: NonNeg | None = None
    min_videos_7d: NonNeg | None = None
    min_videos_30d: NonNeg | None = None
    min_videos_90d: NonNeg | None = None
    max_avg_upload_gap_days: float | None = Field(default=None, ge=0)
    max_days_since_last_upload: NonNeg | None = None
    min_views_to_subs: float | None = Field(default=None, ge=0)
    min_upload_consistency: float | None = Field(default=None, ge=0, le=1)
    country: Annotated[str, StringConstraints(to_upper=True, pattern=r"^[A-Za-z]{2}$")] | None = None
    language: Annotated[str, StringConstraints(strip_whitespace=True, max_length=20)] | None = None
    niche: list[int] = Field(default_factory=list, max_length=50)
    exclude_niche: list[int] = Field(default_factory=list, max_length=50)


class ChannelMetricsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    computed_at: datetime
    window_videos: int
    avg_views: int | None
    median_views: int | None
    last_video_views: int | None
    avg_views_recent: int | None
    views_to_subs_ratio: float | None
    median_views_to_subs_ratio: float | None
    videos_7d: int
    videos_30d: int
    videos_90d: int
    avg_days_between_uploads: float | None
    last_video_at: datetime | None
    oldest_window_video_at: datetime | None
    upload_consistency: float | None
    views_trend: float | None
    recent_views_velocity: float | None


class ChannelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    youtube_channel_id: str
    url: str
    handle: str | None
    title: str
    avatar_url: str | None
    country: str | None
    default_language: str | None
    subscriber_count: int | None
    subscribers_hidden: bool
    view_count: int | None
    video_count: int | None
    published_at: datetime | None
    last_fetched_at: datetime | None
    is_demo: bool
    metrics: ChannelMetricsOut | None = None


class ProjectRef(BaseModel):
    id: int
    name: str


class NicheRef(BaseModel):
    id: int
    name: str
    level: str


class DiscoveryOut(BaseModel):
    """Where/when/how the channel was found (§2, §50)."""

    project: ProjectRef
    query_id: int | None
    query_text: str | None
    niche: NicheRef | None
    method: str
    source_video_id: int | None
    discovered_at: datetime


class ChannelDetail(ChannelOut):
    description: str
    custom_url: str | None
    keywords: list[str]
    topic_categories: list[str]
    projects: list[ProjectRef]
    videos_stored: int
    niches: list[NicheRef]
    discoveries: list[DiscoveryOut]  # newest first, at most MAX_DISCOVERIES_SHOWN
    discoveries_total: int


class ThumbnailOut(BaseModel):
    fetch_status: FetchStatus
    original_url: str
    image_asset_id: int | None
    image_url: str | None  # served by the API with access control; never a direct storage path
    width: int | None
    height: int | None


class VideoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    channel_id: int
    youtube_video_id: str
    title: str
    published_at: datetime | None
    duration_seconds: int | None
    is_short: bool | None
    view_count: int | None
    like_count: int | None
    comment_count: int | None
    thumbnail: ThumbnailOut | None = None


class VideoDetail(VideoOut):
    description: str
    tags: list[str]
    category_id: str | None
