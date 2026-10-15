"""``thumbnail_generation`` job (ТЗ §13, AI_ARCHITECTURE §7).

For every still pending variant of the request: one image call through ``AIRunner.generate_image`` (budget
reservation, ``ai_calls`` row, model fallback) with the current thumbnail / parent variant and the selected
references as input images, then the image is cropped to 16:9 1280×720 and stored content-addressed.
Each variant is committed on its own, so a retried job continues with the variants that are still pending.

* moderation refusal => the variant is ``refused`` (the job goes on);
* broken provider output => the variant is ``failed`` (the job goes on);
* budget / cost cap reached or a permanent provider error => remaining variants ``failed``, the job fails;
* a transient provider error => re-raised for a retry (pending variants stay pending) unless it is the last
  attempt.

At the end the lead leaves ``GENERATION_PENDING``: ``GENERATED`` if any variant is ready, else ``SELECTED``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError, IntegrationError
from app.core.state_machine import LeadStatus
from app.domains.ai.prompts import load_prompt
from app.domains.ai.runner import AIRefusedError, AIRunner, CallContext
from app.domains.generation.models import (
    GeneratedThumbnail,
    GenerationRequest,
    GenerationStatus,
    VariantStatus,
)
from app.domains.generation.service import GENERATION_PROMPT, TASK
from app.domains.leads.models import Lead, LeadStatusHistory
from app.domains.media.imaging import InvalidImageError, fit_thumbnail, prepare_for_vision
from app.domains.media.models import ImageAsset, ImageSource
from app.domains.media.service import store_image
from app.providers.ai import AIProvider, ImageInput
from app.providers.storage import StorageBackend

Sessions = Callable[[], AbstractAsyncContextManager[AsyncSession]]
Progress = Callable[[int, int], Awaitable[None]]
REASON_DONE = "auto: варианты превью сгенерированы"
REASON_NONE = "auto: ни один вариант превью не получен"


async def _input_images(db: AsyncSession, storage: StorageBackend, asset_ids: list[int]) -> list[ImageInput]:
    """Input images in the order the prompt refers to them (image 1 = current thumbnail / parent variant)."""
    by_id: dict[int, ImageInput] = {}
    for asset in await db.scalars(select(ImageAsset).where(ImageAsset.id.in_(asset_ids))):
        if asset.storage_backend != storage.name or not storage.exists(asset.storage_key):
            continue  # a missing file only weakens the input; the prompt still describes the job
        data, mime, _ = prepare_for_vision(storage.get(asset.storage_key))
        by_id[asset.id] = ImageInput(data, mime)
    return [by_id[a] for a in asset_ids if a in by_id]


async def _fail_pending(db: AsyncSession, request_id: int, message: str) -> None:
    await db.execute(
        update(GeneratedThumbnail)
        .where(
            GeneratedThumbnail.generation_request_id == request_id,
            GeneratedThumbnail.status == VariantStatus.PENDING,
        )
        .values(status=VariantStatus.FAILED, error=message)
    )


async def _finish(
    db: AsyncSession, request: GenerationRequest, status: GenerationStatus, error: str | None
) -> bool:
    """Close the request and move the lead out of GENERATION_PENDING. Returns «any variant ready»."""
    request.status = status
    request.error = error
    ready = await db.scalar(
        select(GeneratedThumbnail.id)
        .where(
            GeneratedThumbnail.generation_request_id == request.id,
            GeneratedThumbnail.status.in_(
                [
                    VariantStatus.READY,
                    VariantStatus.APPROVED,
                    VariantStatus.REJECTED,
                    VariantStatus.FINAL,
                ]
            ),
        )
        .limit(1)
    )
    target = LeadStatus.GENERATED if ready is not None else LeadStatus.SELECTED
    moved = await db.scalar(
        update(Lead)
        .where(Lead.id == request.lead_id, Lead.status == LeadStatus.GENERATION_PENDING)
        .values(status=target, status_changed_at=datetime.now(UTC))
        .returning(Lead.id)
    )
    if moved is not None:
        db.add(
            LeadStatusHistory(
                lead_id=request.lead_id,
                from_status=LeadStatus.GENERATION_PENDING,
                to_status=target,
                actor_user_id=None,
                reason=REASON_DONE if ready is not None else REASON_NONE,
            )
        )
    return ready is not None


async def run_thumbnail_generation(
    sessions: Sessions,
    provider: AIProvider,
    storage: StorageBackend,
    settings: Settings,
    *,
    job_run_id: int | None,
    workspace_id: int | None,
    params: dict[str, Any],
    progress: Progress,
    last_attempt: bool,
) -> dict[str, Any]:
    """params: ``{"generation_request_id", "lead_id", "project_id"}``."""
    request_id = int(params["generation_request_id"])
    runner = AIRunner(sessions, provider, settings)
    async with sessions() as db:
        request = await db.scalar(select(GenerationRequest).where(GenerationRequest.id == request_id))
        if request is None:
            return {"skipped": "generation request not found"}
        request.status = GenerationStatus.RUNNING
        prompt = await load_prompt(db, GENERATION_PROMPT[0], GENERATION_PROMPT[1], {})
        variants = list(
            await db.scalars(
                select(GeneratedThumbnail)
                .where(GeneratedThumbnail.generation_request_id == request_id)
                .order_by(GeneratedThumbnail.label)
            )
        )
        source_ids = variants[0].source_image_ids if variants else []
        try:
            inputs = await _input_images(db, storage, list(source_ids))
        except InvalidImageError:
            inputs = []
        model_key, size, quality, project_id = (
            request.model_key,
            request.size,
            request.quality,
            request.project_id,
        )
        await db.commit()

    counts = {s.value: 0 for s in (VariantStatus.READY, VariantStatus.FAILED, VariantStatus.REFUSED)}
    total = len(variants)
    for done, variant in enumerate(variants, start=1):
        if variant.status != VariantStatus.PENDING:  # finished in an earlier attempt
            counts[VariantStatus.READY.value if variant.image_asset_id else VariantStatus.FAILED.value] += 1
            await progress(done, total)
            continue
        values: dict[str, Any]
        try:
            res = await runner.generate_image(
                task=TASK,
                prompt=prompt,
                text=variant.prompt,
                references=inputs,
                model_key=model_key,
                size=None if size == "auto" else size,
                quality=None if quality == "auto" else quality,
                ctx=CallContext(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    job_run_id=job_run_id,
                    input_ref={
                        "generation_request_id": request_id,
                        "variant": variant.label,
                        "source_image_ids": list(source_ids),
                    },
                ),
            )
            image = fit_thumbnail(res.images[0].data)
            async with sessions() as db:
                asset = await store_image(db, storage, image, ImageSource.GENERATED)
                values = {
                    "status": VariantStatus.READY,
                    "image_asset_id": asset.id,
                    "ai_call_id": res.call_id,
                    "cost_usd": res.cost_usd,
                    "api_model_id": res.api_model_id,
                    "model_key": res.model_key,
                    "error": None,
                }
                await db.execute(
                    update(GeneratedThumbnail).where(GeneratedThumbnail.id == variant.id).values(**values)
                )
                await db.commit()
            counts[VariantStatus.READY.value] += 1
        except AIRefusedError as exc:
            await _set(sessions, variant.id, VariantStatus.REFUSED, exc.message)
            counts[VariantStatus.REFUSED.value] += 1
        except InvalidImageError as exc:
            await _set(
                sessions, variant.id, VariantStatus.FAILED, f"The model returned an unusable image: {exc}"
            )
            counts[VariantStatus.FAILED.value] += 1
        except (AppError, IntegrationError) as exc:
            transient = isinstance(exc, IntegrationError) and exc.retryable and not last_attempt
            if not transient:
                message = exc.message if isinstance(exc, AppError) else exc.human_message
                async with sessions() as db:
                    await _fail_pending(db, request_id, message)
                    req = await db.scalar(select(GenerationRequest).where(GenerationRequest.id == request_id))
                    assert req is not None
                    await _finish(db, req, GenerationStatus.FAILED, message)
                    await db.commit()
            raise
        await progress(done, total)

    async with sessions() as db:
        req = await db.scalar(select(GenerationRequest).where(GenerationRequest.id == request_id))
        assert req is not None
        any_ready = await _finish(db, req, GenerationStatus.COMPLETED, None)
        await db.commit()
    return {
        **counts,
        "total": total,
        "generation_request_id": request_id,
        "lead_status": (LeadStatus.GENERATED if any_ready else LeadStatus.SELECTED).value,
        "provider": provider.name,
        "model_key": model_key,
        "ai_calls": runner.calls,
        "spent_usd": str(runner.spent_usd),
    }


async def _set(sessions: Sessions, variant_id: int, status: VariantStatus, error: str) -> None:
    async with sessions() as db:
        await db.execute(
            update(GeneratedThumbnail)
            .where(GeneratedThumbnail.id == variant_id)
            .values(status=status, error=error)
        )
        await db.commit()
