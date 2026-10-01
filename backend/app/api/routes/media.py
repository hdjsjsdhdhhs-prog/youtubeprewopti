"""Image serving (ADR-0006): files are never exposed as static paths; every read checks workspace access."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import select

from app.api.deps import DbSession, ReadAuth, Storage, WriteAuth
from app.core.errors import NotFoundError
from app.domains.jobs.schemas import EnqueueResult, JobRunOut
from app.domains.media.imaging import METRICS_ALGO_VERSION
from app.domains.media.models import ImageAsset
from app.domains.media.schemas import ThumbnailIngestResult, ThumbnailStatsOut
from app.domains.media.service import (
    asset_visible_to_workspace,
    enqueue_channel_thumbnails,
    enqueue_project_thumbnails,
    project_thumbnail_stats,
)

router = APIRouter(tags=["media"])


@router.post(
    "/projects/{project_id}/thumbnails/download",
    response_model=ThumbnailIngestResult,
    status_code=status.HTTP_202_ACCEPTED,
)
async def download_project_thumbnails(
    project_id: int, ctx: WriteAuth, db: DbSession
) -> ThumbnailIngestResult:
    """Queue a ``thumbnail_download`` job for every thumbnail of the project's channels that is not
    downloaded yet or has no metrics of the current algorithm (newest videos first, capped per job —
    ``remaining`` tells how many are left for the next run). No YouTube API quota is used."""
    plan = await enqueue_project_thumbnails(db, ctx.workspace.id, ctx.user.id, project_id)
    if plan.enqueued is None:
        return ThumbnailIngestResult(job=None, created=False, items=0, remaining=0)
    return ThumbnailIngestResult(
        job=JobRunOut.model_validate(plan.enqueued.job), created=plan.enqueued.created, items=plan.items,
        remaining=plan.remaining,
    )


@router.get("/projects/{project_id}/thumbnails/stats", response_model=ThumbnailStatsOut)
async def project_thumbnails_stats(project_id: int, ctx: ReadAuth, db: DbSession) -> ThumbnailStatsOut:
    stats = await project_thumbnail_stats(db, ctx.workspace.id, project_id)
    return ThumbnailStatsOut(**stats.__dict__, metrics_algo_version=METRICS_ALGO_VERSION)


@router.post(
    "/channels/{channel_id}/thumbnails/download",
    response_model=EnqueueResult,
    status_code=status.HTTP_202_ACCEPTED,
)
async def download_channel_thumbnails(channel_id: int, ctx: WriteAuth, db: DbSession) -> EnqueueResult:
    """Queue a ``thumbnail_download`` job for the channel's not-yet-stored thumbnails.
    Repeating the call while that job is active returns the same job (``created=false``)."""
    enq, items = await enqueue_channel_thumbnails(db, ctx.workspace.id, ctx.user.id, channel_id)
    if enq is None:
        return EnqueueResult(job=None, created=False, items=0)
    return EnqueueResult(job=JobRunOut.model_validate(enq.job), created=enq.created, items=items)


@router.get("/images/{asset_id}", responses={200: {"content": {"image/*": {}}}})
async def get_image(
    asset_id: int, request: Request, ctx: ReadAuth, db: DbSession, storage: Storage
) -> Response:
    asset = await db.scalar(select(ImageAsset).where(ImageAsset.id == asset_id))
    if asset is None or not await asset_visible_to_workspace(db, asset.id, ctx.workspace.id):
        raise NotFoundError("Image not found")

    etag = f'"{asset.sha256}"'
    headers = {
        "ETag": etag,
        # content-addressed => immutable; "private" because access is per workspace
        "Cache-Control": "private, max-age=31536000, immutable",
        "X-Content-Type-Options": "nosniff",
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    if asset.storage_backend != storage.name or not storage.exists(asset.storage_key):
        raise NotFoundError("Image file is missing from storage", code="image_missing")
    return Response(content=storage.get(asset.storage_key), media_type=asset.mime, headers=headers)
