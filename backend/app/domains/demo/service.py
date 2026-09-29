"""Demo mode seed (Phase 1, item 9).

Everything created here is synthetic and labelled at several levels so it can never be mistaken for
real YouTube data:

- ``search_projects.is_demo`` / ``channels.is_demo`` = true (shown as a "demo" badge in the UI);
- YouTube IDs use the ``demo-`` prefix, which real channel (``UC…``) / video IDs never have
  (they cannot collide with real rows in the global ``channels`` / ``videos`` tables);
- titles start with ``[DEMO]``, ``raw = {"demo": true}``;
- thumbnails are generated locally, stamped with "DEMO", stored as ``ImageSource.DEMO``;
  ``original_url`` points to the reserved ``.invalid`` TLD, so nothing is ever downloaded;
- discoveries use ``DiscoveryMethod.DEMO``.

The seed is deterministic and idempotent: re-running it adds nothing. ``remove_demo`` deletes the
workspace's demo projects and every demo channel no longer referenced by any project.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import delete, exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.domains.identity.service import audit
from app.domains.media.models import FetchStatus, ImageAsset, ImageSource, Thumbnail
from app.domains.media.service import store_image
from app.domains.projects.models import (
    ChannelDiscovery,
    DiscoveryMethod,
    ProjectChannel,
    SearchProject,
    SearchQuery,
)
from app.domains.projects.service import clean_query, normalize_query
from app.domains.youtube.models import Channel, Video
from app.providers.storage.base import StorageBackend

DEMO_ID_PREFIX = "demo-"
DEMO_TITLE_PREFIX = "[DEMO] "
DEMO_THUMBNAIL_HOST = "demo-thumbnails.invalid"
VIDEOS_PER_CHANNEL = 12


@dataclass(frozen=True)
class _ChannelSpec:
    key: str
    title: str
    country: str
    language: str
    subscribers: int


@dataclass(frozen=True)
class _ProjectSpec:
    name: str
    description: str
    language: str
    region_code: str
    queries: tuple[str, ...]
    channels: tuple[_ChannelSpec, ...]


_PROJECTS: tuple[_ProjectSpec, ...] = (
    _ProjectSpec(
        name="[DEMO] Технообзоры",
        description="Демо-проект: синтетические каналы и видео, не из YouTube.",
        language="ru",
        region_code="RU",
        queries=("обзор смартфонов", "распаковка ноутбука", "лучшие наушники 2026"),
        channels=(
            _ChannelSpec("tech-01", "Гаджет Лаб", "RU", "ru", 184_000),
            _ChannelSpec("tech-02", "Обзорщик Пётр", "RU", "ru", 42_500),
            _ChannelSpec("tech-03", "Tech Unboxed Daily", "US", "en", 1_250_000),
            _ChannelSpec("tech-04", "Железо и Код", "BY", "ru", 8_900),
            _ChannelSpec("tech-05", "Смарт Тест", "KZ", "ru", 310_000),
            _ChannelSpec("shared-01", "Всё о технологиях и кухне", "RU", "ru", 67_000),
        ),
    ),
    _ProjectSpec(
        name="[DEMO] Кулинария",
        description="Демо-проект: синтетические каналы и видео, не из YouTube.",
        language="ru",
        region_code="RU",
        queries=("рецепты выпечки", "ужин за 15 минут", "домашний хлеб"),
        channels=(
            _ChannelSpec("food-01", "Кухня Марины", "RU", "ru", 520_000),
            _ChannelSpec("food-02", "Хлеб и Соль", "RU", "ru", 15_300),
            _ChannelSpec("food-03", "Easy Dinner Club", "GB", "en", 98_000),
            _ChannelSpec("food-04", "Выпечка без хлопот", "UA", "ru", 3_400),
            _ChannelSpec("food-05", "Шеф на минималках", "RU", "ru", 2_050_000),
            # Also in the tech project: exercises many-to-many channel membership.
            _ChannelSpec("shared-01", "Всё о технологиях и кухне", "RU", "ru", 67_000),
        ),
    ),
)

_VIDEO_TOPICS = (
    "Честный обзор", "5 ошибок новичков", "Сравнение: что выбрать?", "Месяц спустя",
    "Разбор по шагам", "Топ-10", "Я был неправ", "Быстрый гайд", "Эксперимент",
    "Ответы на вопросы", "Бюджетный вариант", "Итоги года",
)


@dataclass
class DemoSeedResult:
    projects_created: int = 0
    channels_created: int = 0
    videos_created: int = 0
    thumbnails_stored: int = 0

    @property
    def created_anything(self) -> bool:
        return bool(self.projects_created or self.channels_created or self.videos_created
                    or self.thumbnails_stored)


@dataclass
class DemoRemoveResult:
    projects_deleted: int = 0
    channels_deleted: int = 0
    images_deleted: int = 0
    storage_keys: list[str] = field(default_factory=list)


def _num(key: str, lo: int, hi: int) -> int:
    """Deterministic pseudo-random integer in [lo, hi] derived from ``key`` (no RNG state)."""
    return lo + int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big") % (hi - lo + 1)


def demo_channel_id(key: str) -> str:
    return f"{DEMO_ID_PREFIX}ch-{key}"


def demo_video_id(channel_key: str, n: int) -> str:
    return f"{DEMO_ID_PREFIX}v-{channel_key}-{n:02d}"


def demo_thumbnail_bytes(video_key: str, size: tuple[int, int] = (480, 270)) -> bytes:
    """Deterministic JPEG with a visible "DEMO" stamp (same key => same bytes => one image asset)."""
    h = hashlib.sha256(video_key.encode()).digest()
    w, hgt = size
    im = Image.new("RGB", size, (h[0], h[1], h[2]))
    draw = ImageDraw.Draw(im)
    for i in range(4):  # a few blocks so perceptual hashes differ between videos
        x, y = h[3 + i] * w // 256, h[7 + i] * hgt // 256
        draw.rectangle((x, y, x + w // 3, y + hgt // 3), fill=(h[11 + i], h[15 + i], h[19 + i]))
    font = ImageFont.load_default(size=hgt // 5)
    draw.rectangle((0, 0, w * 2 // 5, hgt // 4), fill=(20, 20, 20))
    draw.text((w // 40, hgt // 40), "DEMO", fill=(255, 255, 255), font=font)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


async def _upsert_project(
    db: AsyncSession, workspace_id: int, spec: _ProjectSpec
) -> tuple[SearchProject, bool]:
    project = await db.scalar(
        select(SearchProject).where(
            SearchProject.workspace_id == workspace_id, SearchProject.name == spec.name
        )
    )
    if project is not None:
        if not project.is_demo:
            raise ConflictError(
                f"A non-demo project named '{spec.name}' already exists; rename it before seeding demo data",
                code="demo_name_taken",
            )
        return project, False
    project = SearchProject(
        workspace_id=workspace_id,
        name=spec.name,
        description=spec.description,
        language=spec.language,
        region_code=spec.region_code,
        is_demo=True,
    )
    db.add(project)
    await db.flush()
    return project, True


async def _upsert_channel(db: AsyncSession, spec: _ChannelSpec, now: datetime) -> tuple[Channel, bool]:
    yt_id = demo_channel_id(spec.key)
    created = await db.scalar(
        insert(Channel)
        .values(
            youtube_channel_id=yt_id,
            handle=f"@demo-{spec.key}",
            title=DEMO_TITLE_PREFIX + spec.title,
            description="Синтетический канал для демо-режима. Не существует на YouTube.",
            country=spec.country,
            default_language=spec.language,
            published_at=now - timedelta(days=_num(yt_id + "age", 400, 3000)),
            subscriber_count=spec.subscribers,
            view_count=spec.subscribers * _num(yt_id + "views", 40, 400),
            video_count=_num(yt_id + "videos", 60, 900),
            keywords=["demo"],
            last_fetched_at=now,
            is_demo=True,
            raw={"demo": True},
        )
        .on_conflict_do_nothing(index_elements=["youtube_channel_id"])
        .returning(Channel.id)
    )
    channel = await db.scalar(select(Channel).where(Channel.youtube_channel_id == yt_id))
    assert channel is not None
    return channel, created is not None


async def _seed_videos(
    db: AsyncSession, storage: StorageBackend, channel: Channel, spec: _ChannelSpec, now: datetime,
    result: DemoSeedResult,
) -> None:
    for n in range(1, VIDEOS_PER_CHANNEL + 1):
        yt_id = demo_video_id(spec.key, n)
        views = max(100, spec.subscribers * _num(yt_id + "v", 5, 250) // 100)
        video_pk = await db.scalar(
            insert(Video)
            .values(
                channel_id=channel.id,
                youtube_video_id=yt_id,
                title=f"{DEMO_TITLE_PREFIX}{_VIDEO_TOPICS[(n - 1) % len(_VIDEO_TOPICS)]} · {spec.title} #{n}",
                description="Синтетическое видео для демо-режима.",
                published_at=now - timedelta(
                    days=n * _num(yt_id + "gap", 3, 12), hours=_num(yt_id + "h", 0, 23)
                ),
                duration_seconds=_num(yt_id + "dur", 240, 1800),
                is_short=False,
                tags=["demo"],
                view_count=views,
                like_count=views * _num(yt_id + "l", 1, 6) // 100,
                comment_count=views * _num(yt_id + "c", 1, 20) // 1000,
                last_fetched_at=now,
                raw={"demo": True},
            )
            .on_conflict_do_nothing(index_elements=["youtube_video_id"])
            .returning(Video.id)
        )
        if video_pk is None:
            continue  # already seeded
        result.videos_created += 1
        asset = await store_image(db, storage, demo_thumbnail_bytes(yt_id), ImageSource.DEMO)
        db.add(
            Thumbnail(
                video_id=video_pk,
                image_asset_id=asset.id,
                original_url=f"https://{DEMO_THUMBNAIL_HOST}/vi/{yt_id}/hqdefault.jpg",
                fetch_status=FetchStatus.OK,
                fetched_at=now,
            )
        )
        result.thumbnails_stored += 1
    await db.flush()


async def seed_demo(db: AsyncSession, storage: StorageBackend, workspace_id: int) -> DemoSeedResult:
    """Create (or complete) the demo dataset in a workspace. Idempotent; the caller commits."""
    now = datetime.now(UTC)
    result = DemoSeedResult()
    for pspec in _PROJECTS:
        project, created = await _upsert_project(db, workspace_id, pspec)
        result.projects_created += int(created)
        await db.execute(
            insert(SearchQuery)
            .values([
                {"project_id": project.id, "text": clean_query(q), "text_normalized": normalize_query(q)}
                for q in pspec.queries
            ])
            .on_conflict_do_nothing(index_elements=["project_id", "text_normalized"])
        )
        for cspec in pspec.channels:
            channel, ch_created = await _upsert_channel(db, cspec, now)
            if ch_created:
                result.channels_created += 1
                await _seed_videos(db, storage, channel, cspec, now, result)
            await db.execute(
                insert(ProjectChannel)
                .values(project_id=project.id, channel_id=channel.id)
                .on_conflict_do_nothing(index_elements=["project_id", "channel_id"])
            )
            await db.execute(
                insert(ChannelDiscovery)
                .values(project_id=project.id, channel_id=channel.id, method=DiscoveryMethod.DEMO)
                .on_conflict_do_nothing()  # uq_channel_discoveries_source (NULLS NOT DISTINCT)
            )
    await db.flush()
    if result.created_anything:
        await audit(
            db, "demo.seeded", workspace_id=workspace_id, entity_type="workspace", entity_id=workspace_id,
            diff={
                "projects": result.projects_created, "channels": result.channels_created,
                "videos": result.videos_created, "thumbnails": result.thumbnails_stored,
            },
        )
    return result


async def remove_demo(db: AsyncSession, storage_name: str, workspace_id: int) -> DemoRemoveResult:
    """Delete the workspace's demo projects, then demo channels (with videos/thumbnails) that no project
    references any more, then demo image assets no thumbnail references. Real data is never touched:
    every delete is restricted to ``is_demo`` rows / ``ImageSource.DEMO`` assets.

    The caller commits and only then calls ``purge_storage_files`` with ``result.storage_keys``
    (a leftover file is harmless, a row pointing at a deleted file is not)."""
    result = DemoRemoveResult()
    deleted_projects = await db.scalars(
        delete(SearchProject)
        .where(SearchProject.workspace_id == workspace_id, SearchProject.is_demo.is_(True))
        .returning(SearchProject.id)
    )
    result.projects_deleted = len(deleted_projects.all())

    orphan_channels = await db.scalars(
        delete(Channel)
        .where(
            Channel.is_demo.is_(True),
            ~exists(select(ProjectChannel.channel_id).where(ProjectChannel.channel_id == Channel.id)),
        )
        .returning(Channel.id)
    )
    result.channels_deleted = len(orphan_channels.all())

    orphan_assets = (
        await db.execute(
            delete(ImageAsset)
            .where(
                ImageAsset.source == ImageSource.DEMO,
                ~exists(select(Thumbnail.id).where(Thumbnail.image_asset_id == ImageAsset.id)),
            )
            .returning(ImageAsset.storage_backend, ImageAsset.storage_key)
        )
    ).all()
    result.images_deleted = len(orphan_assets)
    result.storage_keys = [key for backend, key in orphan_assets if backend == storage_name]
    await db.flush()

    if result.projects_deleted or result.channels_deleted or result.images_deleted:
        await audit(
            db, "demo.removed", workspace_id=workspace_id, entity_type="workspace", entity_id=workspace_id,
            diff={
                "projects": result.projects_deleted, "channels": result.channels_deleted,
                "images": result.images_deleted,
            },
        )
    return result


async def purge_storage_files(db: AsyncSession, storage: StorageBackend, keys: list[str]) -> int:
    """Delete files whose key no ``image_assets`` row references (run after the removal is committed).

    Keys are content-addressed, so a re-seed in the same transaction (``--reset``) may have re-created
    an asset with the same key — such files are kept."""
    if not keys:
        return 0
    still_used = set(await db.scalars(select(ImageAsset.storage_key).where(ImageAsset.storage_key.in_(keys))))
    removed = 0
    for key in keys:
        if key not in still_used:
            storage.delete(key)
            removed += 1
    return removed
