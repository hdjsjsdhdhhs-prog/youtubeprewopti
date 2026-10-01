"""Image ingestion with content-addressed deduplication (ADR-0006, §34)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import ColumnElement, and_, exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, IntegrationError
from app.domains.identity.service import audit
from app.domains.jobs.service import Enqueued, JobQueue, JobType, enqueue_job
from app.domains.media.imaging import (
    METRICS_ALGO_VERSION,
    InvalidImageError,
    compute_image_metrics,
    inspect_image,
    phash,
)
from app.domains.media.models import FetchStatus, ImageAsset, ImageMetrics, ImageSource, Thumbnail
from app.domains.projects.models import ProjectChannel, ProjectStatus, SearchProject
from app.domains.projects.service import get_project
from app.domains.youtube.models import Video
from app.domains.youtube.service import get_visible_channel
from app.providers.storage.base import StorageBackend, sha256_hex
from app.providers.thumbnails.base import ThumbnailFetcher

MAX_VIDEOS_PER_THUMBNAIL_JOB = 1000
MAX_VIDEOS_PER_PROJECT_JOB = 5000


async def store_image(
    db: AsyncSession, storage: StorageBackend, data: bytes, source: ImageSource
) -> ImageAsset:
    """Validate, store once per SHA-256 and return the (possibly pre-existing) ImageAsset.

    Raises ``InvalidImageError`` for non-images / unsupported formats.
    """
    digest = sha256_hex(data)
    existing = await db.scalar(select(ImageAsset).where(ImageAsset.sha256 == digest))
    if existing is not None:
        return existing

    info = inspect_image(data)
    stored = storage.put(data, info.ext)
    await db.execute(
        insert(ImageAsset)
        .values(
            sha256=digest,
            phash=phash(data),
            storage_backend=storage.name,
            storage_key=stored.key,
            mime=info.mime,
            format=info.ext,
            width=info.width,
            height=info.height,
            bytes=len(data),
            source=source,
        )
        .on_conflict_do_nothing(index_elements=["sha256"])  # concurrent ingestion of the same bytes
    )
    asset = await db.scalar(select(ImageAsset).where(ImageAsset.sha256 == digest))
    assert asset is not None
    return asset


async def asset_visible_to_workspace(db: AsyncSession, asset_id: int, workspace_id: int) -> bool:
    """An asset is visible if it is the thumbnail of a video whose channel is in one of the workspace's
    projects. Generated/reference assets will add their own ownership paths (Phase 5)."""
    via_thumbnail = (
        select(Thumbnail.id)
        .join(Video, Video.id == Thumbnail.video_id)
        .join(ProjectChannel, ProjectChannel.channel_id == Video.channel_id)
        .join(SearchProject, SearchProject.id == ProjectChannel.project_id)
        .where(Thumbnail.image_asset_id == asset_id, SearchProject.workspace_id == workspace_id)
    )
    return bool(await db.scalar(select(exists(via_thumbnail))))


class DownloadOutcome(StrEnum):
    DOWNLOADED = "downloaded"
    SKIPPED = "skipped"  # already downloaded (idempotent re-run)
    FAILED = "failed"  # permanent problem, recorded on the thumbnail
    MISSING = "missing"  # video/thumbnail row no longer exists


async def download_thumbnail(
    db: AsyncSession, storage: StorageBackend, fetcher: ThumbnailFetcher, video_id: int
) -> tuple[DownloadOutcome, str | None]:
    """Download and store one video's thumbnail. Caller commits.

    Retryable provider errors propagate (the job is retried); permanent ones are stored on the
    thumbnail row as ``fetch_status=failed`` and reported as ``FAILED``.
    """
    thumb = await db.scalar(select(Thumbnail).where(Thumbnail.video_id == video_id).with_for_update())
    if thumb is None:
        return DownloadOutcome.MISSING, None
    if thumb.fetch_status == FetchStatus.OK and thumb.image_asset_id is not None:
        return DownloadOutcome.SKIPPED, None
    try:
        data = await fetcher.fetch(thumb.original_url)
        asset = await store_image(db, storage, data, fetcher.image_source)
    except IntegrationError as exc:
        if exc.retryable:
            raise
        error = exc.human_message
    except InvalidImageError as exc:
        error = f"Downloaded file is not a supported image: {exc}"
    else:
        thumb.image_asset_id = asset.id
        thumb.fetch_status = FetchStatus.OK
        thumb.fetched_at = datetime.now(UTC)
        thumb.error = None
        await db.flush()
        return DownloadOutcome.DOWNLOADED, None

    thumb.fetch_status = FetchStatus.FAILED
    thumb.fetched_at = datetime.now(UTC)
    thumb.error = error
    await db.flush()
    return DownloadOutcome.FAILED, error


class MetricsOutcome(StrEnum):
    COMPUTED = "computed"
    CURRENT = "current"  # already computed with the current algorithm version
    FAILED = "failed"  # file missing from storage / undecodable
    NO_IMAGE = "no_image"  # thumbnail not stored (download failed or row missing)


async def ensure_image_metrics(
    db: AsyncSession, storage: StorageBackend, asset: ImageAsset, data: bytes | None = None
) -> MetricsOutcome:
    """Compute (or recompute after an algorithm change) the asset's deterministic metrics. Caller commits."""
    current = await db.scalar(
        select(ImageMetrics.algo_version).where(ImageMetrics.image_asset_id == asset.id)
    )
    if current == METRICS_ALGO_VERSION:
        return MetricsOutcome.CURRENT
    if data is None:
        if asset.storage_backend != storage.name or not storage.exists(asset.storage_key):
            return MetricsOutcome.FAILED
        data = storage.get(asset.storage_key)
    try:
        values = compute_image_metrics(data)
    except InvalidImageError:
        return MetricsOutcome.FAILED
    row = {"algo_version": METRICS_ALGO_VERSION, "computed_at": func.now(), **values.as_row()}
    stmt = insert(ImageMetrics).values(image_asset_id=asset.id, **row)
    await db.execute(stmt.on_conflict_do_update(index_elements=["image_asset_id"], set_=row))
    return MetricsOutcome.COMPUTED


@dataclass(frozen=True)
class IngestResult:
    download: DownloadOutcome
    error: str | None
    metrics: MetricsOutcome


async def ingest_thumbnail(
    db: AsyncSession, storage: StorageBackend, fetcher: ThumbnailFetcher, video_id: int
) -> IngestResult:
    """Download the video's thumbnail if needed, then make sure its metrics are current. Caller commits."""
    outcome, error = await download_thumbnail(db, storage, fetcher, video_id)
    if outcome not in (DownloadOutcome.DOWNLOADED, DownloadOutcome.SKIPPED):
        return IngestResult(outcome, error, MetricsOutcome.NO_IMAGE)
    asset = await db.scalar(
        select(ImageAsset).join(Thumbnail, Thumbnail.image_asset_id == ImageAsset.id)
        .where(Thumbnail.video_id == video_id)
    )
    if asset is None:
        return IngestResult(outcome, error, MetricsOutcome.NO_IMAGE)
    return IngestResult(outcome, error, await ensure_image_metrics(db, storage, asset))


def needs_ingestion() -> ColumnElement[bool]:
    """Thumbnail not stored yet, or stored without metrics of the current algorithm version."""
    current_metrics = exists(
        select(ImageMetrics.image_asset_id).where(
            ImageMetrics.image_asset_id == Thumbnail.image_asset_id,
            ImageMetrics.algo_version == METRICS_ALGO_VERSION,
        ).correlate(Thumbnail)
    )
    return or_(
        Thumbnail.fetch_status != FetchStatus.OK,
        Thumbnail.image_asset_id.is_(None),
        ~current_metrics,
    )


async def _enqueue_ingestion(
    db: AsyncSession, workspace_id: int, actor_id: int, video_ids: list[int], scope: dict[str, Any]
) -> Enqueued:
    enq = await enqueue_job(
        db,
        type_=JobType.THUMBNAIL_DOWNLOAD,
        queue=JobQueue.BULK,
        params={"video_ids": video_ids, **scope},
        workspace_id=workspace_id,
        created_by=actor_id,
        progress_total=len(video_ids),
    )
    if enq.created:
        await audit(
            db, "job.enqueued", workspace_id=workspace_id, actor_user_id=actor_id, entity_type="job_run",
            entity_id=enq.job.id, diff={"type": enq.job.type, **scope, "videos": len(video_ids)},
        )
    return enq


async def enqueue_channel_thumbnails(
    db: AsyncSession, workspace_id: int, actor_id: int, channel_id: int
) -> tuple[Enqueued | None, int]:
    """Queue downloads (+ metrics) for the channel's thumbnails that are not fully ingested yet.

    Returns ``(None, 0)`` when there is nothing to do.
    """
    await get_visible_channel(db, workspace_id, channel_id)  # 404 for other workspaces
    video_ids = list(
        await db.scalars(
            select(Video.id)
            .join(Thumbnail, Thumbnail.video_id == Video.id)
            .where(Video.channel_id == channel_id, needs_ingestion())
            .order_by(Video.id)
            .limit(MAX_VIDEOS_PER_THUMBNAIL_JOB)
        )
    )
    if not video_ids:
        return None, 0
    enq = await _enqueue_ingestion(db, workspace_id, actor_id, video_ids, {"channel_id": channel_id})
    return enq, len(video_ids)


def _project_videos(project_id: int) -> ColumnElement[bool]:
    return exists(
        select(ProjectChannel.channel_id).where(
            ProjectChannel.project_id == project_id, ProjectChannel.channel_id == Video.channel_id
        ).correlate(Video)
    )


@dataclass(frozen=True)
class ProjectIngestion:
    enqueued: Enqueued | None
    items: int
    remaining: int  # still needing ingestion beyond this job's cap (start again after it finishes)


async def enqueue_project_thumbnails(
    db: AsyncSession, workspace_id: int, actor_id: int, project_id: int
) -> ProjectIngestion:
    """Bulk ingestion (§6 step 1): every not yet ingested thumbnail of the project's channels — newest
    videos first, at most ``MAX_VIDEOS_PER_PROJECT_JOB`` per job."""
    project = await get_project(db, workspace_id, project_id)
    if project.status == ProjectStatus.ARCHIVED:
        raise ConflictError("Project is archived; restore it to download thumbnails", code="project_archived")
    base = (
        select(Video.id)
        .join(Thumbnail, Thumbnail.video_id == Video.id)
        .where(_project_videos(project_id), needs_ingestion())
    )
    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    video_ids = list(
        await db.scalars(
            base.order_by(Video.published_at.desc().nulls_last(), Video.id).limit(MAX_VIDEOS_PER_PROJECT_JOB)
        )
    )
    if not video_ids:
        return ProjectIngestion(enqueued=None, items=0, remaining=0)
    video_ids.sort()  # canonical order => identical requests share one active job (fingerprint)
    enq = await _enqueue_ingestion(db, workspace_id, actor_id, video_ids, {"project_id": project_id})
    return ProjectIngestion(enqueued=enq, items=len(video_ids), remaining=max(0, total - len(video_ids)))


@dataclass(frozen=True)
class ThumbnailStats:
    videos: int
    with_thumbnail: int
    downloaded: int
    pending: int
    failed: int
    with_metrics: int  # downloaded and metrics of the current algorithm version


async def project_thumbnail_stats(db: AsyncSession, workspace_id: int, project_id: int) -> ThumbnailStats:
    await get_project(db, workspace_id, project_id)
    has_metrics = and_(
        ImageMetrics.image_asset_id.is_not(None), ImageMetrics.algo_version == METRICS_ALGO_VERSION
    )
    row = (
        await db.execute(
            select(
                func.count(Video.id),
                func.count(Thumbnail.id),
                func.count(Thumbnail.id).filter(Thumbnail.fetch_status == FetchStatus.OK),
                func.count(Thumbnail.id).filter(Thumbnail.fetch_status == FetchStatus.PENDING),
                func.count(Thumbnail.id).filter(Thumbnail.fetch_status == FetchStatus.FAILED),
                func.count(Thumbnail.id).filter(Thumbnail.fetch_status == FetchStatus.OK, has_metrics),
            )
            .select_from(Video)
            .outerjoin(Thumbnail, Thumbnail.video_id == Video.id)
            .outerjoin(ImageMetrics, ImageMetrics.image_asset_id == Thumbnail.image_asset_id)
            .where(_project_videos(project_id))
        )
    ).one()
    return ThumbnailStats(*row)
