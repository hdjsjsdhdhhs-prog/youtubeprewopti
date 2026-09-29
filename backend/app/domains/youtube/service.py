"""Read access to (global) YouTube entities, restricted to what a workspace has discovered.

A channel is visible to a workspace iff it belongs to at least one of the workspace's projects.
Invisible entities are reported as 404 (not 403) to avoid leaking existence.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import ColumnElement, and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core.errors import NotFoundError
from app.domains.media.models import ImageAsset, Thumbnail
from app.domains.projects.models import ProjectChannel, SearchProject
from app.domains.youtube.models import Channel, Video
from app.domains.youtube.schemas import (
    ChannelDetail,
    ChannelOut,
    ChannelSort,
    ProjectRef,
    SortOrder,
    ThumbnailOut,
    VideoDetail,
    VideoOut,
)


def _visible(workspace_id: int, project_id: int | None = None) -> ColumnElement[bool]:
    sub = (
        select(ProjectChannel.channel_id)
        .join(SearchProject, SearchProject.id == ProjectChannel.project_id)
        .where(ProjectChannel.channel_id == Channel.id, SearchProject.workspace_id == workspace_id)
    )
    if project_id is not None:
        sub = sub.where(ProjectChannel.project_id == project_id)
    return exists(sub.correlate(Channel))


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def list_channels(
    db: AsyncSession,
    workspace_id: int,
    *,
    project_id: int | None,
    q: str | None,
    min_subscribers: int | None,
    max_subscribers: int | None,
    country: str | None,
    sort: ChannelSort,
    order: SortOrder,
    limit: int,
    offset: int,
) -> tuple[list[ChannelOut], int]:
    where: list[ColumnElement[bool]] = [_visible(workspace_id, project_id)]
    if q:
        pattern = f"%{_escape_like(q.strip())}%"
        where.append(
            or_(
                Channel.title.ilike(pattern, escape="\\"),
                Channel.handle.ilike(pattern, escape="\\"),
                Channel.youtube_channel_id == q.strip(),
            )
        )
    if min_subscribers is not None:
        where.append(Channel.subscriber_count >= min_subscribers)
    if max_subscribers is not None:
        where.append(Channel.subscriber_count <= max_subscribers)
    if country:
        where.append(Channel.country == country.upper())

    total = await db.scalar(select(func.count()).select_from(Channel).where(*where)) or 0

    stmt = select(Channel).where(*where)
    key: ColumnElement[Any] | InstrumentedAttribute[Any]
    if sort == ChannelSort.DISCOVERED:
        last_seen = (
            select(func.max(ProjectChannel.last_discovered_at))
            .join(SearchProject, SearchProject.id == ProjectChannel.project_id)
            .where(ProjectChannel.channel_id == Channel.id, SearchProject.workspace_id == workspace_id)
            .correlate(Channel)
            .scalar_subquery()
        )
        key = last_seen
    else:
        key = {
            ChannelSort.SUBSCRIBERS: Channel.subscriber_count,
            ChannelSort.VIEWS: Channel.view_count,
            ChannelSort.VIDEOS: Channel.video_count,
            ChannelSort.TITLE: Channel.title,
            ChannelSort.PUBLISHED: Channel.published_at,
        }[sort]
    ordered = key.asc() if order == SortOrder.ASC else key.desc()
    stmt = stmt.order_by(ordered.nulls_last(), Channel.id).limit(limit).offset(offset)
    return [ChannelOut.model_validate(c) for c in await db.scalars(stmt)], total


async def get_visible_channel(db: AsyncSession, workspace_id: int, channel_id: int) -> Channel:
    channel = await db.scalar(select(Channel).where(Channel.id == channel_id, _visible(workspace_id)))
    if channel is None:
        raise NotFoundError("Channel not found")
    return channel


async def get_channel_detail(db: AsyncSession, workspace_id: int, channel_id: int) -> ChannelDetail:
    channel = await get_visible_channel(db, workspace_id, channel_id)
    projects = (
        await db.execute(
            select(SearchProject.id, SearchProject.name)
            .join(ProjectChannel, ProjectChannel.project_id == SearchProject.id)
            .where(ProjectChannel.channel_id == channel.id, SearchProject.workspace_id == workspace_id)
            .order_by(SearchProject.name)
        )
    ).all()
    videos_stored = await db.scalar(
        select(func.count()).select_from(Video).where(Video.channel_id == channel.id)
    )
    base = ChannelOut.model_validate(channel).model_dump()
    return ChannelDetail(
        **base,
        description=channel.description,
        custom_url=channel.custom_url,
        keywords=channel.keywords,
        topic_categories=channel.topic_categories,
        projects=[ProjectRef(id=p.id, name=p.name) for p in projects],
        videos_stored=videos_stored or 0,
    )


def image_url(asset_id: int) -> str:
    return f"/api/images/{asset_id}"


def _thumb(thumb: Thumbnail | None, asset: ImageAsset | None) -> ThumbnailOut | None:
    if thumb is None:
        return None
    return ThumbnailOut(
        fetch_status=thumb.fetch_status,
        original_url=thumb.original_url,
        image_asset_id=thumb.image_asset_id,
        image_url=image_url(asset.id) if asset else None,
        width=asset.width if asset else None,
        height=asset.height if asset else None,
    )


def _video_select():
    return (
        select(Video, Thumbnail, ImageAsset)
        .outerjoin(Thumbnail, Thumbnail.video_id == Video.id)
        .outerjoin(ImageAsset, and_(ImageAsset.id == Thumbnail.image_asset_id))
    )


async def list_channel_videos(
    db: AsyncSession, workspace_id: int, channel_id: int, *, limit: int, offset: int
) -> tuple[list[VideoOut], int]:
    await get_visible_channel(db, workspace_id, channel_id)
    total = (
        await db.scalar(select(func.count()).select_from(Video).where(Video.channel_id == channel_id)) or 0
    )
    rows = await db.execute(
        _video_select()
        .where(Video.channel_id == channel_id)
        .order_by(Video.published_at.desc().nulls_last(), Video.id.desc())
        .limit(limit)
        .offset(offset)
    )
    items = []
    for video, thumb, asset in rows:
        out = VideoOut.model_validate(video)
        out.thumbnail = _thumb(thumb, asset)
        items.append(out)
    return items, total


async def get_video_detail(db: AsyncSession, workspace_id: int, video_id: int) -> VideoDetail:
    row = (await db.execute(_video_select().where(Video.id == video_id))).first()
    if row is None:
        raise NotFoundError("Video not found")
    video, thumb, asset = row
    await get_visible_channel(db, workspace_id, video.channel_id)  # 404 if not visible
    out = VideoDetail.model_validate(video)
    out.thumbnail = _thumb(thumb, asset)
    return out
