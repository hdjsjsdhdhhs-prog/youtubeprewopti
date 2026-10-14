"""Comparative AI channel audit job (Phase 3.6, AI_PIPELINE: ``channel_audit``).

Input of one channel = its newest ``videos_per_channel`` non-Shorts videos whose thumbnail has a current AI
audit (3.5), newest first. Fewer than ``MIN_THUMBNAILS`` => the channel is skipped (``not_enough``).

1. **Cache** (§34): an audit of this channel with the same ``inputs_digest`` (videos, images, thumbnail
   audits), prompt version and one of the route's models exists => made current, no call.
2. Otherwise one vision call with all thumbnails through ``AIRunner`` (budget check, ``ai_calls`` row,
   schema validation with one repair, model fallback); the validated audit is stored and made current.

Each channel is committed on its own: a retried job resumes where it stopped (done channels hit the cache).
Invalid output / refusals / missing files fail only that channel; budget stops and provider errors stop the
job (stored audits are kept).
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import and_, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domains.ai.registry import AITask
from app.domains.ai.runner import (
    AIInvalidOutputError,
    AINotConfiguredError,
    AIRefusedError,
    AIRunner,
    CallContext,
)
from app.domains.analysis.audit import DEFAULT_WEIGHTS, SUBSCORES, overall_score
from app.domains.analysis.channel_audit import MIN_THUMBNAILS, ChannelVisualAudit, map_indexes
from app.domains.analysis.models import AILevel, ChannelAnalysis, ThumbnailAnalysis
from app.domains.analysis.pipeline import project_niches
from app.domains.media.imaging import InvalidImageError, prepare_for_vision
from app.domains.media.models import ImageAsset, Thumbnail
from app.domains.projects.models import ProjectChannel
from app.domains.youtube.models import Channel, Video
from app.providers.ai import AIProvider, ImageDetail, ImageInput
from app.providers.storage import StorageBackend

Sessions = Callable[[], AbstractAsyncContextManager[AsyncSession]]
Progress = Callable[[int, int], Awaitable[None]]

CHANNEL_PROMPT = ("channel_analysis", 1)
TASK = AITask.CHANNEL_ANALYSIS
MAX_REPORTED_FAILURES = 50
_Q = Decimal("0.01")


class ChannelOutcome(StrEnum):
    ANALYZED = "analyzed"  # new AI call
    CACHED = "cached"  # same inputs already audited with this prompt version and model
    NOT_ENOUGH = "not_enough"  # fewer than MIN_THUMBNAILS audited thumbnails
    FAILED = "failed"  # invalid output after repair, refusal, missing/broken image file


@dataclass(frozen=True)
class InputVideo:
    video_id: int
    title: str
    published_at: datetime | None
    view_count: int | None
    asset: ImageAsset
    analysis: ThumbnailAnalysis


def inputs_digest(videos: Sequence[InputVideo]) -> str:
    """Identity of what the model is shown: videos (in order), their images and their thumbnail audits."""
    canonical = ";".join(f"{v.video_id}:{v.asset.sha256}:{v.analysis.id}" for v in videos)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


async def select_channel_inputs(
    db: AsyncSession, project_id: int, videos_per_channel: int, channel_ids: Sequence[int] | None = None
) -> dict[int, list[InputVideo]]:
    """Channels of the project with at least one audited thumbnail -> their newest audited videos."""
    conds = [Video.is_short.is_not(True)]
    if channel_ids is not None:
        conds.append(Video.channel_id.in_(list(channel_ids)))
    rank = func.row_number().over(
        partition_by=Video.channel_id, order_by=(Video.published_at.desc().nulls_last(), Video.id.desc())
    )
    ranked = (
        select(Video.id.label("video_id"), rank.label("rn"))
        .join(Thumbnail, Thumbnail.video_id == Video.id)
        .join(
            ThumbnailAnalysis,
            and_(ThumbnailAnalysis.image_asset_id == Thumbnail.image_asset_id, ThumbnailAnalysis.is_current),
        )
        .join(
            ProjectChannel,
            and_(ProjectChannel.channel_id == Video.channel_id, ProjectChannel.project_id == project_id),
        )
        .where(*conds)
        .subquery()
    )
    rows = await db.execute(
        select(Video, ImageAsset, ThumbnailAnalysis)
        .join(ranked, ranked.c.video_id == Video.id)
        .join(Thumbnail, Thumbnail.video_id == Video.id)
        .join(ImageAsset, ImageAsset.id == Thumbnail.image_asset_id)
        .join(
            ThumbnailAnalysis,
            and_(ThumbnailAnalysis.image_asset_id == ImageAsset.id, ThumbnailAnalysis.is_current),
        )
        .where(ranked.c.rn <= videos_per_channel)
        .order_by(Video.channel_id, ranked.c.rn)
    )
    out: dict[int, list[InputVideo]] = {}
    for video, asset, analysis in rows.tuples():
        out.setdefault(video.channel_id, []).append(
            InputVideo(video.id, video.title, video.published_at, video.view_count, asset, analysis)
        )
    return out


async def find_existing(
    db: AsyncSession, channel_id: int, digest: str, template_id: int, model_keys: Sequence[str]
) -> ChannelAnalysis | None:
    return await db.scalar(
        select(ChannelAnalysis)
        .where(
            ChannelAnalysis.channel_id == channel_id,
            ChannelAnalysis.inputs_digest == digest,
            ChannelAnalysis.prompt_template_id == template_id,
            ChannelAnalysis.model_key.in_(model_keys),
        )
        .order_by(ChannelAnalysis.created_at.desc(), ChannelAnalysis.id.desc())
        .limit(1)
    )


async def make_current(db: AsyncSession, channel_id: int, analysis_id: int) -> None:
    """Two statements: the partial unique index on ``is_current`` is checked row by row."""
    await db.execute(
        update(ChannelAnalysis)
        .where(
            ChannelAnalysis.channel_id == channel_id,
            ChannelAnalysis.is_current,
            ChannelAnalysis.id != analysis_id,
        )
        .values(is_current=False)
    )
    await db.execute(update(ChannelAnalysis).where(ChannelAnalysis.id == analysis_id).values(is_current=True))


def audit_values(a: ChannelVisualAudit, video_ids: list[int]) -> dict[str, Any]:
    weakest = map_indexes(a.weakest_video_indexes, video_ids)
    candidates: list[dict[str, Any]] = []
    for c in a.best_redesign_candidates:
        mapped = map_indexes([c.video_index], video_ids)
        if mapped and all(x["video_id"] != mapped[0] for x in candidates):
            candidates.append({"video_id": mapped[0], "why": c.why})
    return {
        "visual_consistency": Decimal(str(a.visual_consistency)).quantize(_Q),
        "repeated_template": a.repeated_template,
        "outdated_style_score": Decimal(str(a.outdated_style)).quantize(_Q),
        "has_design_system": a.has_design_system,
        "visual_opportunity": AILevel(a.visual_opportunity),
        "patterns": [p.model_dump(mode="json") for p in a.better_performing_patterns],
        "recurring_problems": [p.model_dump(mode="json") for p in a.recurring_problems],
        "recurring_problem_codes": sorted({p.code.value for p in a.recurring_problems}),
        "weakest_video_ids": weakest,
        "redesign_candidates": candidates,
        "summary": a.summary,
    }


async def save_analysis(
    db: AsyncSession,
    *,
    channel_id: int,
    videos: Sequence[InputVideo],
    digest: str,
    template_id: int,
    provider: str,
    model_key: str,
    detail: str,
    values: dict[str, Any],
    ai_call_id: int | None,
) -> int:
    """Insert (or, after a concurrent insert of the same key, take the existing row) and make it current."""
    new_id = await db.scalar(
        insert(ChannelAnalysis)
        .values(
            channel_id=channel_id, prompt_template_id=template_id, provider=provider, model_key=model_key,
            ai_call_id=ai_call_id, detail=detail, inputs_digest=digest, is_current=False,
            video_ids=[v.video_id for v in videos], thumbnail_analysis_ids=[v.analysis.id for v in videos],
            **values,
        )
        .on_conflict_do_nothing(constraint="uq_channel_analyses_cache_key")
        .returning(ChannelAnalysis.id)
    )
    if new_id is None:
        new_id = await db.scalar(
            select(ChannelAnalysis.id).where(
                ChannelAnalysis.channel_id == channel_id,
                ChannelAnalysis.inputs_digest == digest,
                ChannelAnalysis.prompt_template_id == template_id,
                ChannelAnalysis.model_key == model_key,
            )
        )
        assert new_id is not None
    await make_current(db, channel_id, new_id)
    return new_id


def _spaced(n: int | None) -> str:
    return f"{n:,}".replace(",", " ") if n is not None else "unknown"


def videos_text(videos: Sequence[InputVideo]) -> str:
    """The list the model compares (image N = Video N). The quality number uses the built-in weights: the
    channel audit is global and must not depend on one workspace's weights."""
    lines: list[str] = []
    for i, v in enumerate(videos, start=1):
        a = v.analysis
        quality = overall_score({k: getattr(a, k) for k in SUBSCORES}, DEFAULT_WEIGHTS)
        published = v.published_at.date().isoformat() if v.published_at else "unknown"
        views = _spaced(v.view_count)
        codes = ", ".join(a.problem_codes) or "none"
        lines.append(
            f'Video {i}: "{v.title}" | published {published} | views {views}\n'
            f"  individual audit: quality {quality}/10, redesign potential {a.redesign_potential}/10, "
            f"opportunity {a.improvement_opportunity.value}; problems: {codes}\n"
            f"  summary: {a.visual_summary}"
        )
    return "\n".join(lines)


class ChannelAuditJob:
    def __init__(
        self,
        sessions: Sessions,
        provider: AIProvider,
        storage: StorageBackend,
        settings: Settings,
        *,
        job_run_id: int | None,
        workspace_id: int | None,
        project_id: int,
        videos_per_channel: int,
        detail: ImageDetail,
    ) -> None:
        self.sessions = sessions
        self.provider = provider
        self.storage = storage
        self.settings = settings
        self.job_run_id = job_run_id
        self.workspace_id = workspace_id
        self.project_id = project_id
        self.videos_per_channel = videos_per_channel
        self.detail = detail
        self.runner = AIRunner(sessions, provider, settings)
        self.model_keys: list[str] = []
        self.template_id = 0
        self.niches = ""

    async def prepare(self) -> None:
        models = await self.runner.candidates(TASK)
        if not models:
            raise AINotConfiguredError(
                f"No enabled vision model for '{TASK.value}' with provider '{self.provider.name}'; "
                "check the AI provider settings and the model registry."
            )
        self.model_keys = [m.key for m in models]
        self.template_id = (await self.runner.prompt(*CHANNEL_PROMPT, ChannelVisualAudit)).template_id
        async with self.sessions() as db:
            self.niches = await project_niches(db, self.project_id)

    async def audit_one(self, channel_id: int) -> tuple[ChannelOutcome, str | None]:
        async with self.sessions() as db:
            selected = await select_channel_inputs(db, self.project_id, self.videos_per_channel, [channel_id])
            videos = selected.get(channel_id, [])
            if len(videos) < MIN_THUMBNAILS:
                return ChannelOutcome.NOT_ENOUGH, (
                    f"Only {len(videos)} audited thumbnail(s); at least {MIN_THUMBNAILS} are needed."
                )
            digest = inputs_digest(videos)
            existing = await find_existing(db, channel_id, digest, self.template_id, self.model_keys)
            if existing is not None:
                await make_current(db, channel_id, existing.id)
                await db.commit()
                return ChannelOutcome.CACHED, None
            channel = await db.scalar(select(Channel).where(Channel.id == channel_id))
            if channel is None:
                return ChannelOutcome.FAILED, "The channel no longer exists."
            title, subscribers = channel.title, channel.subscriber_count
        images: list[ImageInput] = []
        for v in videos:
            if v.asset.storage_backend != self.storage.name or not self.storage.exists(v.asset.storage_key):
                return ChannelOutcome.FAILED, (
                    f"The thumbnail of video {v.video_id} is missing from storage; download it again."
                )
            try:
                data, mime, _ = prepare_for_vision(self.storage.get(v.asset.storage_key))
            except InvalidImageError as exc:
                return ChannelOutcome.FAILED, f"The thumbnail of video {v.video_id} cannot be decoded: {exc}"
            images.append(ImageInput(data, mime))

        video_ids = [v.video_id for v in videos]
        try:
            res = await self.runner.run(
                task=TASK,
                schema=ChannelVisualAudit,
                prompt=CHANNEL_PROMPT,
                variables={
                    "channel_title": title,
                    "subscribers": _spaced(subscribers),
                    "niches": self.niches,
                    "videos": videos_text(videos),
                },
                ctx=CallContext(
                    workspace_id=self.workspace_id, project_id=self.project_id, job_run_id=self.job_run_id,
                    input_ref={"channel_id": channel_id, "video_ids": video_ids, "inputs_digest": digest},
                ),
                images=images,
                detail=self.detail,
            )
        except (AIInvalidOutputError, AIRefusedError) as exc:
            return ChannelOutcome.FAILED, exc.message
        async with self.sessions() as db:
            await save_analysis(
                db, channel_id=channel_id, videos=videos, digest=digest, template_id=self.template_id,
                provider=self.provider.name, model_key=res.model_key, detail=self.detail,
                values=audit_values(res.output, video_ids), ai_call_id=res.call_id,
            )
            await db.commit()
        return ChannelOutcome.ANALYZED, None


async def run_channel_analysis(
    sessions: Sessions,
    provider: AIProvider,
    storage: StorageBackend,
    settings: Settings,
    *,
    job_run_id: int | None,
    workspace_id: int | None,
    params: dict[str, Any],
    progress: Progress,
) -> dict[str, Any]:
    """params: ``{"project_id", "channel_ids": [...], "videos_per_channel", "detail", "model_key"}``."""
    channel_ids = [int(c) for c in params.get("channel_ids", [])]
    detail: ImageDetail = "high" if params.get("detail") == "high" else "low"
    job = ChannelAuditJob(
        sessions, provider, storage, settings, job_run_id=job_run_id, workspace_id=workspace_id,
        project_id=int(params["project_id"]), videos_per_channel=int(params["videos_per_channel"]),
        detail=detail,
    )
    await job.prepare()
    counts = {o.value: 0 for o in ChannelOutcome}
    failures: list[dict[str, Any]] = []
    for done, channel_id in enumerate(channel_ids, start=1):
        outcome, error = await job.audit_one(channel_id)
        counts[outcome.value] += 1
        if error is not None and len(failures) < MAX_REPORTED_FAILURES:
            failures.append({"channel_id": channel_id, "error": error})
        await progress(done, len(channel_ids))
    return {
        **counts,
        "total": len(channel_ids),
        "provider": provider.name,
        "models": job.model_keys,
        "prompt": f"{CHANNEL_PROMPT[0]}/v{CHANNEL_PROMPT[1]}",
        "ai_calls": job.runner.calls,
        "spent_usd": str(job.runner.spent_usd),
        "failures": failures,
    }
