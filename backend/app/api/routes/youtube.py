"""Read-only channel/video endpoints (discovery writes arrive with the worker in Phase 2)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import DbSession, Paging, ReadAuth
from app.api.schemas import Page
from app.domains.youtube import service
from app.domains.youtube.schemas import (
    ChannelDetail,
    ChannelOut,
    ChannelSort,
    SortOrder,
    VideoDetail,
    VideoOut,
)

router = APIRouter(tags=["youtube"])


@router.get("/channels", response_model=Page[ChannelOut])
async def list_channels(
    ctx: ReadAuth,
    db: DbSession,
    page: Paging,
    project_id: int | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    min_subscribers: Annotated[int | None, Query(ge=0)] = None,
    max_subscribers: Annotated[int | None, Query(ge=0)] = None,
    country: Annotated[str | None, Query(pattern=r"^[A-Za-z]{2}$")] = None,
    sort: ChannelSort = ChannelSort.SUBSCRIBERS,
    order: SortOrder = SortOrder.DESC,
) -> Page[ChannelOut]:
    items, total = await service.list_channels(
        db,
        ctx.workspace.id,
        project_id=project_id,
        q=q,
        min_subscribers=min_subscribers,
        max_subscribers=max_subscribers,
        country=country,
        sort=sort,
        order=order,
        limit=page.limit,
        offset=page.offset,
    )
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/channels/{channel_id}", response_model=ChannelDetail)
async def get_channel(channel_id: int, ctx: ReadAuth, db: DbSession) -> ChannelDetail:
    return await service.get_channel_detail(db, ctx.workspace.id, channel_id)


@router.get("/channels/{channel_id}/videos", response_model=Page[VideoOut])
async def list_channel_videos(channel_id: int, ctx: ReadAuth, db: DbSession, page: Paging) -> Page[VideoOut]:
    items, total = await service.list_channel_videos(
        db, ctx.workspace.id, channel_id, limit=page.limit, offset=page.offset
    )
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/videos/{video_id}", response_model=VideoDetail)
async def get_video(video_id: int, ctx: ReadAuth, db: DbSession) -> VideoDetail:
    return await service.get_video_detail(db, ctx.workspace.id, video_id)
