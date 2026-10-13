"""AI thumbnail audit job (Phase 3.5, AI_PIPELINE: ``thumbnail_audit``).

For every distinct image of the confirmed selection:

1. **Cache** (§34): an audit of this image for the same prompt version and one of the route's models
   exists => reused, no call.
2. **pHash near-duplicate**: an audit of a visually identical image (Hamming distance ≤
   ``YTL_ANALYSIS_PHASH_MAX_DISTANCE``) => copied with ``reused_from_id``, no call.
3. Otherwise one vision call through ``AIRunner`` (budget check, ``ai_calls`` row, schema validation with
   one repair, model fallback) and the validated audit is stored.

Each image is committed on its own: a retried job resumes where it stopped (done images hit the cache).
Invalid output / refusals are reported per image and do not fail the job; budget stops and provider
errors do (already stored audits are kept).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, and_, cast, func, literal, select, update
from sqlalchemy.dialects.postgresql import BIT, insert
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
from app.domains.analysis.audit import SUBSCORES, ThumbnailAudit
from app.domains.analysis.models import AILevel, ThumbnailAnalysis
from app.domains.media.imaging import METRICS_ALGO_VERSION, InvalidImageError, prepare_for_vision
from app.domains.media.models import ImageAsset, ImageMetrics, Thumbnail
from app.domains.projects.models import ProjectChannel, ProjectNiche, TaxonomyNode
from app.domains.youtube.models import Channel, Video
from app.providers.ai import AIProvider, ImageDetail, ImageInput
from app.providers.storage import StorageBackend

Sessions = Callable[[], AbstractAsyncContextManager[AsyncSession]]
Progress = Callable[[int, int], Awaitable[None]]

THUMBNAIL_PROMPT = ("thumbnail_analysis", 1)
TASK = AITask.THUMBNAIL_ANALYSIS
MAX_REPORTED_FAILURES = 50
_Q = Decimal("0.01")

# Columns copied when a near-duplicate reuses an audit (everything the model produced).
AUDIT_COLUMNS: tuple[str, ...] = (
    *SUBSCORES,
    "has_face", "face_emotion", "text_present", "text_word_count_est", "focal_point",
    "background_separation", "clutter_level", "improvement_opportunity", "problems", "recommendations",
    "problem_codes", "visual_summary",
)


class AuditOutcome(StrEnum):
    ANALYZED = "analyzed"  # new AI call
    CACHED = "cached"  # same image already audited with this prompt version and model
    REUSED = "reused"  # visually identical image (pHash) already audited
    FAILED = "failed"  # invalid output after repair, refusal, missing/broken image file


def audit_values(a: ThumbnailAudit) -> dict[str, Any]:
    values: dict[str, Any] = {k: Decimal(str(getattr(a, k))).quantize(_Q) for k in SUBSCORES}
    values.update(
        has_face=a.has_face,
        face_emotion=a.face_emotion or None,
        text_present=a.text_present,
        text_word_count_est=a.text_word_count_est,
        focal_point=a.focal_point,
        background_separation=AILevel(a.background_separation),
        clutter_level=AILevel(a.clutter_level),
        improvement_opportunity=AILevel(a.improvement_opportunity),
        problems=[p.model_dump(mode="json") for p in a.problems],
        recommendations=[r.model_dump(mode="json") for r in a.recommendations],
        problem_codes=sorted({p.code.value for p in a.problems}),
        visual_summary=a.visual_summary,
    )
    return values


async def find_existing(
    db: AsyncSession, asset_id: int, template_id: int, model_keys: Sequence[str]
) -> ThumbnailAnalysis | None:
    return await db.scalar(
        select(ThumbnailAnalysis)
        .where(
            ThumbnailAnalysis.image_asset_id == asset_id,
            ThumbnailAnalysis.prompt_template_id == template_id,
            ThumbnailAnalysis.model_key.in_(model_keys),
        )
        .order_by(ThumbnailAnalysis.created_at.desc(), ThumbnailAnalysis.id.desc())
        .limit(1)
    )


async def find_near_duplicate(
    db: AsyncSession, asset: ImageAsset, template_id: int, model_keys: Sequence[str], max_distance: int
) -> tuple[ThumbnailAnalysis, int] | None:
    """Closest original (not itself reused) audit of another image within ``max_distance`` pHash bits."""
    if max_distance < 0:
        return None
    distance = func.bit_count(cast(ImageAsset.phash.op("#")(literal(asset.phash, BigInteger)), BIT(64)))
    row = (
        await db.execute(
            select(ThumbnailAnalysis, distance.label("distance"))
            .join(ImageAsset, ImageAsset.id == ThumbnailAnalysis.image_asset_id)
            .where(
                ThumbnailAnalysis.prompt_template_id == template_id,
                ThumbnailAnalysis.model_key.in_(model_keys),
                ThumbnailAnalysis.reused_from_id.is_(None),
                ThumbnailAnalysis.image_asset_id != asset.id,
                distance <= max_distance,
            )
            .order_by(distance, ThumbnailAnalysis.id)
            .limit(1)
        )
    ).first()
    return None if row is None else (row[0], int(row[1]))


async def make_current(db: AsyncSession, asset_id: int, analysis_id: int) -> None:
    """The latest run decides which audit of an image is shown (two statements: the partial unique
    index on ``is_current`` is checked row by row)."""
    await db.execute(
        update(ThumbnailAnalysis)
        .where(
            ThumbnailAnalysis.image_asset_id == asset_id,
            ThumbnailAnalysis.is_current,
            ThumbnailAnalysis.id != analysis_id,
        )
        .values(is_current=False)
    )
    await db.execute(
        update(ThumbnailAnalysis).where(ThumbnailAnalysis.id == analysis_id).values(is_current=True)
    )


async def save_analysis(
    db: AsyncSession,
    *,
    asset_id: int,
    template_id: int,
    provider: str,
    model_key: str,
    detail: str,
    values: dict[str, Any],
    ai_call_id: int | None = None,
    reused_from_id: int | None = None,
    phash_distance: int | None = None,
) -> int:
    """Insert (or, after a concurrent insert of the same key, take the existing row) and make it current."""
    new_id = await db.scalar(
        insert(ThumbnailAnalysis)
        .values(
            image_asset_id=asset_id, prompt_template_id=template_id, provider=provider, model_key=model_key,
            detail=detail, ai_call_id=ai_call_id, reused_from_id=reused_from_id,
            phash_distance=phash_distance, is_current=False, **values,
        )
        .on_conflict_do_nothing(index_elements=["image_asset_id", "prompt_template_id", "model_key"])
        .returning(ThumbnailAnalysis.id)
    )
    if new_id is None:
        new_id = await db.scalar(
            select(ThumbnailAnalysis.id).where(
                ThumbnailAnalysis.image_asset_id == asset_id,
                ThumbnailAnalysis.prompt_template_id == template_id,
                ThumbnailAnalysis.model_key == model_key,
            )
        )
        assert new_id is not None
    await make_current(db, asset_id, new_id)
    return new_id


async def _project_niches(db: AsyncSession, project_id: int) -> str:
    names = list(
        await db.scalars(
            select(TaxonomyNode.name)
            .join(ProjectNiche, ProjectNiche.taxonomy_node_id == TaxonomyNode.id)
            .where(ProjectNiche.project_id == project_id)
            .order_by(TaxonomyNode.name)
        )
    )
    return ", ".join(names) if names else "not specified"


async def _video_context(db: AsyncSession, project_id: int, asset_id: int) -> tuple[str, str]:
    """(video title, channel title) of the newest project video using this image."""
    row = (
        await db.execute(
            select(Video.title, Channel.title)
            .join(Thumbnail, Thumbnail.video_id == Video.id)
            .join(Channel, Channel.id == Video.channel_id)
            .join(
                ProjectChannel,
                and_(ProjectChannel.channel_id == Channel.id, ProjectChannel.project_id == project_id),
            )
            .where(Thumbnail.image_asset_id == asset_id)
            .order_by(Video.published_at.desc().nulls_last(), Video.id.desc())
            .limit(1)
        )
    ).first()
    return (row[0], row[1]) if row is not None else ("(unknown)", "(unknown)")


def metrics_text(m: ImageMetrics | None) -> str:
    """Deterministic metrics as objective context for the model (it must not contradict measurements)."""
    if m is None:
        return "- not computed"
    lines = [
        f"- mean brightness: {m.luminance_mean:.2f} (0 = black, 1 = white)",
        f"- RMS contrast: {m.contrast_rms:.3f} (0 = flat; above 0.25 is high)",
        f"- colorfulness: {m.colorfulness:.0f} (0 = grey; above 80 is very colourful)",
        f"- sharpness (Laplacian variance): {m.sharpness_laplacian:.0f} (below ~100 looks soft)",
        f"- edge density: {m.edge_density:.3f} (share of edge pixels; higher = busier)",
    ]
    if m.saliency_center_ratio is not None:
        lines.append(
            f"- detail in the central 50%x50% area: {m.saliency_center_ratio:.2f} (0.25 = evenly spread)"
        )
    colors = ", ".join(f"{c['hex']} {float(c['share']):.0%}" for c in (m.dominant_colors or [])[:5])
    if colors:
        lines.append(f"- dominant colours: {colors}")
    return "\n".join(lines)


class ThumbnailAuditJob:
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
        detail: ImageDetail,
    ) -> None:
        self.sessions = sessions
        self.provider = provider
        self.storage = storage
        self.settings = settings
        self.job_run_id = job_run_id
        self.workspace_id = workspace_id
        self.project_id = project_id
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
        self.template_id = (await self.runner.prompt(*THUMBNAIL_PROMPT, ThumbnailAudit)).template_id
        async with self.sessions() as db:
            self.niches = await _project_niches(db, self.project_id)

    async def audit_one(self, asset_id: int) -> tuple[AuditOutcome, str | None]:
        async with self.sessions() as db:
            existing = await find_existing(db, asset_id, self.template_id, self.model_keys)
            if existing is not None:
                await make_current(db, asset_id, existing.id)
                await db.commit()
                return AuditOutcome.CACHED, None
            asset = await db.scalar(select(ImageAsset).where(ImageAsset.id == asset_id))
            if asset is None:
                return AuditOutcome.FAILED, "The image no longer exists."
            near = await find_near_duplicate(
                db, asset, self.template_id, self.model_keys, self.settings.analysis_phash_max_distance
            )
            if near is not None:
                src, distance = near
                await save_analysis(
                    db, asset_id=asset_id, template_id=self.template_id, provider=src.provider,
                    model_key=src.model_key, detail=src.detail,
                    values={c: getattr(src, c) for c in AUDIT_COLUMNS}, ai_call_id=src.ai_call_id,
                    reused_from_id=src.id, phash_distance=distance,
                )
                await db.commit()
                return AuditOutcome.REUSED, None
            if asset.storage_backend != self.storage.name or not self.storage.exists(asset.storage_key):
                return AuditOutcome.FAILED, "The image file is missing from storage; download it again."
            data = self.storage.get(asset.storage_key)
            metrics = await db.scalar(
                select(ImageMetrics).where(
                    ImageMetrics.image_asset_id == asset_id, ImageMetrics.algo_version == METRICS_ALGO_VERSION
                )
            )
            measured = metrics_text(metrics)
            video_title, channel_title = await _video_context(db, self.project_id, asset_id)
            sha256 = asset.sha256
        try:
            image, mime, _ = prepare_for_vision(data)
        except InvalidImageError as exc:
            return AuditOutcome.FAILED, f"The stored image cannot be decoded: {exc}"

        try:
            res = await self.runner.run(
                task=TASK,
                schema=ThumbnailAudit,
                prompt=THUMBNAIL_PROMPT,
                variables={
                    "video_title": video_title, "channel_title": channel_title, "niches": self.niches,
                    "metrics": measured,
                },
                ctx=CallContext(
                    workspace_id=self.workspace_id, project_id=self.project_id, job_run_id=self.job_run_id,
                    input_ref={"image_asset_id": asset_id, "sha256": sha256},
                ),
                images=[ImageInput(image, mime)],
                detail=self.detail,
            )
        except (AIInvalidOutputError, AIRefusedError) as exc:
            return AuditOutcome.FAILED, exc.message
        async with self.sessions() as db:
            await save_analysis(
                db, asset_id=asset_id, template_id=self.template_id, provider=self.provider.name,
                model_key=res.model_key, detail=self.detail, values=audit_values(res.output),
                ai_call_id=res.call_id,
            )
            await db.commit()
        return AuditOutcome.ANALYZED, None


async def run_thumbnail_analysis(
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
    """params: ``{"project_id", "asset_ids": [...], "detail": "low" | "high", "model_key"}``."""
    asset_ids = [int(a) for a in params.get("asset_ids", [])]
    detail: ImageDetail = "high" if params.get("detail") == "high" else "low"
    job = ThumbnailAuditJob(
        sessions, provider, storage, settings, job_run_id=job_run_id, workspace_id=workspace_id,
        project_id=int(params["project_id"]), detail=detail,
    )
    await job.prepare()
    counts = {o.value: 0 for o in AuditOutcome}
    failures: list[dict[str, Any]] = []
    for done, asset_id in enumerate(asset_ids, start=1):
        outcome, error = await job.audit_one(asset_id)
        counts[outcome.value] += 1
        if error is not None and len(failures) < MAX_REPORTED_FAILURES:
            failures.append({"image_asset_id": asset_id, "error": error})
        await progress(done, len(asset_ids))
    return {
        **counts,
        "total": len(asset_ids),
        "provider": provider.name,
        "models": job.model_keys,
        "prompt": f"{THUMBNAIL_PROMPT[0]}/v{THUMBNAIL_PROMPT[1]}",
        "ai_calls": job.runner.calls,
        "spent_usd": str(job.runner.spent_usd),
        "failures": failures,
    }
