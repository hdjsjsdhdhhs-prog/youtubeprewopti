"""Demo seed (Phase 1, item 9): labelling, idempotency, workspace visibility, removal/reset."""

from __future__ import annotations

import factories as f
import pytest
from conftest import csrf_headers, login
from sqlalchemy import func, select

from app.core.errors import ConflictError
from app.domains.demo.service import (
    DEMO_ID_PREFIX,
    DEMO_THUMBNAIL_HOST,
    DEMO_TITLE_PREFIX,
    VIDEOS_PER_CHANNEL,
    purge_storage_files,
    remove_demo,
    seed_demo,
)
from app.domains.identity.models import AuditLog
from app.domains.media.models import FetchStatus, ImageAsset, ImageSource, Thumbnail
from app.domains.media.service import asset_visible_to_workspace, store_image
from app.domains.projects.models import ChannelDiscovery, DiscoveryMethod, SearchProject, SearchQuery
from app.domains.youtube.models import Channel, Video
from app.domains.youtube.schemas import ChannelFilterSet, ChannelSort, SortOrder
from app.domains.youtube.service import list_channels
from app.providers.thumbnails.mock import mock_thumbnail_bytes

EXPECTED_PROJECTS = 2
EXPECTED_CHANNELS = 11  # 6 + 6, one channel shared by both projects
EXPECTED_VIDEOS = EXPECTED_CHANNELS * VIDEOS_PER_CHANNEL


async def _count(db, stmt) -> int:
    return await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0


async def _visible_channels(db, workspace_id: int) -> list:
    items, _ = await list_channels(
        db, workspace_id, project_id=None, q=None, filters=ChannelFilterSet(),
        sort=ChannelSort.TITLE, order=SortOrder.ASC, limit=500, offset=0,
    )
    return items


async def test_seed_creates_clearly_labelled_data(db, owner, storage):
    _, ws = owner
    result = await seed_demo(db, storage, ws.id)

    assert (result.projects_created, result.channels_created) == (EXPECTED_PROJECTS, EXPECTED_CHANNELS)
    assert result.videos_created == result.thumbnails_stored == EXPECTED_VIDEOS

    projects = list(await db.scalars(select(SearchProject).where(SearchProject.workspace_id == ws.id)))
    assert len(projects) == EXPECTED_PROJECTS
    assert all(p.is_demo and p.name.startswith(DEMO_TITLE_PREFIX) for p in projects)
    project_ids = [p.id for p in projects]
    assert await _count(db, select(SearchQuery).where(SearchQuery.project_id.in_(project_ids))) == 6

    channels = list(
        await db.scalars(select(Channel).where(Channel.youtube_channel_id.startswith(DEMO_ID_PREFIX)))
    )
    assert len(channels) == EXPECTED_CHANNELS
    for c in channels:
        assert c.is_demo and c.title.startswith(DEMO_TITLE_PREFIX) and c.raw == {"demo": True}

    videos = list(await db.scalars(select(Video).where(Video.channel_id.in_([c.id for c in channels]))))
    assert len(videos) == EXPECTED_VIDEOS
    assert all(v.youtube_video_id.startswith(DEMO_ID_PREFIX) and v.raw == {"demo": True} for v in videos)

    rows = (
        await db.execute(
            select(Thumbnail, ImageAsset)
            .join(ImageAsset, ImageAsset.id == Thumbnail.image_asset_id)
            .where(Thumbnail.video_id.in_([v.id for v in videos]))
        )
    ).all()
    assert len(rows) == EXPECTED_VIDEOS
    for thumb, asset in rows:
        assert thumb.fetch_status == FetchStatus.OK
        assert DEMO_THUMBNAIL_HOST in thumb.original_url and thumb.original_url.endswith(".jpg")
        assert asset.source == ImageSource.DEMO
        assert storage.exists(asset.storage_key)
    assert len({asset.sha256 for _, asset in rows}) == EXPECTED_VIDEOS  # every video has its own image

    methods = set(
        await db.scalars(select(ChannelDiscovery.method).where(ChannelDiscovery.project_id.in_(project_ids)))
    )
    assert methods == {DiscoveryMethod.DEMO}
    assert await db.scalar(select(AuditLog.id).where(AuditLog.action == "demo.seeded",
                                                     AuditLog.workspace_id == ws.id))


async def test_seed_is_idempotent(db, owner, storage):
    _, ws = owner
    await seed_demo(db, storage, ws.id)
    models = (SearchProject, SearchQuery, Channel, Video, Thumbnail, ImageAsset, ChannelDiscovery)
    counts = [await _count(db, select(m)) for m in models]

    again = await seed_demo(db, storage, ws.id)

    assert not again.created_anything
    assert [await _count(db, select(m)) for m in models] == counts
    seeded_audits = select(AuditLog).where(AuditLog.action == "demo.seeded", AuditLog.workspace_id == ws.id)
    assert await _count(db, seeded_audits) == 1


async def test_demo_data_is_visible_only_to_the_seeded_workspace(db, owner, other_owner, storage):
    _, ws = owner
    _, other_ws = other_owner
    await seed_demo(db, storage, ws.id)

    visible = await _visible_channels(db, ws.id)
    assert len(visible) == EXPECTED_CHANNELS and all(c.is_demo for c in visible)
    assert await _visible_channels(db, other_ws.id) == []

    asset_id = await db.scalar(
        select(Thumbnail.image_asset_id)
        .join(Video, Video.id == Thumbnail.video_id)
        .where(Video.youtube_video_id.startswith(DEMO_ID_PREFIX))
        .limit(1)
    )
    assert await asset_visible_to_workspace(db, asset_id, ws.id)
    assert not await asset_visible_to_workspace(db, asset_id, other_ws.id)


async def test_demo_thumbnails_are_served_through_the_api(db, owner, other_owner, storage, client, settings):
    user, ws = owner
    await seed_demo(db, storage, ws.id)
    channel = next(c for c in await _visible_channels(db, ws.id))
    assert (await login(client, user.email)).status_code == 200

    videos = (await client.get(f"/api/channels/{channel.id}/videos")).json()
    assert videos["total"] == VIDEOS_PER_CHANNEL
    thumb = videos["items"][0]["thumbnail"]
    assert thumb["fetch_status"] == "ok" and thumb["image_url"]

    image = await client.get(thumb["image_url"])
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"

    assert (await client.post("/api/auth/logout", headers=csrf_headers(client, settings))).status_code < 300
    other_user, _ = other_owner
    assert (await login(client, other_user.email)).status_code == 200
    assert (await client.get(thumb["image_url"])).status_code == 404
    assert (await client.get(f"/api/channels/{channel.id}")).status_code == 404


async def test_second_workspace_reuses_global_demo_channels(db, owner, other_owner, storage):
    _, ws = owner
    _, other_ws = other_owner
    await seed_demo(db, storage, ws.id)

    second = await seed_demo(db, storage, other_ws.id)
    assert second.projects_created == EXPECTED_PROJECTS
    assert second.channels_created == second.videos_created == 0
    assert len(await _visible_channels(db, other_ws.id)) == EXPECTED_CHANNELS

    # Removing demo data from one workspace keeps channels the other workspace still uses.
    removed = await remove_demo(db, storage.name, ws.id)
    assert removed.projects_deleted == EXPECTED_PROJECTS and removed.channels_deleted == 0
    assert await _visible_channels(db, ws.id) == []
    assert len(await _visible_channels(db, other_ws.id)) == EXPECTED_CHANNELS


async def test_remove_deletes_only_demo_data(db, owner, storage):
    _, ws = owner
    # Real data, including a real channel whose thumbnail came from the mock fetcher (ImageSource.DEMO).
    real_project = await f.project(db, ws.id, name="Real project")
    real_channel = await f.channel(db, projects=[real_project])
    mock_bytes = mock_thumbnail_bytes("https://i.ytimg.com/x")
    mock_asset = await store_image(db, storage, mock_bytes, ImageSource.DEMO)
    real_video = await f.video(db, real_channel, asset=mock_asset)

    await seed_demo(db, storage, ws.id)
    demo_keys = list(await db.scalars(select(ImageAsset.storage_key).where(
        ImageAsset.source == ImageSource.DEMO, ImageAsset.id != mock_asset.id)))
    assert len(demo_keys) == EXPECTED_VIDEOS

    removed = await remove_demo(db, storage.name, ws.id)
    assert (removed.projects_deleted, removed.channels_deleted, removed.images_deleted) == (
        EXPECTED_PROJECTS, EXPECTED_CHANNELS, EXPECTED_VIDEOS)
    assert sorted(removed.storage_keys) == sorted(demo_keys)

    assert await _count(db, select(Channel).where(Channel.is_demo.is_(True))) == 0
    assert await _count(db, select(Video).where(Video.youtube_video_id.startswith(DEMO_ID_PREFIX))) == 0
    assert await _count(db, select(SearchProject).where(SearchProject.is_demo.is_(True))) == 0
    # Real rows untouched.
    assert await db.get(SearchProject, real_project.id) is not None
    assert await db.scalar(select(Video.id).where(Video.id == real_video.id)) == real_video.id
    assert await db.scalar(select(ImageAsset.id).where(ImageAsset.id == mock_asset.id)) == mock_asset.id

    assert await purge_storage_files(db, storage, removed.storage_keys) == EXPECTED_VIDEOS
    assert not any(storage.exists(k) for k in demo_keys)
    assert storage.exists(mock_asset.storage_key)
    assert await db.scalar(select(AuditLog.id).where(AuditLog.action == "demo.removed"))


async def test_reset_keeps_files_of_reseeded_assets(db, owner, storage):
    _, ws = owner
    await seed_demo(db, storage, ws.id)

    removed = await remove_demo(db, storage.name, ws.id)
    reseeded = await seed_demo(db, storage, ws.id)
    assert reseeded.videos_created == EXPECTED_VIDEOS

    # Same deterministic bytes => same content-addressed keys; files must survive the purge.
    assert await purge_storage_files(db, storage, removed.storage_keys) == 0
    keys = list(await db.scalars(select(ImageAsset.storage_key).where(ImageAsset.source == ImageSource.DEMO)))
    assert len(keys) == EXPECTED_VIDEOS and all(storage.exists(k) for k in keys)


async def test_seed_refuses_to_take_over_a_real_project_with_a_demo_name(db, owner, storage):
    _, ws = owner
    await f.project(db, ws.id, name="[DEMO] Технообзоры")
    with pytest.raises(ConflictError) as exc:
        await seed_demo(db, storage, ws.id)
    assert exc.value.code == "demo_name_taken"
