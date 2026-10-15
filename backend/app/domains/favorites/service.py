"""Favorites (ТЗ §37): personal to a member of a workspace. Only entities visible to the workspace can be
added; a favorite whose entity is no longer visible (project deleted) is not listed."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import ColumnElement, and_, delete, exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core.errors import NotFoundError
from app.domains.favorites.models import Favorite, FavoriteEntity
from app.domains.favorites.schemas import (
    FavoriteChannelRef,
    FavoriteList,
    FavoriteOut,
    FavoriteState,
    FavoriteVideoRef,
)
from app.domains.media.models import Thumbnail
from app.domains.projects.models import ProjectChannel, SearchProject
from app.domains.youtube.models import Channel, Video
from app.domains.youtube.service import image_url

type IdExpr = ColumnElement[int] | InstrumentedAttribute[int]  # a correlated id column


def channel_visible(workspace_id: int, channel_id: IdExpr) -> ColumnElement[bool]:
    return exists(
        select(ProjectChannel.channel_id)
        .join(SearchProject, SearchProject.id == ProjectChannel.project_id)
        .where(ProjectChannel.channel_id == channel_id, SearchProject.workspace_id == workspace_id)
    )


def _video_visible(workspace_id: int, video_id: IdExpr) -> ColumnElement[bool]:
    return exists(
        select(Video.id).where(Video.id == video_id, channel_visible(workspace_id, Video.channel_id))
    )


def _entity_visible(workspace_id: int) -> ColumnElement[bool]:
    return or_(
        and_(
            Favorite.entity_type == FavoriteEntity.CHANNEL,
            channel_visible(workspace_id, Favorite.entity_id),
        ),
        and_(
            Favorite.entity_type == FavoriteEntity.THUMBNAIL,
            _video_visible(workspace_id, Favorite.entity_id),
        ),
    )


def is_favorite_condition(
    workspace_id: int, user_id: int, entity_type: FavoriteEntity, entity_id: IdExpr
) -> ColumnElement[bool]:
    """SQL «is a favorite of this user» for a correlated entity id (lead filters)."""
    return exists(
        select(Favorite.id).where(
            Favorite.workspace_id == workspace_id,
            Favorite.user_id == user_id,
            Favorite.entity_type == entity_type,
            Favorite.entity_id == entity_id,
        )
    )


async def _check_visible(
    db: AsyncSession, workspace_id: int, entity_type: FavoriteEntity, entity_id: int
) -> None:
    if entity_type == FavoriteEntity.CHANNEL:
        stmt = select(channel_visible(workspace_id, Channel.id)).where(Channel.id == entity_id)
        missing = "Channel not found"
    else:
        stmt = select(channel_visible(workspace_id, Video.channel_id)).where(Video.id == entity_id)
        missing = "Video not found"
    if not await db.scalar(stmt):
        raise NotFoundError(missing)


async def favorite_ids(
    db: AsyncSession, workspace_id: int, user_id: int, entity_type: FavoriteEntity, ids: Iterable[int]
) -> set[int]:
    wanted = list(dict.fromkeys(ids))
    if not wanted:
        return set()
    return set(
        await db.scalars(
            select(Favorite.entity_id).where(
                Favorite.workspace_id == workspace_id,
                Favorite.user_id == user_id,
                Favorite.entity_type == entity_type,
                Favorite.entity_id.in_(wanted),
            )
        )
    )


async def set_favorites(
    db: AsyncSession,
    workspace_id: int,
    user_id: int,
    entity_type: FavoriteEntity,
    ids: Iterable[int],
    on: bool,
) -> set[int]:
    """Add (``on``) or remove favorites of already visible entities; returns the ids that changed."""
    wanted = list(dict.fromkeys(ids))
    if not wanted:
        return set()
    if on:
        changed = await db.scalars(
            insert(Favorite)
            .values(
                [
                    {
                        "workspace_id": workspace_id,
                        "user_id": user_id,
                        "entity_type": entity_type,
                        "entity_id": i,
                    }
                    for i in wanted
                ]
            )
            .on_conflict_do_nothing()
            .returning(Favorite.entity_id)
        )
    else:
        changed = await db.scalars(
            delete(Favorite)
            .where(
                Favorite.workspace_id == workspace_id,
                Favorite.user_id == user_id,
                Favorite.entity_type == entity_type,
                Favorite.entity_id.in_(wanted),
            )
            .returning(Favorite.entity_id)
        )
    return set(changed)


async def add_favorite(
    db: AsyncSession, workspace_id: int, user_id: int, entity_type: FavoriteEntity, entity_id: int
) -> FavoriteState:
    await _check_visible(db, workspace_id, entity_type, entity_id)
    await set_favorites(db, workspace_id, user_id, entity_type, [entity_id], True)
    return FavoriteState(entity_type=entity_type, entity_id=entity_id, is_favorite=True)


async def remove_favorite(
    db: AsyncSession, workspace_id: int, user_id: int, entity_type: FavoriteEntity, entity_id: int
) -> FavoriteState:
    await set_favorites(db, workspace_id, user_id, entity_type, [entity_id], False)
    return FavoriteState(entity_type=entity_type, entity_id=entity_id, is_favorite=False)


async def list_favorites(
    db: AsyncSession,
    workspace_id: int,
    user_id: int,
    entity_type: FavoriteEntity | None,
    *,
    limit: int,
    offset: int,
) -> FavoriteList:
    conds: list[ColumnElement[bool]] = [
        Favorite.workspace_id == workspace_id,
        Favorite.user_id == user_id,
        _entity_visible(workspace_id),
    ]
    if entity_type is not None:
        conds.append(Favorite.entity_type == entity_type)
    total = await db.scalar(select(func.count()).select_from(Favorite).where(*conds)) or 0
    rows = list(
        await db.scalars(
            select(Favorite).where(*conds).order_by(Favorite.created_at.desc(), Favorite.id.desc())
            .limit(limit).offset(offset)
        )
    )
    channel_ids = [r.entity_id for r in rows if r.entity_type == FavoriteEntity.CHANNEL]
    video_ids = [r.entity_id for r in rows if r.entity_type == FavoriteEntity.THUMBNAIL]
    channels: dict[int, FavoriteChannelRef] = {}
    if channel_ids:
        for c in await db.scalars(select(Channel).where(Channel.id.in_(channel_ids))):
            channels[c.id] = FavoriteChannelRef(
                id=c.id, youtube_channel_id=c.youtube_channel_id, title=c.title, avatar_url=c.avatar_url,
                subscriber_count=c.subscriber_count, is_demo=c.is_demo,
            )
    videos: dict[int, FavoriteVideoRef] = {}
    if video_ids:
        for video, channel_title, asset_id in (
            await db.execute(
                select(Video, Channel.title, Thumbnail.image_asset_id)
                .join(Channel, Channel.id == Video.channel_id)
                .outerjoin(Thumbnail, Thumbnail.video_id == Video.id)
                .where(Video.id.in_(video_ids))
            )
        ).tuples():
            videos[video.id] = FavoriteVideoRef(
                id=video.id, youtube_video_id=video.youtube_video_id, title=video.title,
                channel_id=video.channel_id, channel_title=channel_title,
                image_url=image_url(asset_id) if asset_id is not None else None,
            )
    return FavoriteList(
        items=[
            FavoriteOut(
                entity_type=r.entity_type, entity_id=r.entity_id, created_at=r.created_at,
                channel=channels.get(r.entity_id) if r.entity_type == FavoriteEntity.CHANNEL else None,
                video=videos.get(r.entity_id) if r.entity_type == FavoriteEntity.THUMBNAIL else None,
            )
            for r in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
