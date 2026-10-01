"""Read-only channel/video endpoints (discovery writes arrive with the worker in Phase 2)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import DbSession, Paging, ReadAuth
from app.api.schemas import Page
from app.domains.youtube import service
from app.domains.youtube.schemas import (
    ChannelDetail,
    ChannelFilterSet,
    ChannelOut,
    ChannelSort,
    SortOrder,
    VideoDetail,
    VideoOut,
)

router = APIRouter(tags=["youtube"])


NonNegQ = Annotated[int | None, Query(ge=0)]


@router.get("/channels", response_model=Page[ChannelOut])
async def list_channels(
    ctx: ReadAuth,
    db: DbSession,
    page: Paging,
    project_id: int | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    min_subscribers: NonNegQ = None,
    max_subscribers: NonNegQ = None,
    min_avg_views: NonNegQ = None,
    max_avg_views: NonNegQ = None,
    min_median_views: NonNegQ = None,
    min_last_video_views: NonNegQ = None,
    min_avg_views_recent: NonNegQ = None,
    min_videos: NonNegQ = None,
    max_videos: NonNegQ = None,
    min_videos_7d: NonNegQ = None,
    min_videos_30d: NonNegQ = None,
    min_videos_90d: NonNegQ = None,
    max_avg_upload_gap_days: Annotated[float | None, Query(ge=0)] = None,
    max_days_since_last_upload: NonNegQ = None,
    min_views_to_subs: Annotated[float | None, Query(ge=0)] = None,
    min_upload_consistency: Annotated[float | None, Query(ge=0, le=1)] = None,
    country: Annotated[str | None, Query(pattern=r"^[A-Za-z]{2}$")] = None,
    language: Annotated[str | None, Query(max_length=20)] = None,
    niche: Annotated[list[int] | None, Query(max_length=50)] = None,
    exclude_niche: Annotated[list[int] | None, Query(max_length=50)] = None,
    sort: ChannelSort = ChannelSort.SUBSCRIBERS,
    order: SortOrder = SortOrder.DESC,
) -> Page[ChannelOut]:
    """Channels discovered in the workspace's projects, filtered by §3 criteria (all optional, AND-ed).
    ``niche`` / ``exclude_niche`` may repeat and include descendants (niche → topics → subtopics)."""
    filters = ChannelFilterSet(
        min_subscribers=min_subscribers, max_subscribers=max_subscribers, min_avg_views=min_avg_views,
        max_avg_views=max_avg_views, min_median_views=min_median_views,
        min_last_video_views=min_last_video_views, min_avg_views_recent=min_avg_views_recent,
        min_videos=min_videos, max_videos=max_videos, min_videos_7d=min_videos_7d,
        min_videos_30d=min_videos_30d, min_videos_90d=min_videos_90d,
        max_avg_upload_gap_days=max_avg_upload_gap_days,
        max_days_since_last_upload=max_days_since_last_upload,
        min_views_to_subs=min_views_to_subs, min_upload_consistency=min_upload_consistency, country=country,
        language=language, niche=niche or [], exclude_niche=exclude_niche or [],
    )
    items, total = await service.list_channels(
        db,
        ctx.workspace.id,
        project_id=project_id,
        q=q,
        filters=filters,
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
