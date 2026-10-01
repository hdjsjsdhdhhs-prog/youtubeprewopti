"""Offline, deterministic YouTube provider for demo mode and tests (§67).

Everything it returns is clearly synthetic: ids start with ``demo-yt-``, titles with ``[DEMO]``,
thumbnail URLs point to the reserved ``.invalid`` TLD (the real thumbnail fetcher refuses them).
Channels are drawn from a fixed pool, so different queries overlap — this exercises deduplication.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from app.providers.youtube.base import (
    MAX_PAGE_SIZE,
    ChannelInfo,
    SearchHit,
    SearchPage,
    SearchParams,
    SearchType,
    VideoInfo,
)

PROVIDER = "youtube_mock"
ID_PREFIX = "demo-yt-"
POOL_SIZE = 40
UPLOADS_PER_CHANNEL = 30
MAX_PAGES = 3
THUMBNAIL_HOST = "demo-thumbnails.invalid"
_EPOCH = datetime(2026, 9, 1, tzinfo=UTC)  # fixed: results do not depend on the wall clock


def _num(key: str, lo: int, hi: int) -> int:
    return lo + int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big") % (hi - lo + 1)


def mock_channel_id(n: int) -> str:
    return f"{ID_PREFIX}ch-{n:02d}"


def mock_video_id(channel_n: int, k: int) -> str:
    return f"{ID_PREFIX}v-{channel_n:02d}-{k:02d}"


def _parse_channel(channel_id: str) -> int | None:
    if not channel_id.startswith(f"{ID_PREFIX}ch-"):
        return None
    tail = channel_id.removeprefix(f"{ID_PREFIX}ch-")
    return int(tail) if tail.isdigit() and int(tail) < POOL_SIZE else None


def _parse_video(video_id: str) -> tuple[int, int] | None:
    if not video_id.startswith(f"{ID_PREFIX}v-"):
        return None
    parts = video_id.removeprefix(f"{ID_PREFIX}v-").split("-")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None
    n, k = int(parts[0]), int(parts[1])
    return (n, k) if n < POOL_SIZE and 0 <= k < UPLOADS_PER_CHANNEL else None


def _published(n: int, k: int) -> datetime:
    """Upload k (0 = newest) of channel n; some channels are inactive (large gaps)."""
    gap = _num(f"gap{n}", 2, 21)
    idle = _num(f"idle{n}", 0, 120) if n % 5 == 0 else 0
    return _EPOCH - timedelta(days=idle + k * gap, hours=_num(f"h{n}-{k}", 0, 23))


class MockYouTubeProvider:
    name = PROVIDER
    is_mock = True

    def __init__(self) -> None:
        self.calls: list[str] = []  # endpoint names, for tests

    async def search(self, params: SearchParams) -> SearchPage:
        self.calls.append("search")
        page = int(params.page_token.removeprefix("p")) if params.page_token else 0
        start = _num(params.query.casefold(), 0, POOL_SIZE - 1)
        size = max(1, min(params.max_results, MAX_PAGE_SIZE))
        hits: list[SearchHit] = []
        for i in range(size):
            n = (start + page * size + i) % POOL_SIZE
            if params.search_type == SearchType.CHANNEL:
                hits.append(SearchHit(channel_id=mock_channel_id(n), video_id=None))
            else:
                k = _num(f"{params.query}{page}{i}", 0, UPLOADS_PER_CHANNEL - 1)
                hits.append(SearchHit(channel_id=mock_channel_id(n), video_id=mock_video_id(n, k)))
        token = f"p{page + 1}" if page + 1 < MAX_PAGES else None
        return SearchPage(hits=hits, next_page_token=token)

    async def get_channels(self, channel_ids: list[str]) -> list[ChannelInfo]:
        self.calls.append("channels")
        out = []
        for cid in channel_ids[:MAX_PAGE_SIZE]:
            n = _parse_channel(cid)
            if n is None:
                continue
            subs = _num(f"subs{n}", 800, 900_000)
            hidden = n % 13 == 7
            out.append(
                ChannelInfo(
                    youtube_channel_id=cid,
                    title=f"[DEMO] Mock channel {n:02d}",
                    description="Synthetic channel returned by the offline YouTube mock. Not real.",
                    handle=f"@demo-yt-{n:02d}",
                    custom_url=f"@demo-yt-{n:02d}",
                    avatar_url=None,
                    country=("RU", "US", "GB", "KZ", None)[n % 5],
                    default_language=("ru", "en", "en", "ru", None)[n % 5],
                    published_at=_EPOCH - timedelta(days=_num(f"age{n}", 200, 4000)),
                    uploads_playlist_id=f"{ID_PREFIX}uploads-{n:02d}",
                    subscriber_count=None if hidden else subs,
                    subscribers_hidden=hidden,
                    view_count=subs * _num(f"vpc{n}", 20, 300),
                    video_count=UPLOADS_PER_CHANNEL + _num(f"extra{n}", 0, 400),
                    topic_categories=[],
                    keywords=["demo"],
                    raw={"demo": True},
                )
            )
        return out

    async def get_playlist_video_ids(self, playlist_id: str, max_results: int) -> list[str]:
        self.calls.append("playlistItems")
        tail = playlist_id.removeprefix(f"{ID_PREFIX}uploads-")
        if not tail.isdigit():
            return []
        n = int(tail)
        return [mock_video_id(n, k) for k in range(min(max_results, MAX_PAGE_SIZE, UPLOADS_PER_CHANNEL))]

    async def get_videos(self, video_ids: list[str]) -> list[VideoInfo]:
        self.calls.append("videos")
        out = []
        for vid in video_ids[:MAX_PAGE_SIZE]:
            parsed = _parse_video(vid)
            if parsed is None:
                continue
            n, k = parsed
            base = _num(f"subs{n}", 800, 900_000)
            views = max(50, base * _num(f"vv{n}-{k}", 2, 160) // 100)
            out.append(
                VideoInfo(
                    youtube_video_id=vid,
                    youtube_channel_id=mock_channel_id(n),
                    title=f"[DEMO] Mock video {k:02d} of channel {n:02d}",
                    description="Synthetic video returned by the offline YouTube mock.",
                    published_at=_published(n, k),
                    duration_seconds=_num(f"dur{n}-{k}", 120, 2400),
                    category_id="22",
                    tags=["demo"],
                    view_count=views,
                    like_count=views * _num(f"l{n}-{k}", 1, 6) // 100,
                    comment_count=views * _num(f"c{n}-{k}", 1, 20) // 1000,
                    thumbnail_url=f"https://{THUMBNAIL_HOST}/vi/{vid}/hqdefault.jpg",
                    raw={"demo": True},
                )
            )
        return out
