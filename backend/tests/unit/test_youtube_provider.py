"""YouTube Data API adapter: request shape, parsing, error classification (respx, no network)."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.core.errors import IntegrationError, IntegrationErrorCode
from app.providers.youtube import SearchParams, SearchType, YouTubeDataApiProvider
from app.providers.youtube.http import BASE_URL, parse_duration, parse_keywords

KEY = "test-key-not-real"


@pytest.fixture
async def provider():
    async with httpx.AsyncClient() as client:
        yield YouTubeDataApiProvider(client, KEY)


def _err(status: int, reason: str) -> httpx.Response:
    return httpx.Response(
        status, json={"error": {"code": status, "message": "x", "errors": [{"reason": reason}]}}
    )


@pytest.mark.parametrize(
    ("value", "seconds"),
    [
        ("PT1H2M3S", 3723),
        ("PT45S", 45),
        ("PT10M", 600),
        ("P1DT2H", 93600),
        ("P0D", 0),
        ("bogus", None),
        (None, None),
    ],
)
def test_parse_duration(value, seconds):
    assert parse_duration(value) == seconds


def test_parse_keywords_handles_quotes_and_duplicates():
    assert parse_keywords('finance "personal finance" ETF finance') == ["finance", "personal finance", "ETF"]
    assert parse_keywords(None) == []


@respx.mock
async def test_search_request_and_parsing(provider):
    route = respx.get(f"{BASE_URL}/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "nextPageToken": "T2",
                "items": [
                    {"id": {"kind": "youtube#video", "videoId": "v1"}, "snippet": {"channelId": "UC1"}},
                    {"id": {"kind": "youtube#video", "videoId": "v2"}, "snippet": {"channelId": "UC2"}},
                    {"id": {"kind": "youtube#video"}, "snippet": {}},  # malformed → skipped
                ],
            },
        )
    )
    page = await provider.search(
        SearchParams(
            query="ETF обзор",
            search_type=SearchType.VIDEO,
            max_results=80,
            language="ru",
            region_code="RU",
            published_after=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    assert [(h.channel_id, h.video_id) for h in page.hits] == [("UC1", "v1"), ("UC2", "v2")]
    assert page.next_page_token == "T2"
    params = route.calls.last.request.url.params
    assert params["q"] == "ETF обзор" and params["type"] == "video" and params["maxResults"] == "50"
    assert params["relevanceLanguage"] == "ru" and params["regionCode"] == "RU"
    assert params["publishedAfter"] == "2026-01-01T00:00:00Z" and params["key"] == KEY
    assert "pageToken" not in params


@respx.mock
async def test_channel_search_ignores_published_after(provider):
    route = respx.get(f"{BASE_URL}/search").mock(
        return_value=httpx.Response(
            200, json={"items": [{"id": {"kind": "youtube#channel", "channelId": "UC9"}}]}
        )
    )
    page = await provider.search(
        SearchParams(
            query="finance",
            search_type=SearchType.CHANNEL,
            max_results=5,
            published_after=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    assert [(h.channel_id, h.video_id) for h in page.hits] == [("UC9", None)] and page.next_page_token is None
    assert "publishedAfter" not in route.calls.last.request.url.params


@respx.mock
async def test_channels_parsing(provider):
    respx.get(f"{BASE_URL}/channels").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "UC1",
                        "snippet": {
                            "title": "Fin",
                            "description": "d",
                            "customUrl": "@fin",
                            "publishedAt": "2015-03-01T10:00:00Z",
                            "country": "ru",
                            "defaultLanguage": "ru",
                            "thumbnails": {
                                "default": {"url": "https://yt3.ggpht.com/a"},
                                "high": {"url": "https://yt3.ggpht.com/h"},
                            },
                        },
                        "statistics": {
                            "viewCount": "1000",
                            "subscriberCount": "50",
                            "hiddenSubscriberCount": False,
                            "videoCount": "12",
                        },
                        "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}},
                        "topicDetails": {"topicCategories": ["https://en.wikipedia.org/wiki/Finance"]},
                        "brandingSettings": {"channel": {"keywords": 'money "personal finance"'}},
                    },
                    {
                        "id": "UC2",
                        "snippet": {"title": "Hidden", "customUrl": "legacyname"},
                        "statistics": {"subscriberCount": "0", "hiddenSubscriberCount": True},
                    },
                ]
            },
        )
    )
    a, b = await provider.get_channels(["UC1", "UC2"])
    assert (a.handle, a.country, a.subscriber_count, a.view_count, a.video_count) == (
        "@fin",
        "RU",
        50,
        1000,
        12,
    )
    assert a.uploads_playlist_id == "UU1" and a.avatar_url == "https://yt3.ggpht.com/h"
    assert a.keywords == ["money", "personal finance"] and a.published_at == datetime(
        2015, 3, 1, 10, tzinfo=UTC
    )
    assert (b.handle, b.custom_url, b.subscriber_count, b.subscribers_hidden) == (
        None,
        "legacyname",
        None,
        True,
    )


@respx.mock
async def test_playlist_not_found_is_empty_and_videos_parsing(provider):
    respx.get(f"{BASE_URL}/playlistItems").mock(return_value=_err(404, "playlistNotFound"))
    assert await provider.get_playlist_video_ids("UUx", 12) == []
    respx.get(f"{BASE_URL}/videos").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "v1",
                        "snippet": {
                            "channelId": "UC1",
                            "title": "T",
                            "publishedAt": "2026-09-01T00:00:00Z",
                            "tags": ["a"],
                            "categoryId": "27",
                            "thumbnails": {
                                "high": {"url": "https://i.ytimg.com/vi/v1/hqdefault.jpg"},
                                "maxres": {"url": "https://i.ytimg.com/vi/v1/maxresdefault.jpg"},
                            },
                        },
                        "contentDetails": {"duration": "PT12M5S"},
                        "statistics": {"viewCount": "77", "likeCount": "5"},
                    }
                ]
            },
        )
    )
    (v,) = await provider.get_videos(["v1"])
    assert (v.duration_seconds, v.view_count, v.like_count, v.comment_count) == (725, 77, 5, None)
    assert v.thumbnail_url.endswith("maxresdefault.jpg") and v.tags == ["a"] and v.category_id == "27"


@pytest.mark.parametrize(
    ("response", "code", "retryable"),
    [
        (_err(403, "quotaExceeded"), IntegrationErrorCode.QUOTA_EXCEEDED, False),
        (_err(403, "rateLimitExceeded"), IntegrationErrorCode.RATE_LIMITED, True),
        (_err(400, "keyInvalid"), IntegrationErrorCode.AUTH_EXPIRED, False),
        (_err(403, "accessNotConfigured"), IntegrationErrorCode.FORBIDDEN, False),
        (httpx.Response(503, text="unavailable"), IntegrationErrorCode.PROVIDER_UNAVAILABLE, True),
    ],
)
@respx.mock
async def test_error_classification(provider, response, code, retryable):
    respx.get(f"{BASE_URL}/channels").mock(return_value=response)
    with pytest.raises(IntegrationError) as info:
        await provider.get_channels(["UC1"])
    assert info.value.code == code and info.value.retryable is retryable
    assert KEY not in info.value.human_message


@respx.mock
async def test_timeout_is_retryable(provider):
    respx.get(f"{BASE_URL}/videos").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(IntegrationError) as info:
        await provider.get_videos(["v1"])
    assert info.value.code == IntegrationErrorCode.TIMEOUT and info.value.retryable
