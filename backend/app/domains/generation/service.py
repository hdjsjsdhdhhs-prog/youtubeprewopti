"""Thumbnail generation for a lead (ТЗ §12–14, §42–43; AI_ARCHITECTURE §7).

* **Estimate → confirm → start.** Generation is always paid, so it starts only with the ``confirm_token`` of
  the estimate the owner saw (model, price, variant count, inputs). The confirmed total is also the job's
  hard cost cap (``job_runs.budget_usd``); budgets are checked again before every image (runner).
* **Lead status** (ADR-0011): starting moves the lead to ``GENERATION_PENDING`` (through ``SELECTED`` if
  needed — pressing «Generate» is the owner's decision); the job ends in ``GENERATED`` when at least one
  variant is ready, otherwise back to ``SELECTED``.
* **Decisions** on variants (approve / reject / final / reset) are logged to ``feedback_events`` (§46).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.state_machine import ACTIVE_JOB_STATUSES, LEAD_MACHINE, LeadStatus
from app.domains.ai import budget
from app.domains.ai.cost import image_price
from app.domains.ai.models import AIModel
from app.domains.ai.prompts import LoadedPrompt, load_prompt
from app.domains.ai.registry import AITask, resolve_models
from app.domains.ai.runner import AINotConfiguredError
from app.domains.analysis import scoring as thumb_scoring
from app.domains.analysis.models import ChannelAnalysis, ThumbnailAnalysis
from app.domains.analysis.service import budget_checks
from app.domains.feedback.models import FeedbackEvent, FeedbackKind
from app.domains.generation.brief import (
    BriefHistory,
    BriefReference,
    GenerationBrief,
    build_fixes,
    prompt_variables,
    strategy_for,
)
from app.domains.generation.models import (
    DECIDABLE,
    GeneratedThumbnail,
    GenerationRequest,
    GenerationStatus,
    StyleReference,
    VariantStatus,
)
from app.domains.generation.schemas import (
    ComparisonOriginal,
    GenerationEstimate,
    GenerationIn,
    GenerationOut,
    GenerationStart,
    GenerationStartResult,
    ImageModelOption,
    LeadGenerations,
    ReferenceIn,
    ReferenceOut,
    ReferencePatch,
    SourceCandidate,
    VariantDecision,
    VariantOut,
)
from app.domains.identity.service import audit
from app.domains.jobs.models import JobRun
from app.domains.jobs.schemas import JobRunOut
from app.domains.jobs.service import JobQueue, JobType, enqueue_job
from app.domains.leads.models import Lead, LeadStatusHistory
from app.domains.media.imaging import InvalidImageError
from app.domains.media.models import ImageAsset, ImageMetrics, ImageSource, Thumbnail
from app.domains.media.service import store_image
from app.domains.projects.models import ProjectChannel, ProjectNiche, TaxonomyNode
from app.domains.youtube.models import Channel, Video
from app.domains.youtube.service import image_url
from app.providers.storage import StorageBackend

GENERATION_PROMPT = ("thumbnail_generation", 1)
TASK = AITask.THUMBNAIL_GENERATION
MAX_REFERENCE_BYTES = 10 * 1024 * 1024
SOURCE_CANDIDATES = 12
# statuses a generation can start from (moving through SELECTED where needed)
STARTABLE = frozenset(
    {
        LeadStatus.DISCOVERED,
        LeadStatus.ANALYZED,
        LeadStatus.SHORTLISTED,
        LeadStatus.SELECTED,
        LeadStatus.GENERATED,
        LeadStatus.FAILED,
        LeadStatus.GENERATION_PENDING,
    }
)
REASON_START = "генерация превью запущена"


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------
def decode_image(value: str) -> bytes:
    raw = value.split(",", 1)[1] if value.startswith("data:") else value
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AppError("image_base64 is not valid base64", code="invalid_image") from exc
    if len(data) > MAX_REFERENCE_BYTES:
        raise AppError("The image is larger than 10 MB", code="image_too_large")
    return data


def _reference_out(r: StyleReference, asset: ImageAsset) -> ReferenceOut:
    return ReferenceOut(
        id=r.id,
        image_asset_id=asset.id,
        image_url=image_url(asset.id),
        width=asset.width,
        height=asset.height,
        title=r.title,
        description=r.description,
        style=r.style,
        category=r.category,
        tags=list(r.tags),
        created_at=r.created_at,
    )


async def create_reference(
    db: AsyncSession, storage: StorageBackend, workspace_id: int, actor_id: int, body: ReferenceIn
) -> ReferenceOut:
    try:
        asset = await store_image(db, storage, decode_image(body.image_base64), ImageSource.REFERENCE)
    except InvalidImageError as exc:
        raise AppError(f"Not a supported image (JPEG, PNG, WebP): {exc}", code="invalid_image") from exc
    row = StyleReference(
        workspace_id=workspace_id,
        image_asset_id=asset.id,
        title=body.title,
        description=body.description,
        style=body.style,
        category=body.category,
        tags=sorted(set(body.tags)),
        created_by=actor_id,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    await audit(
        db,
        "reference.created",
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        entity_type="style_reference",
        entity_id=row.id,
        diff={"title": row.title},
    )
    return _reference_out(row, asset)


async def _reference(
    db: AsyncSession, workspace_id: int, reference_id: int
) -> tuple[StyleReference, ImageAsset]:
    row = (
        (
            await db.execute(
                select(StyleReference, ImageAsset)
                .join(ImageAsset, ImageAsset.id == StyleReference.image_asset_id)
                .where(StyleReference.id == reference_id, StyleReference.workspace_id == workspace_id)
            )
        )
        .tuples()
        .first()
    )
    if row is None:
        raise NotFoundError("Reference not found")
    return row


async def list_references(db: AsyncSession, workspace_id: int) -> list[ReferenceOut]:
    rows = await db.execute(
        select(StyleReference, ImageAsset)
        .join(ImageAsset, ImageAsset.id == StyleReference.image_asset_id)
        .where(StyleReference.workspace_id == workspace_id)
        .order_by(StyleReference.created_at.desc(), StyleReference.id.desc())
    )
    return [_reference_out(r, a) for r, a in rows.tuples()]


async def update_reference(
    db: AsyncSession, workspace_id: int, actor_id: int, reference_id: int, body: ReferencePatch
) -> ReferenceOut:
    row, asset = await _reference(db, workspace_id, reference_id)
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if "tags" in changes:
        changes["tags"] = sorted(set(changes["tags"]))
    for k, v in changes.items():
        setattr(row, k, v)
    await db.flush()
    await db.refresh(row)
    await audit(
        db,
        "reference.updated",
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        entity_type="style_reference",
        entity_id=row.id,
        diff=changes,
    )
    return _reference_out(row, asset)


async def delete_reference(db: AsyncSession, workspace_id: int, actor_id: int, reference_id: int) -> None:
    row, _ = await _reference(db, workspace_id, reference_id)
    await db.delete(row)
    await audit(
        db,
        "reference.deleted",
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        entity_type="style_reference",
        entity_id=reference_id,
        diff={"title": row.title},
    )


# ---------------------------------------------------------------------------
# Inputs of the brief
# ---------------------------------------------------------------------------
async def _lead(
    db: AsyncSession, workspace_id: int, lead_id: int, *, lock: bool = False
) -> tuple[Lead, Channel]:
    stmt = (
        select(Lead, Channel)
        .join(Channel, Channel.id == Lead.channel_id)
        .where(Lead.id == lead_id, Lead.workspace_id == workspace_id)
    )
    if lock:
        stmt = stmt.with_for_update(of=Lead).execution_options(populate_existing=True)
    row = (await db.execute(stmt)).tuples().first()
    if row is None:
        raise NotFoundError("Lead not found")
    return row


async def source_candidates(db: AsyncSession, workspace_id: int, channel_id: int) -> list[SourceCandidate]:
    """Newest non-Shorts videos of the channel with a stored thumbnail (and its current audit)."""
    weights = (await thumb_scoring.active_thumbnail_weights(db, workspace_id)).weights
    overall = thumb_scoring.overall_sql(weights)
    rows = await db.execute(
        select(Video, Thumbnail.image_asset_id, overall.label("overall"), ThumbnailAnalysis.problem_codes)
        .join(Thumbnail, Thumbnail.video_id == Video.id)
        .outerjoin(
            ThumbnailAnalysis,
            and_(ThumbnailAnalysis.image_asset_id == Thumbnail.image_asset_id, ThumbnailAnalysis.is_current),
        )
        .where(
            Video.channel_id == channel_id, Video.is_short.is_not(True), Thumbnail.image_asset_id.is_not(None)
        )
        .order_by(Video.published_at.desc().nulls_last(), Video.id.desc())
        .limit(SOURCE_CANDIDATES)
    )
    return [
        SourceCandidate(
            video_id=v.id,
            title=v.title,
            published_at=v.published_at,
            view_count=v.view_count,
            image_url=image_url(asset_id) if asset_id is not None else None,
            overall_score=Decimal(score).quantize(Decimal("0.01")) if score is not None else None,
            problem_codes=list(codes or []),
        )
        for v, asset_id, score, codes in rows.tuples()
    ]


def default_source(candidates: Sequence[SourceCandidate]) -> SourceCandidate | None:
    """The weakest audited thumbnail (the best redesign candidate), else the newest one."""
    audited = [c for c in candidates if c.overall_score is not None]
    if audited:
        return min(audited, key=lambda c: (c.overall_score, -c.video_id))
    return candidates[0] if candidates else None


async def _niches(db: AsyncSession, channel_id: int) -> list[str]:
    return list(
        await db.scalars(
            select(TaxonomyNode.name)
            .join(ProjectNiche, ProjectNiche.taxonomy_node_id == TaxonomyNode.id)
            .join(ProjectChannel, ProjectChannel.project_id == ProjectNiche.project_id)
            .where(ProjectChannel.channel_id == channel_id)
            .distinct()
            .order_by(TaxonomyNode.name)
        )
    )


async def _history(db: AsyncSession, lead_id: int) -> BriefHistory:
    rows = await db.execute(
        select(GeneratedThumbnail.strategy, GeneratedThumbnail.status)
        .join(GenerationRequest, GenerationRequest.id == GeneratedThumbnail.generation_request_id)
        .where(GenerationRequest.lead_id == lead_id)
    )
    approved: set[str] = set()
    rejected: set[str] = set()
    for strategy, status in rows.tuples():
        if status in (VariantStatus.APPROVED, VariantStatus.FINAL):
            approved.add(strategy)
        elif status == VariantStatus.REJECTED:
            rejected.add(strategy)
    requests = await db.scalar(
        select(func.count()).select_from(GenerationRequest).where(GenerationRequest.lead_id == lead_id)
    )
    return BriefHistory(
        previous_requests=int(requests or 0), approved=sorted(approved), rejected=sorted(rejected - approved)
    )


@dataclass(frozen=True)
class BriefInputs:
    brief: GenerationBrief
    base_asset_id: int | None  # image 1 (current thumbnail or parent variant)
    reference_asset_ids: list[int]
    source: SourceCandidate | None
    candidates: list[SourceCandidate]
    parent: GeneratedThumbnail | None


async def gather_brief(
    db: AsyncSession, workspace_id: int, lead: Lead, channel: Channel, body: GenerationIn
) -> BriefInputs:
    candidates = await source_candidates(db, workspace_id, channel.id)
    source: SourceCandidate | None
    if body.source_video_id is not None:
        source = next((c for c in candidates if c.video_id == body.source_video_id), None)
        if source is None:
            raise NotFoundError("The source video is not one of the channel's videos with a stored thumbnail")
    else:
        source = default_source(candidates)

    parent: GeneratedThumbnail | None = None
    if body.parent_variant_id is not None:
        parent = await db.scalar(
            select(GeneratedThumbnail)
            .join(GenerationRequest, GenerationRequest.id == GeneratedThumbnail.generation_request_id)
            .where(GeneratedThumbnail.id == body.parent_variant_id, GenerationRequest.lead_id == lead.id)
        )
        if parent is None or parent.image_asset_id is None:
            raise NotFoundError("Variant not found (or it has no image)")

    thumb_problems: list[dict[str, str]] = []
    recommendations: list[str] = []
    palette: list[str] = []
    source_asset: int | None = None
    if source is not None:
        source_asset = await db.scalar(
            select(Thumbnail.image_asset_id).where(Thumbnail.video_id == source.video_id)
        )
        analysis = await db.scalar(
            select(ThumbnailAnalysis).where(
                ThumbnailAnalysis.image_asset_id == source_asset, ThumbnailAnalysis.is_current
            )
        )
        if analysis is not None:
            thumb_problems = [dict(p) for p in analysis.problems]
            recommendations = [str(r.get("action", "")) for r in analysis.recommendations][:3]
        metrics = await db.scalar(select(ImageMetrics).where(ImageMetrics.image_asset_id == source_asset))
        if metrics is not None:
            palette = [str(c["hex"]) for c in (metrics.dominant_colors or [])[:5]]
    channel_audit = await db.scalar(
        select(ChannelAnalysis).where(ChannelAnalysis.channel_id == channel.id, ChannelAnalysis.is_current)
    )

    refs: list[tuple[StyleReference, ImageAsset]] = []
    for ref_id in dict.fromkeys(body.reference_ids):
        refs.append(await _reference(db, workspace_id, ref_id))

    base_asset = (
        parent.image_asset_id if parent is not None else (source_asset if body.use_source_image else None)
    )
    brief = GenerationBrief(
        channel_title=channel.title,
        niches=await _niches(db, channel.id),
        video_title=source.title if source is not None else None,
        base_image=("parent_variant" if parent is not None else "current_thumbnail") if base_asset else None,
        fixes=build_fixes(
            thumb_problems, [dict(p) for p in channel_audit.recurring_problems] if channel_audit else []
        ),
        recommendations=[r for r in recommendations if r],
        channel_summary=channel_audit.summary if channel_audit is not None else None,
        palette=palette,
        instructions=body.instructions,
        references=[
            BriefReference(id=r.id, title=r.title, style=r.style, description=r.description) for r, _ in refs
        ],
        history=await _history(db, lead.id),
    )
    return BriefInputs(
        brief=brief,
        base_asset_id=base_asset,
        reference_asset_ids=[a.id for _, a in refs],
        source=source,
        candidates=candidates,
        parent=parent,
    )


def render_prompt(prompt: LoadedPrompt, brief: GenerationBrief, index: int) -> tuple[str, str, str]:
    """(label, strategy key, full prompt text) of the index-th variant."""
    label, key, text = strategy_for(index)
    system, user = prompt.render(prompt_variables(brief, label, text))
    return label, key, f"{system}\n\n{user}"


# ---------------------------------------------------------------------------
# Estimate / start
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class GenerationPlan:
    estimate: GenerationEstimate
    inputs: BriefInputs
    model: AIModel | None
    prompt: LoadedPrompt
    token_payload: dict[str, Any]


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


async def plan_generation(
    db: AsyncSession, settings: Settings, workspace_id: int, lead_id: int, body: GenerationIn
) -> GenerationPlan:
    lead, channel = await _lead(db, workspace_id, lead_id)
    inputs = await gather_brief(db, workspace_id, lead, channel, body)
    models = await resolve_models(db, settings, TASK)
    options = [
        ImageModelOption(
            key=m.key,
            api_model_id=m.api_model_id,
            price_per_image_usd=image_price(m),
            pricing_verified=m.pricing_verified_at is not None,
        )
        for m in models
    ]
    if body.model_key is not None:
        model = next((m for m in models if m.key == body.model_key), None)
        if model is None:
            raise AppError(
                f"Image model '{body.model_key}' is not enabled for the active provider",
                code="invalid_model",
                details={"available": [m.key for m in models]},
            )
    else:
        model = models[0] if models else None
    prompt = await load_prompt(db, GENERATION_PROMPT[0], GENERATION_PROMPT[1], {})
    _, _, preview = render_prompt(prompt, inputs.brief, 0)
    price = image_price(model, quality=body.quality, size=body.size) if model is not None else None
    total = price * body.variant_count if price is not None else None
    checks = (
        await budget_checks(
            db, workspace_id, lead.created_from_project_id or 0, TASK, total, body.variant_count
        )
        if model is not None
        else []
    )
    token_payload: dict[str, Any] = {
        "lead_id": lead.id,
        "task": TASK.value,
        "model_key": model.key if model else None,
        "api_model_id": model.api_model_id if model else None,
        "price": str(price) if price is not None else None,
        "variants": body.variant_count,
        "source_video_id": inputs.source.video_id if inputs.source else None,
        "base_asset": inputs.base_asset_id,
        "references": inputs.reference_asset_ids,
        "parent": body.parent_variant_id,
        "size": body.size,
        "quality": body.quality,
        "instructions": _digest(body.instructions),
        "brief": _digest(inputs.brief.model_dump_json()),
        "prompt": f"{GENERATION_PROMPT[0]}/v{GENERATION_PROMPT[1]}",
    }
    estimate = GenerationEstimate(
        lead_id=lead.id,
        lead_status=lead.status,
        provider=model.provider if model else settings.effective_ai_provider,
        model_key=model.key if model else None,
        api_model_id=model.api_model_id if model else None,
        models=options,
        variant_count=body.variant_count,
        price_per_image_usd=price,
        estimated_cost_usd=total.quantize(Decimal("0.000001")) if total is not None else None,
        pricing_verified=bool(model and price is not None and model.pricing_verified_at is not None),
        source=inputs.source,
        source_candidates=inputs.candidates,
        brief=inputs.brief,
        prompt_preview=preview,
        budgets=checks,
        within_budgets=not any(c.would_exceed for c in checks),
        confirm_token=budget.confirmation_token(token_payload),
    )
    return GenerationPlan(
        estimate=estimate, inputs=inputs, model=model, prompt=prompt, token_payload=token_payload
    )


async def estimate_generation(
    db: AsyncSession, settings: Settings, workspace_id: int, lead_id: int, body: GenerationIn
) -> GenerationEstimate:
    """Pre-flight estimate (§76): the brief, the exact prompt of variant A, the price and the budgets.
    Nothing is generated or charged."""
    return (await plan_generation(db, settings, workspace_id, lead_id, body)).estimate


async def _move_lead(
    db: AsyncSession, lead: Lead, target: LeadStatus, actor_id: int | None, reason: str
) -> None:
    if lead.status == target:
        return
    LEAD_MACHINE.assert_can(lead.status, target)
    db.add(
        LeadStatusHistory(
            lead_id=lead.id, from_status=lead.status, to_status=target, actor_user_id=actor_id, reason=reason
        )
    )
    lead.status = target
    lead.status_changed_at = datetime.now(UTC)
    if target != LeadStatus.REJECTED:
        lead.rejected_reason = None


async def _active_generation(db: AsyncSession, lead_id: int) -> GenerationRequest | None:
    return await db.scalar(
        select(GenerationRequest)
        .join(JobRun, JobRun.id == GenerationRequest.job_run_id)
        .where(GenerationRequest.lead_id == lead_id, JobRun.status.in_(ACTIVE_JOB_STATUSES))
        .limit(1)
    )


async def _close_interrupted(db: AsyncSession, lead_id: int) -> None:
    """Requests whose job ended without finishing them (cancelled, worker lost) are closed."""
    stale = list(
        await db.scalars(
            select(GenerationRequest.id).where(
                GenerationRequest.lead_id == lead_id,
                GenerationRequest.status.in_([GenerationStatus.QUEUED, GenerationStatus.RUNNING]),
            )
        )
    )
    if not stale:
        return
    await db.execute(
        update(GeneratedThumbnail)
        .where(
            GeneratedThumbnail.generation_request_id.in_(stale),
            GeneratedThumbnail.status == VariantStatus.PENDING,
        )
        .values(
            status=VariantStatus.FAILED, error="Генерация была прервана (задача отменена или остановлена)."
        )
    )
    await db.execute(
        update(GenerationRequest)
        .where(GenerationRequest.id.in_(stale))
        .values(status=GenerationStatus.FAILED, error="interrupted")
    )


async def start_generation(
    db: AsyncSession,
    settings: Settings,
    workspace_id: int,
    actor_id: int,
    lead_id: int,
    body: GenerationStart,
) -> GenerationStartResult:
    """Queue exactly the confirmed estimate (409 ``confirmation_required`` with a new token otherwise)."""
    lead, _ = await _lead(db, workspace_id, lead_id, lock=True)
    if lead.status not in STARTABLE:
        raise ConflictError(
            f"A thumbnail cannot be generated for a lead in status '{lead.status.value}'",
            code="lead_status",
            details={"status": lead.status.value},
        )
    if await _active_generation(db, lead.id) is not None:
        raise ConflictError("A generation for this lead is already running", code="generation_running")
    await _close_interrupted(db, lead.id)
    plan = await plan_generation(
        db, settings, workspace_id, lead_id, GenerationIn(**body.model_dump(exclude={"confirm_token"}))
    )
    est, model = plan.estimate, plan.model
    if model is None:
        raise AINotConfiguredError(
            "No image model is configured: set YTL_VIBECODE_API_KEY (or YTL_AI_PROVIDER=mock for demo results)."  # noqa: E501
        )
    budget.require_confirmation(body.confirm_token, plan.token_payload)

    if lead.status in (LeadStatus.DISCOVERED, LeadStatus.ANALYZED, LeadStatus.SHORTLISTED, LeadStatus.FAILED):
        await _move_lead(db, lead, LeadStatus.SELECTED, actor_id, REASON_START)
    await _move_lead(db, lead, LeadStatus.GENERATION_PENDING, actor_id, REASON_START)

    inputs = plan.inputs
    request = GenerationRequest(
        workspace_id=workspace_id,
        lead_id=lead.id,
        project_id=lead.created_from_project_id,
        source_video_id=inputs.source.video_id if inputs.source else None,
        parent_variant_id=body.parent_variant_id,
        instructions=body.instructions,
        reference_ids=[r.id for r in inputs.brief.references],
        variant_count=body.variant_count,
        model_key=model.key,
        size=body.size,
        quality=body.quality,
        brief=inputs.brief.model_dump(mode="json"),
        prompt_template_id=plan.prompt.template_id,
        estimated_cost_usd=est.estimated_cost_usd,
        requested_by=actor_id,
    )
    db.add(request)
    await db.flush()
    sources = [a for a in [inputs.base_asset_id, *inputs.reference_asset_ids] if a is not None]
    for i in range(body.variant_count):
        label, strategy, text = render_prompt(plan.prompt, inputs.brief, i)
        db.add(
            GeneratedThumbnail(
                generation_request_id=request.id,
                parent_id=body.parent_variant_id,
                label=label,
                strategy=strategy,
                prompt=text,
                model_key=model.key,
                settings={
                    "size": body.size,
                    "quality": body.quality,
                    "prompt": "/".join(map(str, GENERATION_PROMPT)),
                },
                source_image_ids=sources,
            )
        )
    await db.flush()
    enq = await enqueue_job(
        db,
        type_=JobType.THUMBNAIL_GENERATION,
        queue=JobQueue.INTERACTIVE,
        params={
            "generation_request_id": request.id,
            "lead_id": lead.id,
            "project_id": lead.created_from_project_id,
        },
        workspace_id=workspace_id,
        created_by=actor_id,
        progress_total=body.variant_count,
    )
    enq.job.estimated_cost_usd = est.estimated_cost_usd
    enq.job.budget_usd = est.estimated_cost_usd  # hard cap = what the owner confirmed
    request.job_run_id = enq.job.id
    await db.flush()
    await audit(
        db,
        "generation.started",
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        entity_type="lead",
        entity_id=lead.id,
        diff={
            "generation_request_id": request.id,
            "variants": body.variant_count,
            "model_key": model.key,
            "estimated_cost_usd": str(est.estimated_cost_usd),
            "confirm_token": body.confirm_token,
        },
    )
    [out] = await _generations_out(db, [request], is_mock=model.provider == "mock")
    return GenerationStartResult(generation=out, lead_status=lead.status)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def _variant_out(v: GeneratedThumbnail) -> VariantOut:
    return VariantOut(
        id=v.id,
        label=v.label,
        strategy=v.strategy,
        status=v.status,
        image_asset_id=v.image_asset_id,
        image_url=image_url(v.image_asset_id) if v.image_asset_id is not None else None,
        prompt=v.prompt,
        model_key=v.model_key,
        api_model_id=v.api_model_id,
        settings=dict(v.settings),
        source_image_ids=list(v.source_image_ids),
        parent_id=v.parent_id,
        cost_usd=v.cost_usd,
        error=v.error,
        decided_by=v.decided_by,
        decided_at=v.decided_at,
        created_at=v.created_at,
        updated_at=v.updated_at,
    )


async def _generations_out(
    db: AsyncSession, requests: Sequence[GenerationRequest], *, is_mock: bool | None = None
) -> list[GenerationOut]:
    ids = [r.id for r in requests]
    variants: dict[int, list[GeneratedThumbnail]] = {}
    for v in await db.scalars(
        select(GeneratedThumbnail)
        .where(GeneratedThumbnail.generation_request_id.in_(ids))
        .order_by(GeneratedThumbnail.label)
        .execution_options(populate_existing=True)
    ):
        variants.setdefault(v.generation_request_id, []).append(v)
    jobs = {
        j.id: j
        for j in await db.scalars(
            select(JobRun).where(JobRun.id.in_([r.job_run_id for r in requests if r.job_run_id is not None]))
        )
    }
    models = {m.key: m.provider for m in await db.scalars(select(AIModel))}
    sources = {
        vid: asset
        for vid, asset in (
            await db.execute(
                select(Thumbnail.video_id, Thumbnail.image_asset_id).where(
                    Thumbnail.video_id.in_([r.source_video_id for r in requests if r.source_video_id])
                )
            )
        ).tuples()
    }
    out: list[GenerationOut] = []
    for r in requests:
        vs = variants.get(r.id, [])
        job = jobs.get(r.job_run_id) if r.job_run_id is not None else None
        asset = sources.get(r.source_video_id) if r.source_video_id is not None else None
        out.append(
            GenerationOut(
                id=r.id,
                lead_id=r.lead_id,
                status=r.status,
                source_video_id=r.source_video_id,
                source_image_url=image_url(asset) if asset is not None else None,
                parent_variant_id=r.parent_variant_id,
                instructions=r.instructions,
                reference_ids=list(r.reference_ids),
                variant_count=r.variant_count,
                model_key=r.model_key,
                size=r.size,
                quality=r.quality,
                brief=GenerationBrief.model_validate(r.brief),
                estimated_cost_usd=r.estimated_cost_usd,
                spent_usd=sum((v.cost_usd or Decimal(0) for v in vs), Decimal(0)),
                error=r.error,
                job=JobRunOut.model_validate(job) if job is not None else None,
                provider_is_mock=is_mock if is_mock is not None else models.get(r.model_key) == "mock",
                variants=[_variant_out(v) for v in vs],
                created_at=r.created_at,
            )
        )
    return out


async def lead_generations(db: AsyncSession, workspace_id: int, lead_id: int) -> LeadGenerations:
    lead, _ = await _lead(db, workspace_id, lead_id)
    requests = list(
        await db.scalars(
            select(GenerationRequest)
            .where(GenerationRequest.lead_id == lead.id)
            .order_by(GenerationRequest.created_at.desc(), GenerationRequest.id.desc())
        )
    )
    items = await _generations_out(db, requests)
    final = next((v.id for g in items for v in g.variants if v.status == VariantStatus.FINAL), None)
    return LeadGenerations(
        lead_id=lead.id,
        lead_status=lead.status,
        final_variant_id=final,
        items=items,
        originals=await _originals(
            db, workspace_id, [r.source_video_id for r in requests if r.source_video_id]
        ),
    )


async def _originals(
    db: AsyncSession, workspace_id: int, video_ids: Sequence[int]
) -> list[ComparisonOriginal]:
    wanted = list(dict.fromkeys(video_ids))
    if not wanted:
        return []
    overall = thumb_scoring.overall_sql(
        (await thumb_scoring.active_thumbnail_weights(db, workspace_id)).weights
    )
    rows = await db.execute(
        select(Video, Thumbnail.image_asset_id, ThumbnailAnalysis, overall.label("overall"))
        .join(Thumbnail, Thumbnail.video_id == Video.id)
        .outerjoin(
            ThumbnailAnalysis,
            and_(ThumbnailAnalysis.image_asset_id == Thumbnail.image_asset_id, ThumbnailAnalysis.is_current),
        )
        .where(Video.id.in_(wanted))
    )
    by_id = {
        v.id: ComparisonOriginal(
            video_id=v.id,
            title=v.title,
            image_url=image_url(asset) if asset is not None else None,
            overall_score=Decimal(score).quantize(Decimal("0.01"))
            if score is not None and a is not None
            else None,
            problems=list(a.problems) if a is not None else [],
            recommendations=list(a.recommendations) if a is not None else [],
            visual_summary=a.visual_summary if a is not None else None,
        )
        for v, asset, a, score in rows.tuples()
    }
    return [by_id[i] for i in wanted if i in by_id]


# ---------------------------------------------------------------------------
# Decisions on variants (ТЗ §13: Approve / Reject / Use as final free sample)
# ---------------------------------------------------------------------------
_DECISION_STATUS = {
    "approve": VariantStatus.APPROVED,
    "reject": VariantStatus.REJECTED,
    "final": VariantStatus.FINAL,
    "reset": VariantStatus.READY,
}


async def decide_variant(
    db: AsyncSession, workspace_id: int, actor_id: int, variant_id: int, body: VariantDecision
) -> LeadGenerations:
    row = (
        (
            await db.execute(
                select(GeneratedThumbnail, GenerationRequest)
                .join(GenerationRequest, GenerationRequest.id == GeneratedThumbnail.generation_request_id)
                .where(GeneratedThumbnail.id == variant_id, GenerationRequest.workspace_id == workspace_id)
                .with_for_update(of=GeneratedThumbnail)
            )
        )
        .tuples()
        .first()
    )
    if row is None:
        raise NotFoundError("Variant not found")
    variant, request = row
    if variant.status not in DECIDABLE:
        raise ConflictError(
            f"Variant {variant.label} is '{variant.status.value}': only generated variants can be decided on",
            code="variant_not_ready",
        )
    target = _DECISION_STATUS[body.decision]
    previous = variant.status
    now = datetime.now(UTC)
    if target == VariantStatus.FINAL:  # one final sample per lead
        others = (
            select(GeneratedThumbnail.id)
            .join(GenerationRequest, GenerationRequest.id == GeneratedThumbnail.generation_request_id)
            .where(
                GenerationRequest.lead_id == request.lead_id,
                GeneratedThumbnail.status == VariantStatus.FINAL,
                GeneratedThumbnail.id != variant.id,
            )
        )
        await db.execute(
            update(GeneratedThumbnail)
            .where(GeneratedThumbnail.id.in_(others))
            .values(status=VariantStatus.APPROVED, decided_by=actor_id, decided_at=now)
        )
    variant.status = target
    variant.decided_by = actor_id
    variant.decided_at = now
    if previous != target:
        db.add(
            FeedbackEvent(
                workspace_id=workspace_id,
                user_id=actor_id,
                kind=FeedbackKind.GENERATION_DECISION,
                target_type="generated_thumbnail",
                target_id=variant.id,
                field="status",
                payload={
                    "decision": body.decision,
                    "from": previous.value,
                    "to": target.value,
                    "strategy": variant.strategy,
                    "label": variant.label,
                    "model_key": variant.model_key,
                    "lead_id": request.lead_id,
                    "comment": body.comment,
                },
            )
        )
        await audit(
            db,
            f"generation.variant_{body.decision}",
            workspace_id=workspace_id,
            actor_user_id=actor_id,
            entity_type="generated_thumbnail",
            entity_id=variant.id,
            diff={
                "from": previous.value,
                "to": target.value,
                "lead_id": request.lead_id,
                "comment": body.comment,
            },
        )
    await db.flush()
    return await lead_generations(db, workspace_id, request.lead_id)
