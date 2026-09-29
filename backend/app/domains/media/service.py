"""Image ingestion with content-addressed deduplication (ADR-0006, §34)."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import IntegrationError
from app.domains.identity.service import audit
from app.domains.jobs.service import Enqueued, JobQueue, JobType, enqueue_job
from app.domains.media.imaging import InvalidImageError, inspect_image, phash
from app.domains.media.models import FetchStatus, ImageAsset, ImageSource, Thumbnail
from app.domains.projects.models import ProjectChannel, SearchProject
from app.domains.youtube.models import Video
from app.domains.youtube.service import get_visible_channel
from app.providers.storage.base import StorageBackend, sha256_hex
from app.providers.thumbnails.base import ThumbnailFetcher

MAX_VIDEOS_PER_THUMBNAIL_JOB = 1000


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


async def enqueue_channel_thumbnails(
    db: AsyncSession, workspace_id: int, actor_id: int, channel_id: int
) -> tuple[Enqueued | None, int]:
    """Queue downloads for the channel's thumbnails that are not stored yet.

    Returns ``(None, 0)`` when there is nothing to download.
    """
    await get_visible_channel(db, workspace_id, channel_id)  # 404 for other workspaces
    video_ids = list(
        await db.scalars(
            select(Video.id)
            .join(Thumbnail, Thumbnail.video_id == Video.id)
            .where(Video.channel_id == channel_id, Thumbnail.fetch_status != FetchStatus.OK)
            .order_by(Video.id)
            .limit(MAX_VIDEOS_PER_THUMBNAIL_JOB)
        )
    )
    if not video_ids:
        return None, 0
    enq = await enqueue_job(
        db,
        type_=JobType.THUMBNAIL_DOWNLOAD,
        queue=JobQueue.BULK,
        params={"video_ids": video_ids, "channel_id": channel_id},
        workspace_id=workspace_id,
        created_by=actor_id,
        progress_total=len(video_ids),
    )
    if enq.created:
        await audit(
            db, "job.enqueued", workspace_id=workspace_id, actor_user_id=actor_id, entity_type="job_run",
            entity_id=enq.job.id,
            diff={"type": enq.job.type, "channel_id": channel_id, "videos": len(video_ids)},
        )
    return enq, len(video_ids)
