from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.domains.favorites.models import FavoriteEntity


class FavoriteChannelRef(BaseModel):
    id: int
    youtube_channel_id: str
    title: str
    avatar_url: str | None
    subscriber_count: int | None
    is_demo: bool


class FavoriteVideoRef(BaseModel):
    id: int
    youtube_video_id: str
    title: str
    channel_id: int
    channel_title: str
    image_url: str | None  # the stored thumbnail (``/api/images/…``), None = not downloaded


class FavoriteOut(BaseModel):
    entity_type: FavoriteEntity
    entity_id: int
    created_at: datetime
    channel: FavoriteChannelRef | None  # entity_type = channel
    video: FavoriteVideoRef | None  # entity_type = thumbnail


class FavoriteList(BaseModel):
    items: list[FavoriteOut]
    total: int
    limit: int
    offset: int


class FavoriteState(BaseModel):
    entity_type: FavoriteEntity
    entity_id: int
    is_favorite: bool
