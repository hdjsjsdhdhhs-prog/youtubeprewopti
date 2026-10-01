"""Read access to (global) YouTube entities, restricted to what a workspace has discovered.

A channel is visible to a workspace iff it belongs to at least one of the workspace's projects.
Invisible entities are reported as 404 (not 403) to avoid leaking existence.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import ColumnElement, and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core.errors import NotFoundError
from app.domains.media.models import ImageAsset, Thumbnail
from app.domains.projects.models import (
    ChannelDiscovery,
    ProjectChannel,
    SearchProject,
    SearchQuery,
    TaxonomyNode,
)
from app.domains.projects.taxonomy import subtree_ids
from app.domains.youtube.models import Channel, ChannelMetrics, Video
from app.domains.youtube.schemas import (
    ChannelDetail,
    ChannelFilterSet,
    ChannelMetricsOut,
    ChannelOut,
    ChannelSort,
    DiscoveryOut,
    NicheRef,
    ProjectRef,
    SortOrder,
    ThumbnailOut,
    VideoDetail,
    VideoOut,
)

MAX_DISCOVERIES_SHOWN = 100


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


def _in_niches(workspace_id: int, project_id: int | None, node_ids: list[int]) -> ColumnElement[bool]:
    """Channel was discovered (in this workspace / project) by a query tagged with one of ``node_ids``."""
    sub = (
        select(ChannelDiscovery.id)
        .join(SearchQuery, SearchQuery.id == ChannelDiscovery.search_query_id)
        .join(SearchProject, SearchProject.id == ChannelDiscovery.project_id)
        .where(
            ChannelDiscovery.channel_id == Channel.id,
            SearchProject.workspace_id == workspace_id,
            SearchQuery.taxonomy_node_id.in_(node_ids),
        )
    )
    if project_id is not None:
        sub = sub.where(ChannelDiscovery.project_id == project_id)
    return exists(sub.correlate(Channel))


async def _filter_conditions(
    db: AsyncSession, workspace_id: int, project_id: int | None, f: ChannelFilterSet
) -> list[ColumnElement[bool]]:
    m = ChannelMetrics
    where: list[ColumnElement[bool]] = []
    ranges: list[tuple[Any, int | float | None, int | float | None]] = [
        (Channel.subscriber_count, f.min_subscribers, f.max_subscribers),
        (Channel.video_count, f.min_videos, f.max_videos),
        (m.avg_views, f.min_avg_views, f.max_avg_views),
        (m.median_views, f.min_median_views, None),
        (m.last_video_views, f.min_last_video_views, None),
        (m.avg_views_recent, f.min_avg_views_recent, None),
        (m.videos_7d, f.min_videos_7d, None),
        (m.videos_30d, f.min_videos_30d, None),
        (m.videos_90d, f.min_videos_90d, None),
        (m.avg_days_between_uploads, None, f.max_avg_upload_gap_days),
        (m.views_to_subs_ratio, f.min_views_to_subs, None),
        (m.upload_consistency, f.min_upload_consistency, None),
    ]
    for column, lo, hi in ranges:
        if lo is not None:
            where.append(column >= lo)
        if hi is not None:
            where.append(column <= hi)
    if f.max_days_since_last_upload is not None:
        where.append(m.last_video_at >= func.now() - timedelta(days=f.max_days_since_last_upload))
    if f.country:
        where.append(Channel.country == f.country.upper())
    if f.language:
        lang = f.language.lower()
        where.append(or_(func.lower(Channel.default_language) == lang,
                         func.lower(Channel.default_language).like(f"{_escape_like(lang)}-%", escape="\\")))
    if f.niche:
        where.append(_in_niches(workspace_id, project_id, await subtree_ids(db, f.niche)))
    if f.exclude_niche:
        where.append(~_in_niches(workspace_id, project_id, await subtree_ids(db, f.exclude_niche)))
    return where


def _channel_out(channel: Channel, metrics: ChannelMetrics | None) -> ChannelOut:
    out = ChannelOut.model_validate(channel)
    out.metrics = ChannelMetricsOut.model_validate(metrics) if metrics is not None else None
    return out


async def list_channels(
    db: AsyncSession,
    workspace_id: int,
    *,
    project_id: int | None,
    q: str | None,
    filters: ChannelFilterSet,
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
    where.extend(await _filter_conditions(db, workspace_id, project_id, filters))

    joined = select(Channel.id).outerjoin(ChannelMetrics, ChannelMetrics.channel_id == Channel.id)
    total = await db.scalar(select(func.count()).select_from(joined.where(*where).subquery())) or 0

    stmt = (
        select(Channel, ChannelMetrics)
        .outerjoin(ChannelMetrics, ChannelMetrics.channel_id == Channel.id)
        .where(*where)
    )
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
            ChannelSort.AVG_VIEWS: ChannelMetrics.avg_views,
            ChannelSort.MEDIAN_VIEWS: ChannelMetrics.median_views,
            ChannelSort.LAST_VIDEO: ChannelMetrics.last_video_at,
            ChannelSort.VIEWS_RATIO: ChannelMetrics.views_to_subs_ratio,
            ChannelSort.VIDEOS_30D: ChannelMetrics.videos_30d,
        }[sort]
    ordered = key.asc() if order == SortOrder.ASC else key.desc()
    stmt = stmt.order_by(ordered.nulls_last(), Channel.id).limit(limit).offset(offset)
    return [_channel_out(c, m) for c, m in (await db.execute(stmt)).tuples()], total


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
    metrics = await db.get(ChannelMetrics, channel.id)
    in_workspace = (
        select(ChannelDiscovery, SearchProject.name, SearchQuery.text, TaxonomyNode)
        .join(SearchProject, SearchProject.id == ChannelDiscovery.project_id)
        .outerjoin(SearchQuery, SearchQuery.id == ChannelDiscovery.search_query_id)
        .outerjoin(TaxonomyNode, TaxonomyNode.id == SearchQuery.taxonomy_node_id)
        .where(ChannelDiscovery.channel_id == channel.id, SearchProject.workspace_id == workspace_id)
    )
    discoveries_total = await db.scalar(select(func.count()).select_from(in_workspace.subquery())) or 0
    rows = (
        await db.execute(
            in_workspace.order_by(ChannelDiscovery.discovered_at.desc(), ChannelDiscovery.id.desc())
            .limit(MAX_DISCOVERIES_SHOWN)
        )
    ).tuples().all()
    niche_rows = (
        await db.scalars(
            select(TaxonomyNode)
            .join(SearchQuery, SearchQuery.taxonomy_node_id == TaxonomyNode.id)
            .join(ChannelDiscovery, ChannelDiscovery.search_query_id == SearchQuery.id)
            .join(SearchProject, SearchProject.id == ChannelDiscovery.project_id)
            .where(ChannelDiscovery.channel_id == channel.id, SearchProject.workspace_id == workspace_id)
            .distinct()
            .order_by(TaxonomyNode.name)
        )
    ).all()

    def niche_ref(node: TaxonomyNode | None) -> NicheRef | None:
        return NicheRef(id=node.id, name=node.name, level=node.level.value) if node is not None else None

    base = _channel_out(channel, metrics).model_dump()
    return ChannelDetail(
        **base,
        description=channel.description,
        custom_url=channel.custom_url,
        keywords=channel.keywords,
        topic_categories=channel.topic_categories,
        projects=[ProjectRef(id=p.id, name=p.name) for p in projects],
        videos_stored=videos_stored or 0,
        niches=[n for n in (niche_ref(node) for node in niche_rows) if n is not None],
        discoveries=[
            DiscoveryOut(
                project=ProjectRef(id=d.project_id, name=pname), query_id=d.search_query_id, query_text=qtext,
                niche=niche_ref(node), method=d.method.value, source_video_id=d.source_video_id,
                discovered_at=d.discovered_at,
            )
            for d, pname, qtext, node in rows
        ],
        discoveries_total=discoveries_total,
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
