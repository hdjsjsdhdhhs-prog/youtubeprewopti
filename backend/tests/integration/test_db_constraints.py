"""Database-level invariants (ADR-0014): the schema itself rejects bad data, independent of services.

Each violation runs in its own SAVEPOINT, so the test transaction stays usable afterwards, and the
exact constraint name is asserted (not just "some IntegrityError").
"""

from __future__ import annotations

import factories as f
import pytest
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.core.state_machine import JobStatus
from app.domains.identity.models import User
from app.domains.jobs.models import JobRun
from app.domains.media.models import ImageAsset, ImageSource, Thumbnail
from app.domains.projects.models import (
    ChannelDiscovery,
    DiscoveryMethod,
    ProjectChannel,
    SearchProject,
    SearchQuery,
    TaxonomyLevel,
    TaxonomyNode,
)
from app.domains.youtube.models import Channel, Video


async def _violates(db, stmt, constraint: str) -> None:
    with pytest.raises(IntegrityError) as exc:
        async with db.begin_nested():
            await db.execute(stmt)
    assert exc.value.orig.diag.constraint_name == constraint


async def test_email_is_unique_case_insensitively(db, owner):
    user, _ = owner
    await _violates(
        db,
        insert(User).values(email=user.email.upper(), password_hash="x", display_name="dup"),
        "uq_users_email",
    )


async def test_project_name_unique_per_workspace_only(db, owner, other_owner):
    _, ws = owner
    _, other_ws = other_owner
    await f.project(db, ws.id, name="Same")
    await _violates(
        db,
        insert(SearchProject).values(workspace_id=ws.id, name="Same"),
        "uq_search_projects_workspace_id_name",
    )
    await f.project(db, other_ws.id, name="Same")  # other workspace: allowed


@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [
        ("results_per_query", 0, "ck_search_projects_results_per_query_range"),
        ("results_per_query", 501, "ck_search_projects_results_per_query_range"),
        ("search_depth", 11, "ck_search_projects_search_depth_range"),
        ("videos_to_analyze", 0, "ck_search_projects_videos_to_analyze_range"),
        ("videos_to_analyze", 51, "ck_search_projects_videos_to_analyze_range"),
    ],
)
async def test_project_numeric_ranges(db, owner, column, value, constraint):
    _, ws = owner
    await _violates(
        db, insert(SearchProject).values(workspace_id=ws.id, name="P", **{column: value}), constraint
    )


async def test_query_unique_by_normalized_text_per_project(db, owner):
    _, ws = owner
    p1, p2 = await f.project(db, ws.id), await f.project(db, ws.id)
    db.add(SearchQuery(project_id=p1.id, text="Cooking", text_normalized="cooking"))
    await db.flush()
    await _violates(
        db,
        insert(SearchQuery).values(project_id=p1.id, text="COOKING ", text_normalized="cooking"),
        "uq_search_queries_project_id_text_normalized",
    )
    db.add(SearchQuery(project_id=p2.id, text="Cooking", text_normalized="cooking"))
    await db.flush()


async def test_youtube_ids_are_globally_unique(db, owner):
    ch = await f.channel(db)
    v = await f.video(db, ch)
    await _violates(
        db,
        insert(Channel).values(youtube_channel_id=ch.youtube_channel_id, title="dup"),
        "uq_channels_youtube_channel_id",
    )
    await _violates(
        db,
        insert(Video).values(
            channel_id=ch.id, youtube_video_id=v.youtube_video_id, title="dup", published_at=func.now()
        ),
        "uq_videos_youtube_video_id",
    )


async def test_one_thumbnail_per_video_and_unique_image_sha(db, storage):
    ch = await f.channel(db)
    v = await f.video(db, ch)  # factory already creates its thumbnail
    await _violates(
        db,
        insert(Thumbnail).values(video_id=v.id, original_url="https://i.ytimg.com/x.jpg"),
        "uq_thumbnails_video_id",
    )
    asset = dict(
        sha256="a" * 64,
        phash=1,
        storage_backend="local",
        storage_key="aa/a.png",
        mime="image/png",
        format="png",
        width=1,
        height=1,
        bytes=1,
        source=ImageSource.UPLOAD,
    )
    await db.execute(insert(ImageAsset).values(**asset))
    await _violates(db, insert(ImageAsset).values(**asset), "uq_image_assets_sha256")


async def test_channel_discovery_dedup_treats_nulls_as_equal(db, owner):
    _, ws = owner
    p = await f.project(db, ws.id)
    ch = await f.channel(db, projects=[p])
    row = dict(project_id=p.id, channel_id=ch.id, method=DiscoveryMethod.MANUAL_IMPORT)
    await db.execute(insert(ChannelDiscovery).values(**row))
    # search_query_id / source_video_id are NULL in both rows — still a duplicate (NULLS NOT DISTINCT).
    await _violates(db, insert(ChannelDiscovery).values(**row), "uq_channel_discoveries_source")
    await db.execute(insert(ChannelDiscovery).values(**{**row, "method": DiscoveryMethod.DEMO}))


async def test_taxonomy_root_slugs_are_unique(db):
    root = dict(parent_id=None, level=TaxonomyLevel.NICHE, name="Food", slug="food")
    await db.execute(insert(TaxonomyNode).values(**root))
    await _violates(db, insert(TaxonomyNode).values(**root), "uq_taxonomy_nodes_parent_id_slug")


async def test_job_fingerprint_unique_only_while_active(db, owner):
    _, ws = owner
    job = await f.job_run(db, ws.id, fingerprint="fp-same")
    for status in (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.RETRYING):
        await db.execute(update(JobRun).where(JobRun.id == job.id).values(status=status))
        await _violates(
            db,
            insert(JobRun).values(workspace_id=ws.id, type="t", fingerprint="fp-same", queue="bulk"),
            "uq_job_runs_active_fingerprint",
        )
    await db.execute(update(JobRun).where(JobRun.id == job.id).values(status=JobStatus.COMPLETED))
    await f.job_run(db, ws.id, fingerprint="fp-same")  # previous one finished: allowed
    await db.execute(
        insert(JobRun).values(
            workspace_id=ws.id, type="t", fingerprint="fp-same", queue="bulk", status=JobStatus.FAILED
        )
    )  # any number of finished ones


async def test_deleting_project_keeps_global_channels(db, owner):
    _, ws = owner
    p = await f.project(db, ws.id)
    ch = await f.channel(db, projects=[p])
    db.add(ChannelDiscovery(project_id=p.id, channel_id=ch.id, method=DiscoveryMethod.MANUAL_IMPORT))
    await db.flush()

    await db.execute(delete(SearchProject).where(SearchProject.id == p.id))

    assert (
        await db.scalar(
            select(func.count()).select_from(ProjectChannel).where(ProjectChannel.project_id == p.id)
        )
        == 0
    )
    assert (
        await db.scalar(
            select(func.count()).select_from(ChannelDiscovery).where(ChannelDiscovery.project_id == p.id)
        )
        == 0
    )
    assert await db.scalar(select(Channel.id).where(Channel.id == ch.id)) == ch.id


async def test_deleting_video_cascades_thumbnail_but_keeps_image(db, storage):
    ch = await f.channel(db)
    asset = await db.scalar(
        insert(ImageAsset)
        .values(
            sha256="b" * 64,
            phash=2,
            storage_backend="local",
            storage_key="bb/b.png",
            mime="image/png",
            format="png",
            width=1,
            height=1,
            bytes=1,
            source=ImageSource.UPLOAD,
        )
        .returning(ImageAsset.id)
    )
    v = await f.video(db, ch)
    await db.execute(update(Thumbnail).where(Thumbnail.video_id == v.id).values(image_asset_id=asset))

    await db.execute(delete(Video).where(Video.id == v.id))

    assert await db.scalar(select(Thumbnail.id).where(Thumbnail.video_id == v.id)) is None
    assert await db.scalar(select(ImageAsset.id).where(ImageAsset.id == asset)) == asset
