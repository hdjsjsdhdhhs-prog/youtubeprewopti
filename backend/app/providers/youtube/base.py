"""YouTube discovery provider protocol (ADR-0008).

Only the calls the discovery pipeline needs. Every method documents its quota cost; callers reserve
units in the quota ledger *before* calling (``app/domains/discovery/quota.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

# Official costs (developers.google.com/youtube/v3/determine_quota_cost), verified 2026-09-24.
SEARCH_COST = 100
LIST_COST = 1  # channels.list / videos.list / playlistItems.list
MAX_PAGE_SIZE = 50  # maxResults and ids-per-call limit


class SearchType(StrEnum):
    VIDEO = "video"  # keyword search over videos → their channels (main discovery path)
    CHANNEL = "channel"  # search channels by topic/niche name


@dataclass(frozen=True)
class SearchHit:
    channel_id: str
    video_id: str | None  # None for channel search results


@dataclass(frozen=True)
class SearchPage:
    hits: list[SearchHit]
    next_page_token: str | None


@dataclass(frozen=True)
class ChannelInfo:
    youtube_channel_id: str
    title: str
    description: str
    handle: str | None
    custom_url: str | None
    avatar_url: str | None
    country: str | None
    default_language: str | None
    published_at: datetime | None
    uploads_playlist_id: str | None
    subscriber_count: int | None
    subscribers_hidden: bool
    view_count: int | None
    video_count: int | None
    topic_categories: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VideoInfo:
    youtube_video_id: str
    youtube_channel_id: str
    title: str
    description: str
    published_at: datetime | None
    duration_seconds: int | None
    category_id: str | None
    tags: list[str]
    view_count: int | None
    like_count: int | None
    comment_count: int | None
    thumbnail_url: str | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchParams:
    query: str
    search_type: SearchType
    max_results: int  # 1..50 for this page
    page_token: str | None = None
    language: str | None = None  # relevanceLanguage
    region_code: str | None = None
    published_after: datetime | None = None  # video search only


class YouTubeProvider(Protocol):
    name: str  # also the quota ledger key
    is_mock: bool  # mock data is stored with is_demo=true

    async def search(self, params: SearchParams) -> SearchPage:
        """``search.list`` — SEARCH_COST units."""
        ...

    async def get_channels(self, channel_ids: list[str]) -> list[ChannelInfo]:
        """``channels.list`` for ≤ 50 ids — LIST_COST. Unknown/terminated ids are simply absent."""
        ...

    async def get_playlist_video_ids(self, playlist_id: str, max_results: int) -> list[str]:
        """Newest ``max_results`` (≤ 50) video ids of an uploads playlist — LIST_COST.
        A missing playlist (channel without uploads) returns ``[]``."""
        ...

    async def get_videos(self, video_ids: list[str]) -> list[VideoInfo]:
        """``videos.list`` for ≤ 50 ids — LIST_COST. Private/deleted videos are absent."""
        ...
