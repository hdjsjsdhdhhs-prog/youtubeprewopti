"""YouTube Data API v3 over httpx (ADR-0008). Public data only, API-key auth."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import httpx

from app.core.errors import IntegrationError, IntegrationErrorCode, classify_http_status
from app.providers.youtube.base import (
    MAX_PAGE_SIZE,
    ChannelInfo,
    SearchHit,
    SearchPage,
    SearchParams,
    SearchType,
    VideoInfo,
)

PROVIDER = "youtube_api"
BASE_URL = "https://www.googleapis.com/youtube/v3"

_QUOTA_REASONS = frozenset({"quotaExceeded", "dailyLimitExceeded"})
_RATE_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded"})
_KEY_REASONS = frozenset({"keyInvalid", "keyExpired", "API_KEY_INVALID"})
_NOT_ENABLED_REASONS = frozenset({"accessNotConfigured", "SERVICE_DISABLED"})
_DURATION = re.compile(
    r"^P(?:(?P<d>\d+)D)?(?:T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+(?:\.\d+)?)S)?)?$"
)
_KEYWORD = re.compile(r'"([^"]+)"|(\S+)')


def parse_duration(value: str | None) -> int | None:
    """ISO 8601 duration of ``contentDetails.duration`` (``PT1H2M3S``, ``P1DT2H``) → seconds."""
    if not value:
        return None
    m = _DURATION.match(value)
    if m is None:
        return None
    d, h, mi, s = (float(m.group(k) or 0) for k in ("d", "h", "m", "s"))
    return int(d * 86400 + h * 3600 + mi * 60 + s)


def parse_keywords(value: str | None) -> list[str]:
    """``brandingSettings.channel.keywords``: space-separated, multi-word keywords in double quotes."""
    if not value:
        return []
    out: list[str] = []
    for quoted, bare in _KEYWORD.findall(value):
        kw = (quoted or bare).strip()
        if kw and kw not in out:
            out.append(kw[:100])
    return out[:50]


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _best_thumbnail(thumbs: dict[str, Any]) -> str | None:
    for key in ("maxres", "standard", "high", "medium", "default"):
        url = (thumbs.get(key) or {}).get("url")
        if url:
            return str(url)
    return None


def _error_reason(body: Any) -> tuple[str | None, str | None]:
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict):
        return None, None
    errors = err.get("errors") or []
    reason = errors[0].get("reason") if errors and isinstance(errors[0], dict) else None
    if reason is None:  # newer Google error format: details[].reason
        for d in err.get("details") or []:
            if isinstance(d, dict) and d.get("reason"):
                reason = d["reason"]
                break
    return reason, err.get("message")


def classify_youtube_error(status: int, body: Any, context: str) -> IntegrationError:
    reason, _ = _error_reason(body)
    if reason in _QUOTA_REASONS:
        return IntegrationError(
            IntegrationErrorCode.QUOTA_EXCEEDED,
            "YouTube API daily quota is exhausted (resets at midnight Pacific Time). "
            "Pending queries stay in the project; start the search again after the reset.",
            retryable=False, provider=PROVIDER, http_status=status,
        )
    if reason in _RATE_REASONS:
        return IntegrationError(
            IntegrationErrorCode.RATE_LIMITED, f"YouTube API rate limit hit while {context}; will retry.",
            retryable=True, provider=PROVIDER, http_status=status,
        )
    if reason in _KEY_REASONS or (status == 400 and reason == "badRequest" and "key" in str(body).lower()):
        return IntegrationError(
            IntegrationErrorCode.AUTH_EXPIRED,
            "YouTube API key is invalid or expired. Update YTL_YOUTUBE_API_KEY.",
            retryable=False, provider=PROVIDER, http_status=status,
        )
    if reason in _NOT_ENABLED_REASONS:
        return IntegrationError(
            IntegrationErrorCode.FORBIDDEN,
            "YouTube Data API v3 is not enabled for the Google Cloud project of this API key.",
            retryable=False, provider=PROVIDER, http_status=status,
        )
    return classify_http_status(PROVIDER, status, context)


class YouTubeDataApiProvider:
    name = PROVIDER
    is_mock = False

    def __init__(self, client: httpx.AsyncClient, api_key: str) -> None:
        self._client = client
        self._key = api_key

    async def _get(self, endpoint: str, params: dict[str, Any], context: str) -> dict[str, Any]:
        query = {k: v for k, v in params.items() if v is not None}
        query["key"] = self._key
        try:
            resp = await self._client.get(f"{BASE_URL}/{endpoint}", params=query)
        except httpx.TimeoutException as exc:
            raise IntegrationError(
                IntegrationErrorCode.TIMEOUT, f"YouTube API timed out while {context}; will retry.",
                retryable=True, provider=PROVIDER,
            ) from exc
        except httpx.TransportError as exc:
            raise IntegrationError(
                IntegrationErrorCode.PROVIDER_UNAVAILABLE,
                f"YouTube API is unreachable ({type(exc).__name__}) while {context}; will retry.",
                retryable=True, provider=PROVIDER,
            ) from exc
        try:
            body = resp.json()
        except ValueError:
            body = None
        if resp.status_code != 200:
            raise classify_youtube_error(resp.status_code, body, context)
        if not isinstance(body, dict):
            raise IntegrationError(
                IntegrationErrorCode.UNKNOWN, f"YouTube API returned a non-JSON body while {context}.",
                retryable=True, provider=PROVIDER, http_status=resp.status_code,
            )
        return body

    async def search(self, params: SearchParams) -> SearchPage:
        body = await self._get(
            "search",
            {
                "part": "snippet",
                "q": params.query,
                "type": params.search_type.value,
                "maxResults": max(1, min(params.max_results, MAX_PAGE_SIZE)),
                "pageToken": params.page_token,
                "relevanceLanguage": params.language,
                "regionCode": params.region_code,
                "publishedAfter": (
                    params.published_after.strftime("%Y-%m-%dT%H:%M:%SZ")
                    if params.published_after and params.search_type == SearchType.VIDEO else None
                ),
                "fields": "nextPageToken,items(id,snippet/channelId)",
            },
            "searching",
        )
        hits: list[SearchHit] = []
        for item in body.get("items") or []:
            ident = item.get("id") or {}
            channel_id = ident.get("channelId") or (item.get("snippet") or {}).get("channelId")
            if not channel_id:
                continue
            hits.append(SearchHit(channel_id=str(channel_id), video_id=ident.get("videoId")))
        return SearchPage(hits=hits, next_page_token=body.get("nextPageToken"))

    async def get_channels(self, channel_ids: list[str]) -> list[ChannelInfo]:
        if not channel_ids:
            return []
        body = await self._get(
            "channels",
            {
                "part": "snippet,statistics,contentDetails,topicDetails,brandingSettings",
                "id": ",".join(channel_ids[:MAX_PAGE_SIZE]),
                "maxResults": MAX_PAGE_SIZE,
            },
            "loading channels",
        )
        return [self._channel(item) for item in body.get("items") or [] if item.get("id")]

    @staticmethod
    def _channel(item: dict[str, Any]) -> ChannelInfo:
        sn = item.get("snippet") or {}
        st = item.get("statistics") or {}
        cd = item.get("contentDetails") or {}
        branding = (item.get("brandingSettings") or {}).get("channel") or {}
        custom = sn.get("customUrl")
        hidden = bool(st.get("hiddenSubscriberCount"))
        country = (sn.get("country") or branding.get("country") or "").upper() or None
        return ChannelInfo(
            youtube_channel_id=str(item["id"]),
            title=(sn.get("title") or "")[:300] or str(item["id"]),
            description=sn.get("description") or "",
            handle=custom if isinstance(custom, str) and custom.startswith("@") else None,
            custom_url=custom[:300] if isinstance(custom, str) else None,
            avatar_url=_best_thumbnail(sn.get("thumbnails") or {}),
            country=country if country and len(country) == 2 else None,
            default_language=(sn.get("defaultLanguage") or branding.get("defaultLanguage") or None),
            published_at=_dt(sn.get("publishedAt")),
            uploads_playlist_id=(cd.get("relatedPlaylists") or {}).get("uploads"),
            subscriber_count=None if hidden else _int(st.get("subscriberCount")),
            subscribers_hidden=hidden,
            view_count=_int(st.get("viewCount")),
            video_count=_int(st.get("videoCount")),
            topic_categories=list((item.get("topicDetails") or {}).get("topicCategories") or [])[:20],
            keywords=parse_keywords(branding.get("keywords")),
            raw=item,
        )

    async def get_playlist_video_ids(self, playlist_id: str, max_results: int) -> list[str]:
        try:
            body = await self._get(
                "playlistItems",
                {
                    "part": "contentDetails",
                    "playlistId": playlist_id,
                    "maxResults": max(1, min(max_results, MAX_PAGE_SIZE)),
                    "fields": "items(contentDetails/videoId)",
                },
                "loading channel uploads",
            )
        except IntegrationError as exc:
            if exc.code == IntegrationErrorCode.NOT_FOUND:
                return []  # channel without public uploads
            raise
        ids = [((i.get("contentDetails") or {}).get("videoId")) for i in body.get("items") or []]
        return [str(v) for v in ids if v]

    async def get_videos(self, video_ids: list[str]) -> list[VideoInfo]:
        if not video_ids:
            return []
        body = await self._get(
            "videos",
            {
                "part": "snippet,contentDetails,statistics",
                "id": ",".join(video_ids[:MAX_PAGE_SIZE]),
                "maxResults": MAX_PAGE_SIZE,
            },
            "loading videos",
        )
        out: list[VideoInfo] = []
        for item in body.get("items") or []:
            sn = item.get("snippet") or {}
            st = item.get("statistics") or {}
            if not item.get("id") or not sn.get("channelId"):
                continue
            out.append(
                VideoInfo(
                    youtube_video_id=str(item["id"]),
                    youtube_channel_id=str(sn["channelId"]),
                    title=(sn.get("title") or "")[:500] or str(item["id"]),
                    description=sn.get("description") or "",
                    published_at=_dt(sn.get("publishedAt")),
                    duration_seconds=parse_duration((item.get("contentDetails") or {}).get("duration")),
                    category_id=sn.get("categoryId"),
                    tags=[str(t)[:100] for t in (sn.get("tags") or [])][:50],
                    view_count=_int(st.get("viewCount")),
                    like_count=_int(st.get("likeCount")),
                    comment_count=_int(st.get("commentCount")),
                    thumbnail_url=_best_thumbnail(sn.get("thumbnails") or {}),
                    raw=item,
                )
            )
        return out
