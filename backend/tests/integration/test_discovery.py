"""Discovery pipeline against the offline mock provider (inside the per-test transaction)."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import factories as f
import pytest
from conftest import csrf_headers, login
from sqlalchemy import func, select

from app.core.errors import IntegrationError, IntegrationErrorCode
from app.domains.discovery.models import YouTubeQuotaLedger
from app.domains.discovery.quota import SessionQuota, get_usage
from app.domains.discovery.service import estimate_quota, run_query
from app.domains.media.models import FetchStatus, Thumbnail
from app.domains.projects.models import (
    ChannelDiscovery,
    DiscoveryMethod,
    ProjectChannel,
    QueryStatus,
    SearchQuery,
    SearchType,
)
from app.domains.youtube.models import Channel, ChannelMetrics, ChannelStatsSnapshot, Video
from app.providers.youtube import LIST_COST, SEARCH_COST, MockYouTubeProvider
from app.providers.youtube.mock import POOL_SIZE

NOW = datetime(2026, 9, 2, tzinfo=UTC)


async def _query(db, project, text, search_type=SearchType.VIDEO, node_id=None) -> SearchQuery:
    q = SearchQuery(
        project_id=project.id,
        text=text,
        text_normalized=text.casefold(),
        search_type=search_type,
        taxonomy_node_id=node_id,
    )
    db.add(q)
    await db.flush()
    return q


async def _count(db, model, *where) -> int:
    return await db.scalar(select(func.count()).select_from(model).where(*where)) or 0


@pytest.fixture
async def project(db, owner):
    p = await f.project(db, owner[1].id, "Finance")
    p.results_per_query, p.search_depth, p.videos_to_analyze = 10, 2, 5
    await db.flush()
    return p


async def test_query_is_ingested_with_provenance_and_quota(db, settings, project):
    provider, quota = MockYouTubeProvider(), SessionQuota(db, "youtube_mock", 10_000)
    q = await _query(db, project, "etf")
    out = await run_query(db, provider, quota, settings, project, q, now=NOW)

    assert out.hits == 10 and out.channels_found == 10 and out.channels_new == 10
    assert out.channels_new_in_project == 10 and out.channels_fetched == 10
    assert q.status == QueryStatus.DONE and q.results_count == 10 and q.last_run_at == NOW
    channels = list(await db.scalars(select(Channel)))
    assert len(channels) == 10 and all(
        c.is_demo and c.youtube_channel_id.startswith("demo-yt-") for c in channels
    )
    assert await _count(db, ChannelStatsSnapshot) == 10
    videos = await _count(db, Video)
    assert videos >= 50  # 5 uploads per channel + source videos that are older than the newest 5

    # quota: 1 search page (10 ≤ 50) + 1 channels.list + 10 playlistItems + videos.list per 50 ids
    assert provider.calls.count("search") == 1
    assert provider.calls.count("videos") == math.ceil(videos / 50)
    assert quota.spent == SEARCH_COST + LIST_COST + 10 * LIST_COST + math.ceil(videos / 50) * LIST_COST
    usage = await get_usage(db, "youtube_mock", 10_000)
    assert usage.used == quota.spent
    assert await _count(db, Thumbnail, Thumbnail.fetch_status == FetchStatus.PENDING) == videos
    assert await _count(db, ChannelMetrics) == 10

    d = (await db.scalars(select(ChannelDiscovery))).all()
    assert len(d) == 10 and {x.method for x in d} == {DiscoveryMethod.KEYWORD_SEARCH}
    assert all(x.search_query_id == q.id and x.source_video_id is not None for x in d)


async def test_same_channels_from_other_queries_and_projects_stay_one_row(db, settings, owner, project):
    provider, quota = MockYouTubeProvider(), SessionQuota(db, "youtube_mock", 10_000)
    q1, q2 = await _query(db, project, "etf"), await _query(db, project, "dividends")
    other = await f.project(db, owner[1].id, "Second")
    other.results_per_query = POOL_SIZE  # the whole pool → overlaps with everything
    q3 = await _query(db, other, "anything", search_type=SearchType.CHANNEL)

    await run_query(db, provider, quota, settings, project, q1, now=NOW)
    second = await run_query(db, provider, quota, settings, project, q2, now=NOW + timedelta(minutes=1))
    third = await run_query(db, provider, quota, settings, other, q3, now=NOW + timedelta(minutes=2))

    assert await _count(db, Channel) == POOL_SIZE  # the mock pool: never duplicated
    assert third.channels_found == POOL_SIZE and third.channels_new_in_project == POOL_SIZE
    # channels fetched by q1/q2 minutes ago are fresh → not re-fetched (quota saved)
    assert third.channels_fresh_skipped == await _count(
        db, ProjectChannel, ProjectChannel.project_id == project.id
    )
    assert third.channels_fetched == POOL_SIZE - third.channels_fresh_skipped
    by_query = {
        qid: set(
            await db.scalars(
                select(ChannelDiscovery.channel_id).where(ChannelDiscovery.search_query_id == qid)
            )
        )
        for qid in (q1.id, q2.id)
    }
    overlap = by_query[q1.id] & by_query[q2.id]
    assert second.channels_found == 10 and second.channels_new == 10 - len(overlap)

    # a channel found by both projects remembers both, with method and query
    shared = await db.scalar(
        select(ProjectChannel.channel_id).where(ProjectChannel.project_id == project.id).limit(1)
    )
    sources = (
        await db.execute(
            select(ChannelDiscovery.project_id, ChannelDiscovery.method).where(
                ChannelDiscovery.channel_id == shared
            )
        )
    ).all()
    assert {s.project_id for s in sources} == {project.id, other.id}
    assert {s.method for s in sources} == {DiscoveryMethod.KEYWORD_SEARCH, DiscoveryMethod.CHANNEL_SEARCH}

    # re-running a query does not duplicate provenance, but counts the rediscovery
    before = await _count(db, ChannelDiscovery)
    counts_before = dict(
        (
            await db.execute(
                select(ProjectChannel.channel_id, ProjectChannel.discovery_count).where(
                    ProjectChannel.project_id == project.id
                )
            )
        )
        .tuples()
        .all()
    )
    await run_query(db, provider, quota, settings, project, q1, now=NOW + timedelta(minutes=3))
    assert await _count(db, ChannelDiscovery) == before
    counts_after = dict(
        (
            await db.execute(
                select(ProjectChannel.channel_id, ProjectChannel.discovery_count).where(
                    ProjectChannel.project_id == project.id
                )
            )
        )
        .tuples()
        .all()
    )
    assert sum(counts_after.values()) == sum(counts_before.values()) + 10


async def test_quota_is_checked_before_each_call(db, settings, project):
    provider = MockYouTubeProvider()
    quota = SessionQuota(
        db, "youtube_mock", SEARCH_COST + 2
    )  # search + channels.list + 1 playlist, then stop
    q = await _query(db, project, "etf")
    with pytest.raises(IntegrationError) as info:
        await run_query(db, provider, quota, settings, project, q, now=NOW)
    assert info.value.code == IntegrationErrorCode.QUOTA_EXCEEDED and not info.value.retryable
    assert "midnight Pacific Time" in info.value.human_message
    assert provider.calls == ["search", "channels", "playlistItems"]  # the 4th call was never made
    ledger = await db.scalar(select(YouTubeQuotaLedger))
    assert ledger.units_used == SEARCH_COST + 2 and ledger.units_limit == SEARCH_COST + 2


def test_estimate_quota(project):
    est = estimate_quota(3, project)  # 10 results → 1 page per query
    assert est.search_units == 3 * SEARCH_COST
    assert est.max_total_units == 3 * SEARCH_COST + 3 * (1 + 10 + 2)  # channels + playlists + ceil(60/50)


# --- API ---------------------------------------------------------------------------------------
@pytest.fixture
def mock_youtube(settings, monkeypatch):
    monkeypatch.setattr(settings, "youtube_provider", "mock")
    return settings


async def _authed(client, settings):
    assert (await login(client, "owner@example.com")).status_code == 200
    return csrf_headers(client, settings)


async def test_start_discovery_api(client, db, settings, mock_youtube, project):
    h = await _authed(client, settings)
    await client.post(f"/api/projects/{project.id}/queries/bulk", headers=h, json={"text": "etf\nbonds"})
    r = await client.post(f"/api/projects/{project.id}/discovery", headers=h, json={})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["created"] and body["queries"] == 2 and body["job"]["type"] == "discovery"
    assert body["quota_search_units"] == 2 * SEARCH_COST
    assert (
        body["quota"]["provider"] == "youtube_mock"
        and body["quota"]["remaining"] == settings.youtube_daily_quota
    )

    again = (await client.post(f"/api/projects/{project.id}/discovery", headers=h, json={})).json()
    assert again["created"] is False and again["job"]["id"] == body["job"]["id"]  # deduplicated active job

    jobs = (await client.get("/api/jobs", params={"project_id": project.id})).json()
    assert jobs["total"] == 1 and jobs["items"][0]["id"] == body["job"]["id"]
    assert (await client.get("/api/jobs", params={"project_id": project.id + 999})).json()["total"] == 0

    r = await client.post(f"/api/projects/{project.id}/discovery", headers=h, json={"query_ids": [999999]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_queries"
    quota = (await client.get("/api/youtube/quota")).json()
    assert quota["mode"] == "mock" and quota["used"] == 0


async def test_start_discovery_requires_configuration_and_active_project(
    client, db, settings, monkeypatch, project
):
    monkeypatch.setattr(settings, "youtube_provider", None)
    monkeypatch.setattr(settings, "youtube_api_key", None)
    monkeypatch.setattr(settings, "demo_mode", False)
    h = await _authed(client, settings)
    r = await client.post(f"/api/projects/{project.id}/discovery", headers=h, json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "youtube_not_configured"
    assert (await client.get("/api/youtube/quota")).json()["provider"] is None

    monkeypatch.setattr(settings, "youtube_provider", "mock")
    await client.patch(f"/api/projects/{project.id}", headers=h, json={"status": "archived"})
    r = await client.post(f"/api/projects/{project.id}/discovery", headers=h, json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "project_archived"


async def test_taxonomy_niches_and_query_tags(client, db, settings, project):
    h = await _authed(client, settings)
    r = await client.post("/api/taxonomy/bulk", headers=h, json={"text": "Finance\nGaming\n finance \n"})
    res = r.json()
    assert r.status_code == 200 and res["created"] == 2 and res["duplicates"] == 1
    finance = next(n for n in res["items"] if n["name"] == "Finance")
    assert finance["level"] == "niche" and finance["slug"] == "finance"
    topic = (
        await client.post(
            "/api/taxonomy/bulk", headers=h, json={"parent_id": finance["id"], "text": "Investing"}
        )
    ).json()["items"][0]
    sub = (
        await client.post("/api/taxonomy/bulk", headers=h, json={"parent_id": topic["id"], "text": "ETF"})
    ).json()["items"][0]
    assert (topic["level"], sub["level"]) == ("topic", "subtopic")
    r = await client.post("/api/taxonomy/bulk", headers=h, json={"parent_id": sub["id"], "text": "too deep"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "too_deep"
    again = (await client.post("/api/taxonomy/bulk", headers=h, json={"text": "Finance"})).json()
    assert again["created"] == 0 and again["items"][0]["id"] == finance["id"]  # existing node returned
    assert len((await client.get("/api/taxonomy")).json()) == 4

    r = await client.put(
        f"/api/projects/{project.id}/niches",
        headers=h,
        json={"taxonomy_node_ids": [finance["id"], topic["id"], finance["id"]]},
    )
    assert r.status_code == 200 and [n["name"] for n in r.json()] == ["Finance", "Investing"]
    assert (
        await client.put(
            f"/api/projects/{project.id}/niches", headers=h, json={"taxonomy_node_ids": [999999]}
        )
    ).status_code == 404

    imported = (
        await client.post(
            f"/api/projects/{project.id}/queries/bulk",
            headers=h,
            json={"text": "etf for beginners", "taxonomy_node_id": sub["id"], "search_type": "channel"},
        )
    ).json()
    q = imported["items"][0]
    assert q["taxonomy_node_id"] == sub["id"] and q["search_type"] == "channel"
    r = await client.patch(
        f"/api/projects/{project.id}/queries/{q['id']}",
        headers=h,
        json={"taxonomy_node_id": None, "search_type": "video"},
    )
    assert (
        r.status_code == 200 and r.json()["taxonomy_node_id"] is None and r.json()["search_type"] == "video"
    )
    r = await client.post(
        f"/api/projects/{project.id}/queries/bulk", headers=h, json={"text": "x", "taxonomy_node_id": 999999}
    )
    assert r.status_code == 404


async def test_channel_filters_niches_metrics_and_detail(client, db, settings, owner, project):
    """Niche include/exclude (with descendants), metric filters, sorting and provenance in the detail."""
    from app.domains.projects.models import TaxonomyLevel, TaxonomyNode

    finance = TaxonomyNode(level=TaxonomyLevel.NICHE, name="Finance", slug="finance")
    gaming = TaxonomyNode(level=TaxonomyLevel.NICHE, name="Gaming", slug="gaming")
    db.add_all([finance, gaming])
    await db.flush()
    etf = TaxonomyNode(parent_id=finance.id, level=TaxonomyLevel.TOPIC, name="ETF", slug="etf")
    db.add(etf)
    await db.flush()

    provider, quota = MockYouTubeProvider(), SessionQuota(db, "youtube_mock", 10_000)
    q_fin = await _query(db, project, "etf", node_id=etf.id)  # tagged with a *topic* under Finance
    q_game = await _query(db, project, "minecraft", node_id=gaming.id)
    # metrics as of NOW (mock uploads are dated relative to 2026-09-01); recency filters use the DB clock
    await run_query(db, provider, quota, settings, project, q_fin, now=NOW)
    await run_query(db, provider, quota, settings, project, q_game, now=NOW)
    now = datetime.now(UTC)

    fin_ids = set(
        await db.scalars(
            select(ChannelDiscovery.channel_id).where(ChannelDiscovery.search_query_id == q_fin.id)
        )
    )
    game_ids = set(
        await db.scalars(
            select(ChannelDiscovery.channel_id).where(ChannelDiscovery.search_query_id == q_game.id)
        )
    )
    await _authed(client, settings)

    async def ids(**params) -> set[int]:
        r = await client.get("/api/channels", params={"limit": 500, **params})
        assert r.status_code == 200, r.text
        return {c["id"] for c in r.json()["items"]}

    assert await ids(niche=finance.id) == fin_ids  # Finance includes its ETF topic
    assert await ids(niche=etf.id) == fin_ids
    assert await ids(niche=finance.id, exclude_niche=gaming.id) == fin_ids - game_ids
    assert await ids(niche=[finance.id, gaming.id]) == fin_ids | game_ids

    everything = (await client.get("/api/channels", params={"limit": 500, "sort": "avg_views"})).json()[
        "items"
    ]
    assert all(c["metrics"] is not None for c in everything)
    avg = [c["metrics"]["avg_views"] for c in everything]
    assert avg == sorted(avg, reverse=True)
    threshold = sorted(avg)[len(avg) // 2]
    assert await ids(min_avg_views=threshold) == {
        c["id"] for c in everything if c["metrics"]["avg_views"] >= threshold
    }
    recent = {
        c["id"]
        for c in everything
        if c["metrics"]["last_video_at"]
        and datetime.fromisoformat(c["metrics"]["last_video_at"]) >= now - timedelta(days=60)
    }
    assert await ids(max_days_since_last_upload=60) == recent
    active = {c["id"] for c in everything if c["metrics"]["videos_30d"] >= 2}
    assert 0 < len(active) < len(everything)  # the mock mixes active and idle channels
    assert await ids(min_videos_30d=2) == active
    assert await ids(language="RU") == {c["id"] for c in everything if c["default_language"] == "ru"}
    assert (await client.get("/api/channels", params={"min_upload_consistency": 2})).status_code == 422

    detail = (await client.get(f"/api/channels/{next(iter(fin_ids))}")).json()
    assert detail["metrics"]["window_videos"] >= 1 and detail["discoveries_total"] >= 1
    d = next(x for x in detail["discoveries"] if x["query_id"] == q_fin.id)
    assert d["query_text"] == "etf" and d["method"] == "keyword_search" and d["project"]["name"] == "Finance"
    assert d["niche"] == {"id": etf.id, "name": "ETF", "level": "topic"}
    assert {"id": etf.id, "name": "ETF", "level": "topic"} in detail["niches"]


async def test_project_filter_settings_are_validated(client, owner, settings):
    h = await _authed(client, settings)
    ok = await client.post(
        "/api/projects",
        headers=h,
        json={
            "name": "F",
            "filter_settings": {
                "min_subscribers": 5000,
                "max_subscribers": 200000,
                "min_avg_views": 10000,
                "max_days_since_last_upload": 14,
                "min_videos_30d": 2,
                "niche": [1],
            },
        },
    )
    assert ok.status_code == 201 and ok.json()["filter_settings"]["min_avg_views"] == 10000
    bad = await client.post("/api/projects", headers=h, json={"name": "G", "filter_settings": {"bogus": 1}})
    assert bad.status_code == 422
