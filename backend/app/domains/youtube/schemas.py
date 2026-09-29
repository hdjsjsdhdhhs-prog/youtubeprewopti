from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.domains.media.models import FetchStatus


class ChannelSort(StrEnum):
    SUBSCRIBERS = "subscribers"
    VIEWS = "views"
    VIDEOS = "videos"
    TITLE = "title"
    PUBLISHED = "published"
    DISCOVERED = "discovered"


class SortOrder(StrEnum):
    ASC = "asc"
    DESC = "desc"


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


class ProjectRef(BaseModel):
    id: int
    name: str


class ChannelDetail(ChannelOut):
    description: str
    custom_url: str | None
    keywords: list[str]
    topic_categories: list[str]
    projects: list[ProjectRef]
    videos_stored: int


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
