"""Small async factories for seeding test data (inside the per-test transaction)."""

from __future__ import annotations

import io
import itertools
from datetime import UTC, datetime, timedelta

import numpy as np
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.jobs.models import JobRun
from app.domains.media.models import FetchStatus, ImageAsset, Thumbnail
from app.domains.projects.models import ProjectChannel, SearchProject
from app.domains.youtube.models import Channel, Video

_seq = itertools.count(1)


def png_bytes(color: tuple[int, int, int] = (200, 30, 30), size: tuple[int, int] = (64, 36)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def noise_png(seed: int, *, tweak: bool = False, size: tuple[int, int] = (160, 90)) -> bytes:
    """Blocky random image with a distinct pHash per seed (solid colours all share pHash 0). ``tweak``
    changes one pixel: new SHA-256, (almost) the same pHash => a visually identical copy."""
    rng = np.random.default_rng(seed)
    blocks = rng.integers(0, 256, size=(9, 16, 3), dtype=np.uint8)
    img = Image.fromarray(blocks, "RGB").resize(size, Image.Resampling.NEAREST)
    if tweak:
        img.putpixel((0, 0), (1, 2, 3))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


async def project(db: AsyncSession, workspace_id: int, name: str | None = None) -> SearchProject:
    p = SearchProject(workspace_id=workspace_id, name=name or f"Project {next(_seq)}")
    db.add(p)
    await db.flush()
    return p


async def channel(db: AsyncSession, *, projects: list[SearchProject] = (), **kw) -> Channel:
    n = next(_seq)
    c = Channel(
        youtube_channel_id=kw.pop("youtube_channel_id", f"UC{n:022d}"),
        title=kw.pop("title", f"Channel {n}"),
        **kw,
    )
    db.add(c)
    await db.flush()
    for p in projects:
        db.add(ProjectChannel(project_id=p.id, channel_id=c.id))
    await db.flush()
    return c


async def video(
    db: AsyncSession, ch: Channel, *, days_ago: int = 1, asset: ImageAsset | None = None, **kw
) -> Video:
    n = next(_seq)
    v = Video(
        channel_id=ch.id,
        youtube_video_id=kw.pop("youtube_video_id", f"vid{n:08d}"),
        title=kw.pop("title", f"Video {n}"),
        published_at=datetime.now(UTC) - timedelta(days=days_ago),
        **kw,
    )
    db.add(v)
    await db.flush()
    db.add(
        Thumbnail(
            video_id=v.id,
            original_url=f"https://i.ytimg.com/vi/{v.youtube_video_id}/hqdefault.jpg",
            image_asset_id=asset.id if asset else None,
            fetch_status=FetchStatus.OK if asset else FetchStatus.PENDING,
        )
    )
    await db.flush()
    return v


async def job_run(db: AsyncSession, workspace_id: int, **kw) -> JobRun:
    j = JobRun(
        workspace_id=workspace_id,
        type=kw.pop("type", "thumbnail_download"),
        fingerprint=kw.pop("fingerprint", f"fp-{next(_seq)}"),
        queue=kw.pop("queue", "bulk"),
        **kw,
    )
    db.add(j)
    await db.flush()
    return j
