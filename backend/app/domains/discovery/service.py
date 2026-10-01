"""Discovery pipeline (§2, §50, ADR-0008).

One search query is processed by :func:`run_query`:

1. ``search.list`` pages (100 units each) up to ``search_depth`` / ``results_per_query``;
2. channels of the hits → ``channels.list`` in batches of 50 (1 unit) — skipped for channels fetched
   less than ``youtube_channel_refresh_hours`` ago;
3. newest ``videos_to_analyze`` uploads per fetched channel (``playlistItems.list``, 1 unit) plus the
   source videos of the hits → ``videos.list`` in batches of 50 (1 unit);
4. upsert of the *global* ``channels`` / ``videos`` / ``thumbnails`` rows, daily stats snapshots and
   ``channel_metrics``;
5. project membership (``project_channels``) and provenance (``channel_discoveries``: project, query,
   source video, method) — one Channel no matter how many queries/projects find it (§50).

Filtering is not a pipeline stage: filters are SQL over channels + metrics at read time.
"""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy import Boolean, func, literal_column, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError, ConflictError, IntegrationError, IntegrationErrorCode, NotFoundError
from app.domains.discovery.metrics import WINDOW, VideoPoint, compute_metrics
from app.domains.discovery.quota import QuotaGuard, QuotaUsage, get_usage
from app.domains.identity.service import audit
from app.domains.jobs.models import JobRun
from app.domains.jobs.service import Enqueued, JobQueue, JobType, enqueue_job
from app.domains.media.models import Thumbnail
from app.domains.projects.models import (
    ChannelDiscovery,
    DiscoveryMethod,
    ProjectChannel,
    ProjectStatus,
    QueryStatus,
    SearchProject,
    SearchQuery,
)
from app.domains.youtube.models import (
    Channel,
    ChannelMetrics,
    ChannelStatsSnapshot,
    Video,
    VideoStatsSnapshot,
)
from app.providers.youtube import (
    LIST_COST,
    MAX_PAGE_SIZE,
    SEARCH_COST,
    ChannelInfo,
    SearchParams,
    SearchType,
    VideoInfo,
    YouTubeProvider,
)

MAX_QUERIES_PER_JOB = 1000
# In RETURNING of an upsert: true when the row was inserted, false when an existing row was updated.
_INSERTED = literal_column("(xmax = 0)", Boolean)


def _batches[T](items: Sequence[T], size: int = MAX_PAGE_SIZE) -> Iterable[Sequence[T]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def provider_name(settings: Settings) -> str | None:
    """Quota ledger key of the configured provider (None = not configured)."""
    mode = settings.effective_youtube_provider
    if mode == "mock":
        return "youtube_mock"
    if mode == "api" and settings.youtube_api_key is not None:
        return "youtube_api"
    return None


async def quota_status(db: AsyncSession, settings: Settings) -> tuple[str | None, QuotaUsage | None]:
    name = provider_name(settings)
    if name is None:
        return None, None
    return name, await get_usage(db, name, settings.youtube_daily_quota)


# ---------------------------------------------------------------------------
# Planning / enqueueing (API side)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class QuotaEstimate:
    search_units: int  # exact upper bound of search.list costs
    max_total_units: int  # incl. enrichment if every hit were a new channel


def estimate_quota(queries: int, project: SearchProject) -> QuotaEstimate:
    pages = min(project.search_depth, math.ceil(project.results_per_query / MAX_PAGE_SIZE))
    max_channels = min(project.results_per_query, pages * MAX_PAGE_SIZE)
    per_channel_videos = project.videos_to_analyze + 1  # uploads + the source video
    enrichment = (
        math.ceil(max_channels / MAX_PAGE_SIZE) * LIST_COST  # channels.list
        + max_channels * LIST_COST  # playlistItems.list
        + math.ceil(max_channels * per_channel_videos / MAX_PAGE_SIZE) * LIST_COST  # videos.list
    )
    search = queries * pages * SEARCH_COST
    return QuotaEstimate(search_units=search, max_total_units=search + queries * enrichment)


@dataclass(frozen=True)
class DiscoveryPlan:
    enqueued: Enqueued | None
    query_ids: list[int]
    estimate: QuotaEstimate


async def _project(db: AsyncSession, workspace_id: int | None, project_id: int) -> SearchProject | None:
    return await db.scalar(
        select(SearchProject).where(
            SearchProject.id == project_id, SearchProject.workspace_id == workspace_id
        )
    )


async def start_discovery(
    db: AsyncSession,
    settings: Settings,
    workspace_id: int,
    actor_id: int,
    project_id: int,
    *,
    query_ids: list[int] | None,
    include_done: bool,
) -> DiscoveryPlan:
    if provider_name(settings) is None:
        raise ConflictError(
            "YouTube API is not configured: set YTL_YOUTUBE_API_KEY (or YTL_YOUTUBE_PROVIDER=mock).",
            code="youtube_not_configured",
        )
    project = await _project(db, workspace_id, project_id)
    if project is None:
        raise NotFoundError("Project not found")
    if project.status == ProjectStatus.ARCHIVED:
        raise ConflictError("Project is archived; restore it to run discovery", code="project_archived")

    stmt = select(SearchQuery.id).where(
        SearchQuery.project_id == project_id, SearchQuery.status != QueryStatus.RUNNING
    )
    if query_ids is not None:
        stmt = stmt.where(SearchQuery.id.in_(query_ids))
    elif not include_done:
        stmt = stmt.where(SearchQuery.status.in_([QueryStatus.PENDING, QueryStatus.FAILED]))
    ids = sorted(await db.scalars(stmt.order_by(SearchQuery.id).limit(MAX_QUERIES_PER_JOB + 1)))
    if query_ids is not None and len(ids) != len(set(query_ids)):
        raise AppError(
            "Some queries do not belong to the project or are running now", code="invalid_queries"
        )
    if len(ids) > MAX_QUERIES_PER_JOB:
        ids = ids[:MAX_QUERIES_PER_JOB]
    estimate = estimate_quota(len(ids), project)
    if not ids:
        return DiscoveryPlan(enqueued=None, query_ids=[], estimate=estimate)

    enq = await enqueue_job(
        db,
        type_=JobType.DISCOVERY,
        queue=JobQueue.BULK,
        params={"project_id": project_id, "query_ids": ids},
        workspace_id=workspace_id,
        created_by=actor_id,
        progress_total=len(ids),
    )
    if enq.created:
        await audit(
            db, "discovery.enqueued", workspace_id=workspace_id, actor_user_id=actor_id,
            entity_type="search_project", entity_id=project_id,
            diff={"job_run_id": enq.job.id, "queries": len(ids), "quota_max": estimate.max_total_units},
        )
    return DiscoveryPlan(enqueued=enq, query_ids=ids, estimate=estimate)


# ---------------------------------------------------------------------------
# Execution (worker side)
# ---------------------------------------------------------------------------
@dataclass
class QueryOutcome:
    hits: int = 0
    channels_found: int = 0
    channels_new: int = 0  # new global Channel rows
    channels_new_in_project: int = 0
    channels_fetched: int = 0
    channels_fresh_skipped: int = 0
    videos_upserted: int = 0
    found_channel_ids: set[int] = field(default_factory=set)


async def _upsert_channels(
    db: AsyncSession, infos: list[ChannelInfo], is_demo: bool, now: datetime
) -> tuple[dict[str, int], int]:
    """Returns (youtube_channel_id → pk, number of newly inserted channels)."""
    ids: dict[str, int] = {}
    created = 0
    for info in infos:
        values: dict[str, Any] = {
            "title": info.title, "description": info.description, "handle": info.handle,
            "custom_url": info.custom_url, "avatar_url": info.avatar_url, "country": info.country,
            "default_language": (info.default_language or "")[:20] or None, "published_at": info.published_at,
            "uploads_playlist_id": info.uploads_playlist_id, "subscriber_count": info.subscriber_count,
            "subscribers_hidden": info.subscribers_hidden, "view_count": info.view_count,
            "video_count": info.video_count, "topic_categories": info.topic_categories,
            "keywords": info.keywords, "last_fetched_at": now, "raw": info.raw,
        }
        stmt = insert(Channel).values(youtube_channel_id=info.youtube_channel_id, is_demo=is_demo, **values)
        upsert = stmt.on_conflict_do_update(
            index_elements=["youtube_channel_id"], set_={**values, "updated_at": func.now()}
        ).returning(Channel.id, _INSERTED.label("inserted"))
        row = (await db.execute(upsert)).one()
        ids[info.youtube_channel_id] = row.id
        created += int(bool(row.inserted))
        snap = insert(ChannelStatsSnapshot).values(
            channel_id=row.id, captured_on=now.date(), subscriber_count=info.subscriber_count,
            view_count=info.view_count, video_count=info.video_count,
        )
        await db.execute(
            snap.on_conflict_do_update(
                index_elements=["channel_id", "captured_on"],
                set_={
                    "subscriber_count": snap.excluded.subscriber_count,
                    "view_count": snap.excluded.view_count,
                    "video_count": snap.excluded.video_count,
                },
            )
        )
    return ids, created


async def _upsert_videos(
    db: AsyncSession, infos: list[VideoInfo], channel_pks: dict[str, int], now: datetime
) -> int:
    count = 0
    for info in infos:
        channel_pk = channel_pks.get(info.youtube_channel_id)
        if channel_pk is None:
            continue  # the channel itself was not returned (terminated) — keep the data consistent
        values: dict[str, Any] = {
            "channel_id": channel_pk, "title": info.title, "description": info.description,
            "published_at": info.published_at, "duration_seconds": info.duration_seconds,
            "category_id": (info.category_id or "")[:10] or None, "tags": info.tags,
            "view_count": info.view_count, "like_count": info.like_count, "comment_count": info.comment_count,
            "last_fetched_at": now, "raw": info.raw,
        }
        stmt = insert(Video).values(youtube_video_id=info.youtube_video_id, **values)
        video_pk = await db.scalar(
            stmt.on_conflict_do_update(
                index_elements=["youtube_video_id"], set_={**values, "updated_at": func.now()}
            ).returning(Video.id)
        )
        count += 1
        if info.thumbnail_url:
            # A stored thumbnail is kept (content-addressed asset, may already be analysed); only a not yet
            # downloaded one gets the (possibly better) new URL.
            thumb = insert(Thumbnail).values(video_id=video_pk, original_url=info.thumbnail_url[:500])
            await db.execute(
                thumb.on_conflict_do_update(
                    index_elements=["video_id"],
                    set_={"original_url": thumb.excluded.original_url},
                    where=Thumbnail.image_asset_id.is_(None),
                )
            )
        snap = insert(VideoStatsSnapshot).values(
            video_id=video_pk, captured_on=now.date(), view_count=info.view_count,
            like_count=info.like_count, comment_count=info.comment_count,
        )
        await db.execute(
            snap.on_conflict_do_update(
                index_elements=["video_id", "captured_on"],
                set_={
                    "view_count": snap.excluded.view_count,
                    "like_count": snap.excluded.like_count,
                    "comment_count": snap.excluded.comment_count,
                },
            )
        )
    return count


async def refresh_metrics(db: AsyncSession, channel_ids: Iterable[int], now: datetime) -> None:
    for channel_id in channel_ids:
        subs = await db.scalar(select(Channel.subscriber_count).where(Channel.id == channel_id))
        rows = (
            await db.execute(
                select(Video.published_at, Video.view_count)
                .where(Video.channel_id == channel_id)
                .order_by(Video.published_at.desc().nulls_last())
                .limit(WINDOW)
            )
        ).all()
        m = compute_metrics([VideoPoint(r.published_at, r.view_count) for r in rows], subs, now)
        values = {"computed_at": now, **m.as_row()}
        stmt = insert(ChannelMetrics).values(channel_id=channel_id, **values)
        await db.execute(stmt.on_conflict_do_update(index_elements=["channel_id"], set_=values))


def _published_after(project: SearchProject) -> datetime | None:
    if project.published_after is None:
        return None
    return datetime.combine(project.published_after, time.min, tzinfo=UTC)


async def run_query(
    db: AsyncSession,
    provider: YouTubeProvider,
    quota: QuotaGuard,
    settings: Settings,
    project: SearchProject,
    query: SearchQuery,
    *,
    now: datetime | None = None,
) -> QueryOutcome:
    """Process one query end to end inside ``db`` (caller commits). Quota is reserved before every call."""
    now = now or datetime.now(UTC)
    out = QueryOutcome()

    # 1. search pages
    source_video: dict[str, str | None] = {}  # youtube channel id -> first hit video id (insertion-ordered)
    remaining, token = project.results_per_query, None
    for _ in range(project.search_depth):
        if remaining <= 0:
            break
        await quota.reserve(SEARCH_COST)
        page = await provider.search(
            SearchParams(
                query=query.text, search_type=query.search_type, max_results=min(MAX_PAGE_SIZE, remaining),
                page_token=token, language=project.language, region_code=project.region_code,
                published_after=_published_after(project),
            )
        )
        out.hits += len(page.hits)
        remaining -= len(page.hits)
        for hit in page.hits:
            if source_video.get(hit.channel_id) is None:
                source_video[hit.channel_id] = hit.video_id
        token = page.next_page_token
        if not token or not page.hits:
            break
    if source_video:
        await _ingest(db, provider, quota, settings, project, query, source_video, out, now)

    query.status = QueryStatus.DONE
    query.last_run_at = now
    query.results_count = out.channels_found
    await db.flush()
    return out


async def _ingest(
    db: AsyncSession,
    provider: YouTubeProvider,
    quota: QuotaGuard,
    settings: Settings,
    project: SearchProject,
    query: SearchQuery,
    source_video: dict[str, str | None],
    out: QueryOutcome,
    now: datetime,
) -> None:
    # 2. channels: re-fetch only stale/unknown ones
    yt_ids = list(source_video)
    known = {
        r.youtube_channel_id: r
        for r in (
            await db.execute(
                select(Channel.id, Channel.youtube_channel_id, Channel.last_fetched_at).where(
                    Channel.youtube_channel_id.in_(yt_ids)
                )
            )
        ).all()
    }
    fresh_after = now - timedelta(hours=settings.youtube_channel_refresh_hours)
    to_fetch = [c for c in yt_ids if c not in known or not known[c].last_fetched_at
                or known[c].last_fetched_at < fresh_after]
    channel_pks: dict[str, int] = {c: known[c].id for c in yt_ids if c in known and c not in to_fetch}
    out.channels_fresh_skipped = len(channel_pks)

    fetched: list[ChannelInfo] = []
    for batch in _batches(to_fetch):
        await quota.reserve(LIST_COST)
        fetched.extend(await provider.get_channels(list(batch)))
    new_pks, out.channels_new = await _upsert_channels(db, fetched, provider.is_mock, now)
    channel_pks.update(new_pks)
    out.channels_fetched = len(fetched)

    # 3. videos: newest uploads of fetched channels + source videos not stored yet
    wanted: list[str] = []
    for info in fetched:
        if info.uploads_playlist_id:
            await quota.reserve(LIST_COST)
            wanted.extend(
                await provider.get_playlist_video_ids(info.uploads_playlist_id, project.videos_to_analyze)
            )
    sources = [v for v in source_video.values() if v]
    stored_sources = set(
        await db.scalars(select(Video.youtube_video_id).where(Video.youtube_video_id.in_(sources)))
    ) if sources else set()
    wanted.extend(v for v in sources if v not in stored_sources)
    wanted = list(dict.fromkeys(wanted))
    video_infos: list[VideoInfo] = []
    for batch in _batches(wanted):
        await quota.reserve(LIST_COST)
        video_infos.extend(await provider.get_videos(list(batch)))
    out.videos_upserted = await _upsert_videos(db, video_infos, channel_pks, now)
    await refresh_metrics(db, new_pks.values(), now)

    # 4. membership + provenance
    found = channel_pks
    out.channels_found = len(found)
    out.found_channel_ids = set(found.values())
    video_pks = dict(
        (
            await db.execute(
                select(Video.youtube_video_id, Video.id).where(Video.youtube_video_id.in_(sources))
            )
        ).tuples().all()
    ) if sources else {}
    method = (
        DiscoveryMethod.CHANNEL_SEARCH
        if query.search_type == SearchType.CHANNEL
        else DiscoveryMethod.KEYWORD_SEARCH
    )
    for yt_id, channel_pk in found.items():
        pc = insert(ProjectChannel).values(
            project_id=project.id, channel_id=channel_pk, first_discovered_at=now, last_discovered_at=now
        )
        inserted = await db.scalar(
            pc.on_conflict_do_update(
                index_elements=["project_id", "channel_id"],
                set_={
                    "last_discovered_at": now,
                    "discovery_count": ProjectChannel.discovery_count + 1,
                },
            ).returning(_INSERTED)
        )
        out.channels_new_in_project += int(bool(inserted))
        src = source_video.get(yt_id)
        await db.execute(
            insert(ChannelDiscovery)
            .values(
                project_id=project.id, channel_id=channel_pk, search_query_id=query.id,
                source_video_id=video_pks.get(src) if src else None, method=method, discovered_at=now,
            )
            .on_conflict_do_nothing()  # uq_channel_discoveries_source (NULLS NOT DISTINCT)
        )


# Errors that make every further query fail the same way: stop the job instead of marking each query failed.
JOB_STOPPING_CODES = frozenset({
    IntegrationErrorCode.QUOTA_EXCEEDED, IntegrationErrorCode.AUTH_EXPIRED, IntegrationErrorCode.FORBIDDEN,
    IntegrationErrorCode.NOT_CONFIGURED,
})
MAX_REPORTED_FAILURES = 50
_TOTAL_KEYS = (
    "hits", "channels_new", "channels_new_in_project", "channels_fetched", "channels_fresh_skipped",
    "videos_upserted",
)


async def run_discovery(
    sessions: Callable[[], AsyncSession],
    provider: YouTubeProvider,
    quota: QuotaGuard,
    settings: Settings,
    *,
    job_run_id: int,
    workspace_id: int | None,
    params: dict[str, Any],
    progress: Callable[[int, int], Awaitable[None]],
) -> dict[str, Any]:
    """Worker body of a ``discovery`` job. Each query is committed on its own, so a retried job resumes
    where it stopped: queries finished after the job was created are skipped.

    * retryable provider error → the query goes back to PENDING and the error propagates (job retry);
    * quota / credentials error → same, but the job fails (non-retryable) with a human-readable message;
    * other permanent error → the query is marked FAILED, the job continues with the next one.
    """
    project_id = int(params["project_id"])
    query_ids = [int(q) for q in params.get("query_ids", [])]
    async with sessions() as db:
        job_created = await db.scalar(select(JobRun.created_at).where(JobRun.id == job_run_id))
        project = await _project(db, workspace_id, project_id)
    if project is None:
        raise AppError("The project of this discovery job no longer exists", code="project_missing")

    totals = dict.fromkeys(("queries_done", "queries_skipped", "queries_failed", *_TOTAL_KEYS), 0)
    found: set[int] = set()
    failures: list[dict[str, Any]] = []
    for done, query_id in enumerate(query_ids, start=1):
        async with sessions() as db:
            query = await db.scalar(
                select(SearchQuery).where(SearchQuery.id == query_id, SearchQuery.project_id == project_id)
            )
            if query is None or (job_created and query.last_run_at and query.last_run_at >= job_created):
                totals["queries_skipped"] += 1  # deleted meanwhile, or finished by an earlier attempt
                await progress(done, len(query_ids))
                continue
            query.status = QueryStatus.RUNNING
            await db.commit()

        try:
            async with sessions() as db:
                project_row = await db.get(SearchProject, project_id)
                query_row = await db.get(SearchQuery, query_id)
                if project_row is None or query_row is None:
                    raise AppError("Project or query deleted during discovery", code="project_missing")
                outcome = await run_query(db, provider, quota, settings, project_row, query_row)
                await db.commit()
        except IntegrationError as exc:
            stop = exc.retryable or exc.code in JOB_STOPPING_CODES
            await _set_query_status(sessions, query_id, QueryStatus.PENDING if stop else QueryStatus.FAILED)
            if stop:
                raise
            totals["queries_failed"] += 1
            if len(failures) < MAX_REPORTED_FAILURES:
                failures.append({"query_id": query_id, "error": exc.human_message})
        except BaseException:
            await _set_query_status(sessions, query_id, QueryStatus.PENDING)
            raise
        else:
            totals["queries_done"] += 1
            for key in _TOTAL_KEYS:
                totals[key] += getattr(outcome, key)
            found |= outcome.found_channel_ids
        await progress(done, len(query_ids))

    return {
        **totals, "channels_found": len(found), "quota_units_spent": quota.spent,
        "provider": provider.name, "failures": failures,
    }


async def _set_query_status(sessions: Callable[[], AsyncSession], query_id: int, status: QueryStatus) -> None:
    async with sessions() as db:
        query = await db.get(SearchQuery, query_id)
        if query is not None and query.status == QueryStatus.RUNNING:
            query.status = status
            await db.commit()
