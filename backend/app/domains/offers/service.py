"""Offers for a lead and the Approval Queue (ТЗ §20–23, §28; ADR-0010, ADR-0011).

* **Inputs** — the facts the drafts may use are built here from stored data (``drafting.py``) and stored on
  the offer; the free sample defaults to the lead's final generated thumbnail.
* **Estimate → confirm → start** (§76, like Phase 5): the start must echo the ``confirm_token`` of the
  estimate (model, price, variants, inputs); the confirmed total is the job's hard cost cap. One offer per
  lead and attempt: starting again («Regenerate») adds a round to the lead's latest offer.
* **Approval** — «Approve» is bound to the SHA-256 of the exact content (selected text, attachment, contact,
  channel: ``content_hash``) and recorded in ``approvals``; any later change sends the offer back to the
  queue. Approve is refused while the text has guard issues, without a usable contact or a matching channel.
  Nothing is sent in Phase 7: «Schedule» only stores the time (``require_approval`` must pass).
* Every status change is in ``offer_status_history`` and moves the lead along ``LEAD_MACHINE``; decisions
  and edits go to ``feedback_events`` (§46) and ``audit_logs``.
"""

from __future__ import annotations

import hashlib
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, and_, exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.state_machine import ACTIVE_JOB_STATUSES, LEAD_MACHINE, LeadStatus
from app.domains.ai import budget
from app.domains.ai.cost import pricing_known, token_cost
from app.domains.ai.models import AIModel
from app.domains.ai.prompts import load_prompt
from app.domains.ai.registry import TASKS, AITask, resolve_models
from app.domains.ai.runner import AINotConfiguredError
from app.domains.analysis.models import ChannelAnalysis, ThumbnailAnalysis
from app.domains.analysis.service import budget_checks
from app.domains.approvals import service as approvals
from app.domains.approvals.models import Approval, ApprovalDecision, ApprovalTarget
from app.domains.contacts.models import ContactType
from app.domains.contacts.schemas import ContactOut
from app.domains.contacts.service import channel_contacts
from app.domains.feedback.models import FeedbackEvent, FeedbackKind
from app.domains.generation.models import GeneratedThumbnail, GenerationRequest, VariantStatus
from app.domains.generation.schemas import SourceCandidate
from app.domains.generation.service import default_source, source_candidates
from app.domains.identity.service import audit
from app.domains.jobs.models import JobRun
from app.domains.jobs.schemas import JobRunOut
from app.domains.jobs.service import JobQueue, JobType, enqueue_job
from app.domains.leads.models import Lead, LeadScore, LeadStatusHistory
from app.domains.media.models import Thumbnail
from app.domains.offers import drafting
from app.domains.offers.drafting import (
    GuardIssue,
    OfferDraft,
    OfferFact,
    OfferInputs,
    OfferSample,
    OfferTemplateRef,
    PersonalizationSource,
)
from app.domains.offers.models import (
    OFFER_MACHINE,
    OPEN_STATUSES,
    Offer,
    OfferStatus,
    OfferStatusHistory,
    OfferVariant,
    OfferVariantStatus,
    OutreachChannel,
)
from app.domains.offers.schemas import (
    ApprovalOut,
    ApprovalQueue,
    Blocker,
    LeadOffers,
    OfferDecisionIn,
    OfferEdit,
    OfferEstimate,
    OfferIn,
    OfferOut,
    OfferScheduleIn,
    OfferStart,
    OfferStartResult,
    OfferVariantOut,
    QueueAnalysis,
    QueueCard,
    QueueChannel,
    QueueLead,
    QueueStatus,
    QueueThumbnail,
    SampleOut,
)
from app.domains.offers.templates import get_template
from app.domains.projects.models import ProjectChannel, ProjectNiche, SearchProject, TaxonomyNode
from app.domains.youtube.models import Channel, Video
from app.domains.youtube.service import image_url

OFFER_PROMPT = ("offer_generation", 1)
TASK = AITask.OFFER_GENERATION
DEFAULT_LANGUAGE = "ru"
MAX_VIDEOS = 3
PROBLEMS_PER_VIDEO = 2
MAX_CHANNEL_PROBLEMS = 3
UNIQUENESS_POOL = 200  # other offers' texts compared for uniqueness
_RANK = {"high": 0, "medium": 1, "low": 2}
# a new round can start from these lead statuses (through SELECTED where needed)
STARTABLE = frozenset(
    {
        LeadStatus.DISCOVERED,
        LeadStatus.ANALYZED,
        LeadStatus.SHORTLISTED,
        LeadStatus.SELECTED,
        LeadStatus.GENERATED,
        LeadStatus.FAILED,
        LeadStatus.OFFER_DRAFT,
        LeadStatus.PENDING_APPROVAL,
        LeadStatus.APPROVED,
        LeadStatus.SCHEDULED,
    }
)
CLOSED_LEAD = frozenset({LeadStatus.REJECTED, LeadStatus.OPTED_OUT})
REUSABLE = (*OPEN_STATUSES, OfferStatus.REJECTED, OfferStatus.FAILED)
EDITABLE = frozenset({OfferStatus.PENDING_APPROVAL, OfferStatus.APPROVED, OfferStatus.SCHEDULED})
SAMPLE_STATUSES = (VariantStatus.APPROVED, VariantStatus.FINAL)
# the channel a contact type is sent through by default (manual = the operator sends it himself, any type)
CHANNEL_FOR_TYPE: dict[ContactType, OutreachChannel] = {
    ContactType.EMAIL: OutreachChannel.EMAIL,
    ContactType.TELEGRAM: OutreachChannel.TELEGRAM,
    ContactType.VK: OutreachChannel.VK,
}
LEAD_PATH_THROUGH = frozenset(
    {
        LeadStatus.SELECTED,
        LeadStatus.OFFER_DRAFT,
        LeadStatus.PENDING_APPROVAL,
        LeadStatus.APPROVED,
        LeadStatus.SCHEDULED,
    }
)
REASON_START = "генерация оффера запущена"
REASON_EDITED = "оффер изменён после подтверждения"


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------
def lead_path(current: LeadStatus, target: LeadStatus) -> list[LeadStatus]:
    """Shortest chain of allowed lead transitions ``current`` → ``target`` through the offer statuses
    (e.g. SCHEDULED → APPROVED → PENDING_APPROVAL); [] = already there; raises if impossible."""
    if current == target:
        return []
    queue: deque[list[LeadStatus]] = deque([[current]])
    seen = {current}
    while queue:
        path = queue.popleft()
        for nxt in sorted(LEAD_MACHINE.allowed(path[-1])):
            if nxt in seen or (nxt != target and nxt not in LEAD_PATH_THROUGH):
                continue
            if nxt == target:
                return [*path[1:], nxt]
            seen.add(nxt)
            queue.append([*path, nxt])
    LEAD_MACHINE.assert_can(current, target)  # raises InvalidTransitionError
    return [target]  # pragma: no cover - assert_can raised


def move_lead(db: AsyncSession, lead: Lead, target: LeadStatus, actor_id: int | None, reason: str) -> None:
    now = datetime.now(UTC)
    for step in lead_path(lead.status, target):
        db.add(
            LeadStatusHistory(
                lead_id=lead.id,
                from_status=lead.status,
                to_status=step,
                actor_user_id=actor_id,
                reason=reason,
            )
        )
        lead.status = step
        lead.status_changed_at = now
        lead.rejected_reason = None


def move_offer(
    db: AsyncSession, offer: Offer, target: OfferStatus, actor_id: int | None, reason: str
) -> None:
    if offer.status == target:
        return
    OFFER_MACHINE.assert_can(offer.status, target)
    db.add(
        OfferStatusHistory(
            offer_id=offer.id,
            from_status=offer.status,
            to_status=target,
            actor_user_id=actor_id,
            reason=reason,
        )
    )
    offer.status = target


# ---------------------------------------------------------------------------
# Inputs
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


def _spaced(n: int) -> str:
    return f"{n:,}".replace(",", " ")


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


def _sample_out(v: GeneratedThumbnail, request_id: int, title: str | None) -> SampleOut:
    return SampleOut(
        variant_id=v.id,
        generation_request_id=request_id,
        label=v.label,
        strategy=v.strategy,
        status=v.status.value,
        image_url=image_url(v.image_asset_id) if v.image_asset_id is not None else None,
        video_title=title,
    )


def _samples_select() -> Any:
    return (
        select(GeneratedThumbnail, GenerationRequest.id, Video.title)
        .join(GenerationRequest, GenerationRequest.id == GeneratedThumbnail.generation_request_id)
        .outerjoin(Video, Video.id == GenerationRequest.source_video_id)
    )


async def sample_candidates(db: AsyncSession, lead_id: int) -> list[SampleOut]:
    """Approved / final generated thumbnails of the lead (final first, then newest)."""
    rows = await db.execute(
        _samples_select()
        .where(
            GenerationRequest.lead_id == lead_id,
            GeneratedThumbnail.status.in_(SAMPLE_STATUSES),
            GeneratedThumbnail.image_asset_id.is_not(None),
        )
        .order_by(
            (GeneratedThumbnail.status == VariantStatus.FINAL).desc(),
            GeneratedThumbnail.created_at.desc(),
            GeneratedThumbnail.id.desc(),
        )
    )
    return [_sample_out(v, rid, title) for v, rid, title in rows.tuples()]


async def _attachment(db: AsyncSession, variant_id: int | None) -> SampleOut | None:
    """The attached variant as it is now (even if its decision changed since)."""
    if variant_id is None:
        return None
    row = (await db.execute(_samples_select().where(GeneratedThumbnail.id == variant_id))).tuples().first()
    return _sample_out(*row) if row is not None else None


async def _sample(db: AsyncSession, sample: SampleOut) -> OfferSample:
    brief = await db.scalar(
        select(GenerationRequest.brief).where(GenerationRequest.id == sample.generation_request_id)
    )
    fixes = [str(f.get("code", "")) for f in (brief or {}).get("fixes", []) if f.get("code")]
    return OfferSample(
        variant_id=sample.variant_id,
        label=sample.label,
        strategy=sample.strategy,
        video_title=sample.video_title,
        fixes=fixes,
    )


async def _current_audit(db: AsyncSession, video_id: int) -> ThumbnailAnalysis | None:
    return await db.scalar(
        select(ThumbnailAnalysis)
        .join(Thumbnail, Thumbnail.image_asset_id == ThumbnailAnalysis.image_asset_id)
        .where(Thumbnail.video_id == video_id, ThumbnailAnalysis.is_current)
    )


def pick_videos(candidates: Sequence[SourceCandidate]) -> list[SourceCandidate]:
    """The concrete videos the offer may talk about: the two weakest audited thumbnails, then the newest."""
    audited = sorted(
        (c for c in candidates if c.overall_score is not None), key=lambda c: (c.overall_score, -c.video_id)
    )
    picked: list[SourceCandidate] = []
    for c in [*audited[:2], *candidates]:
        if all(p.video_id != c.video_id for p in picked):
            picked.append(c)
    return picked[:MAX_VIDEOS]


@dataclass(frozen=True)
class GatheredInputs:
    inputs: OfferInputs
    samples: list[SampleOut]
    attachment: SampleOut | None


async def gather_inputs(
    db: AsyncSession, workspace_id: int, lead: Lead, channel: Channel, body: OfferIn
) -> GatheredInputs:
    facts: list[OfferFact] = []

    def add(kind: drafting.FactKind, field: str, value: str, **ref: int) -> None:
        value = " ".join(value.split())
        if value and len(facts) < drafting.MAX_FACTS:
            facts.append(OfferFact(id=len(facts) + 1, kind=kind, field=field, value=value[:400], ref=ref))

    add("channel", "channel.title", channel.title, channel_id=channel.id)
    if channel.handle:
        add("channel", "channel.handle", f"@{channel.handle.lstrip('@')}", channel_id=channel.id)
    if channel.subscriber_count and not channel.subscribers_hidden:
        add("channel", "channel.subscribers", f"{_spaced(channel.subscriber_count)} подписчиков")
    for niche in (await _niches(db, channel.id))[:3]:
        add("niche", "project.niche", niche)

    videos = pick_videos(await source_candidates(db, workspace_id, channel.id))
    for c in videos:
        add("video", "video.title", c.title, video_id=c.video_id)
    for c in videos:
        analysis = await _current_audit(db, c.video_id)
        if analysis is None:
            continue
        problems = sorted(analysis.problems, key=lambda p: _RANK.get(str(p.get("severity")), 3))
        for p in problems[:PROBLEMS_PER_VIDEO]:
            add(
                "thumbnail_problem",
                "thumbnail_analysis.problem",
                f"превью видео «{c.title}»: {p.get('description', '')}",
                video_id=c.video_id,
            )
        if analysis.recommendations:
            add(
                "recommendation",
                "thumbnail_analysis.recommendation",
                f"для «{c.title}»: {analysis.recommendations[0].get('action', '')}",
                video_id=c.video_id,
            )

    channel_audit = await db.scalar(
        select(ChannelAnalysis).where(ChannelAnalysis.channel_id == channel.id, ChannelAnalysis.is_current)
    )
    if channel_audit is not None:
        add(
            "channel_audit",
            "channel_analysis.summary",
            channel_audit.summary,
            channel_analysis_id=channel_audit.id,
        )
        for p in channel_audit.recurring_problems[:MAX_CHANNEL_PROBLEMS]:
            add(
                "channel_audit",
                "channel_analysis.recurring_problem",
                str(p.get("description", "")),
                channel_analysis_id=channel_audit.id,
            )

    samples = await sample_candidates(db, lead.id)
    attachment: SampleOut | None = None
    if body.attachment_variant_id is not None:
        attachment = next((s for s in samples if s.variant_id == body.attachment_variant_id), None)
        if attachment is None:
            raise NotFoundError(
                "The attachment must be an approved or final generated thumbnail of this lead"
            )
    elif body.attach_sample and samples and samples[0].status == VariantStatus.FINAL.value:
        attachment = samples[0]
    sample = await _sample(db, attachment) if attachment is not None else None
    if sample is not None:
        add(
            "sample",
            "generated_thumbnail",
            f"бесплатный образец превью (вариант {sample.label})"
            + (f" для видео «{sample.video_title}»" if sample.video_title else "")
            + (f"; исправляет: {', '.join(sample.fixes)}" if sample.fixes else ""),
            variant_id=sample.variant_id,
        )

    template: OfferTemplateRef | None = None
    if body.template_id is not None:
        t = await get_template(db, workspace_id, body.template_id)
        template = OfferTemplateRef(id=t.id, name=t.name, version=t.version, body=t.body)

    language = body.language or (channel.default_language or "").split("-")[0].lower() or None
    if language is None and lead.created_from_project_id is not None:
        language = await db.scalar(
            select(SearchProject.language).where(SearchProject.id == lead.created_from_project_id)
        )
    inputs = OfferInputs(
        channel_title=channel.title,
        language=(language or DEFAULT_LANGUAGE)[:20],
        facts=facts,
        sample=sample,
        template=template,
        instructions=body.instructions,
    )
    return GatheredInputs(inputs=inputs, samples=samples, attachment=attachment)


async def other_texts(db: AsyncSession, workspace_id: int, offer_id: int) -> list[str]:
    """Texts of the workspace's other offers (selected variants) — the uniqueness reference (§21)."""
    return list(
        await db.scalars(
            select(OfferVariant.body)
            .join(Offer, Offer.id == OfferVariant.offer_id)
            .where(Offer.workspace_id == workspace_id, Offer.id != offer_id, OfferVariant.is_selected)
            .order_by(OfferVariant.id.desc())
            .limit(UNIQUENESS_POOL)
        )
    )


def check_variant(
    inputs: OfferInputs, variant: OfferVariant, others: Sequence[str]
) -> tuple[list[GuardIssue], Decimal, list[PersonalizationSource]]:
    """Guard + personalization of the variant's current text (AI claims only while it is not edited)."""
    draft = OfferDraft.model_validate(variant.draft) if variant.draft else None
    claims = draft.claims if draft is not None and variant.edited_at is None else None
    issues = drafting.guard(inputs, variant.subject, variant.body, variant.cta, claims)
    score, sources = drafting.personalization(
        inputs,
        draft.facts_used if draft is not None else [],
        f"{variant.subject}\n{variant.body}",
        others,
    )
    return issues, score, sources


# ---------------------------------------------------------------------------
# Estimate / start
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class OfferPlan:
    estimate: OfferEstimate
    gathered: GatheredInputs
    model: AIModel | None
    prompt_template_id: int
    offer: Offer | None
    token_payload: dict[str, Any]


async def _latest_offer(db: AsyncSession, lead_id: int, *, lock: bool = False) -> Offer | None:
    stmt = (
        select(Offer)
        .where(Offer.lead_id == lead_id, Offer.status.in_(REUSABLE))
        .order_by(Offer.id.desc())
        .limit(1)
    )
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return await db.scalar(stmt)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


async def plan_offer(
    db: AsyncSession, settings: Settings, workspace_id: int, lead_id: int, body: OfferIn
) -> OfferPlan:
    lead, channel = await _lead(db, workspace_id, lead_id)
    gathered = await gather_inputs(db, workspace_id, lead, channel, body)
    offer = await _latest_offer(db, lead.id)
    models = await resolve_models(db, settings, TASK)
    model = models[0] if models else None
    spec = TASKS[TASK]
    per = token_cost(model, spec.est_input_tokens, spec.est_output_tokens) if model is not None else None
    total = (per * body.variant_count).quantize(Decimal("0.000001")) if per is not None else None
    prompt = await load_prompt(db, OFFER_PROMPT[0], OFFER_PROMPT[1], OfferDraft.model_json_schema())
    label, style = drafting.style_for(0)
    system, user = prompt.render(drafting.prompt_variables(gathered.inputs, label, style))
    checks = (
        await budget_checks(
            db, workspace_id, lead.created_from_project_id or 0, TASK, total, body.variant_count
        )
        if model is not None
        else []
    )
    round_ = offer.round + 1 if offer is not None else 1
    token_payload: dict[str, Any] = {
        "lead_id": lead.id,
        "task": TASK.value,
        "offer_id": offer.id if offer is not None else None,
        "round": round_,
        "model_key": model.key if model else None,
        "api_model_id": model.api_model_id if model else None,
        "per_variant": str(per) if per is not None else None,
        "variants": body.variant_count,
        "inputs": _digest(gathered.inputs.model_dump_json()),
        "prompt": f"{OFFER_PROMPT[0]}/v{OFFER_PROMPT[1]}",
    }
    estimate = OfferEstimate(
        lead_id=lead.id,
        lead_status=lead.status,
        offer_id=offer.id if offer is not None else None,
        round=round_,
        provider=model.provider if model else settings.effective_ai_provider,
        model_key=model.key if model else None,
        api_model_id=model.api_model_id if model else None,
        variant_count=body.variant_count,
        styles=[drafting.style_for(i)[1] for i in range(body.variant_count)],
        cost_per_variant_usd=per,
        estimated_cost_usd=total,
        pricing_verified=bool(model and pricing_known(model) and model.pricing_verified_at is not None),
        inputs=gathered.inputs,
        prompt_preview=f"{system}\n\n{user}",
        samples=gathered.samples,
        budgets=checks,
        within_budgets=not any(c.would_exceed for c in checks),
        confirm_token=budget.confirmation_token(token_payload),
    )
    return OfferPlan(
        estimate=estimate,
        gathered=gathered,
        model=model,
        prompt_template_id=prompt.template_id,
        offer=offer,
        token_payload=token_payload,
    )


async def estimate_offer(
    db: AsyncSession, settings: Settings, workspace_id: int, lead_id: int, body: OfferIn
) -> OfferEstimate:
    """Pre-flight (§76): the facts, the exact prompt of variant A, price and budgets. Nothing is called."""
    return (await plan_offer(db, settings, workspace_id, lead_id, body)).estimate


async def _active_job(db: AsyncSession, offer: Offer) -> bool:
    if offer.job_run_id is None:
        return False
    status = await db.scalar(select(JobRun.status).where(JobRun.id == offer.job_run_id))
    return status in ACTIVE_JOB_STATUSES


def _default_contact(offer: Offer, contacts: Sequence[ContactOut]) -> None:
    """Keep a usable chosen contact; otherwise take the best usable one (contacts are sorted usable first,
    then by type). The channel follows the contact type unless it is still compatible."""
    current = next((c for c in contacts if c.id == offer.contact_id and c.usable), None)
    if current is None:
        current = next((c for c in contacts if c.usable), None)
        offer.contact_id = current.id if current is not None else None
    if current is not None and not channel_fits(offer.channel, current.type):
        offer.channel = CHANNEL_FOR_TYPE.get(current.type, OutreachChannel.MANUAL)


def channel_fits(channel: OutreachChannel | None, contact_type: ContactType) -> bool:
    return channel is not None and (
        channel == OutreachChannel.MANUAL or CHANNEL_FOR_TYPE.get(contact_type) == channel
    )


async def start_offer(
    db: AsyncSession, settings: Settings, workspace_id: int, actor_id: int, lead_id: int, body: OfferStart
) -> OfferStartResult:
    """Queue exactly the confirmed estimate (409 ``confirmation_required`` with a new token otherwise)."""
    lead, channel = await _lead(db, workspace_id, lead_id, lock=True)
    if lead.status == LeadStatus.GENERATION_PENDING:
        raise ConflictError(
            "A thumbnail generation for this lead is running; write the offer when it is done",
            code="generation_running",
        )
    if lead.status not in STARTABLE:
        raise ConflictError(
            f"An offer cannot be written for a lead in status '{lead.status.value}'",
            code="lead_status",
            details={"status": lead.status.value},
        )
    existing = await _latest_offer(db, lead.id, lock=True)
    if existing is not None and existing.status == OfferStatus.DRAFT and await _active_job(db, existing):
        raise ConflictError("Drafts of this offer are being generated", code="offer_generating")
    plan = await plan_offer(
        db, settings, workspace_id, lead_id, OfferIn(**body.model_dump(exclude={"confirm_token"}))
    )
    est, model = plan.estimate, plan.model
    if model is None:
        raise AINotConfiguredError(
            "No text model is configured: set YTL_VIBECODE_API_KEY (or YTL_AI_PROVIDER=mock for demo drafts)."
        )
    budget.require_confirmation(body.confirm_token, plan.token_payload)

    if existing is None:
        offer = Offer(
            workspace_id=workspace_id,
            lead_id=lead.id,
            status=OfferStatus.DRAFT,
            round=1,
            variant_count=body.variant_count,
            language=plan.gathered.inputs.language,
            inputs={},
            created_by=actor_id,
        )
        db.add(offer)
        await db.flush()
        db.add(
            OfferStatusHistory(
                offer_id=offer.id,
                from_status=None,
                to_status=OfferStatus.DRAFT,
                actor_user_id=actor_id,
                reason=REASON_START,
            )
        )
    else:
        offer = existing
        await _close_interrupted(db, offer)
        move_offer(db, offer, OfferStatus.DRAFT, actor_id, "новый раунд генерации")
        offer.round += 1
    inputs = plan.gathered.inputs
    offer.project_id = lead.created_from_project_id
    offer.variant_count = body.variant_count
    offer.language = inputs.language
    offer.instructions = body.instructions
    offer.template_id = inputs.template.id if inputs.template is not None else None
    offer.inputs = inputs.model_dump(mode="json")
    offer.attachment_variant_id = plan.gathered.attachment.variant_id if plan.gathered.attachment else None
    offer.model_key = model.key
    offer.prompt_template_id = plan.prompt_template_id
    offer.estimated_cost_usd = est.estimated_cost_usd
    offer.error = None
    offer.scheduled_at = None
    offer.skipped_at = None
    contacts = (await channel_contacts(db, workspace_id, channel.id)).items
    _default_contact(offer, contacts)
    for i in range(body.variant_count):
        label, style = drafting.style_for(i)
        db.add(OfferVariant(offer_id=offer.id, round=offer.round, label=label, style=style))
    await db.flush()
    move_lead(db, lead, LeadStatus.OFFER_DRAFT, actor_id, REASON_START)

    enq = await enqueue_job(
        db,
        type_=JobType.OFFER_GENERATION,
        queue=JobQueue.INTERACTIVE,
        params={
            "offer_id": offer.id,
            "round": offer.round,
            "lead_id": lead.id,
            "project_id": lead.created_from_project_id,
        },
        workspace_id=workspace_id,
        created_by=actor_id,
        progress_total=body.variant_count,
    )
    enq.job.estimated_cost_usd = est.estimated_cost_usd
    enq.job.budget_usd = est.estimated_cost_usd  # hard cap = what the owner confirmed
    offer.job_run_id = enq.job.id
    await db.flush()
    await audit(
        db,
        "offer.generation_started",
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        entity_type="offer",
        entity_id=offer.id,
        diff={
            "lead_id": lead.id,
            "round": offer.round,
            "variants": body.variant_count,
            "model_key": model.key,
            "estimated_cost_usd": str(est.estimated_cost_usd),
            "confirm_token": body.confirm_token,
        },
    )
    out = await offer_out(db, workspace_id, offer, lead_status=lead.status, contacts=contacts)
    return OfferStartResult(offer=out, lead_status=lead.status)


async def _close_interrupted(db: AsyncSession, offer: Offer) -> None:
    """Variants of an earlier round whose job ended without finishing them are closed."""
    await db.execute(
        update(OfferVariant)
        .where(OfferVariant.offer_id == offer.id, OfferVariant.status == OfferVariantStatus.PENDING)
        .values(
            status=OfferVariantStatus.FAILED,
            error="Генерация была прервана (задача отменена или остановлена).",
        )
    )


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def _variant_out(v: OfferVariant) -> OfferVariantOut:
    draft = OfferDraft.model_validate(v.draft) if v.draft else None
    return OfferVariantOut(
        id=v.id,
        round=v.round,
        label=v.label,
        style=v.style,
        status=v.status,
        is_selected=v.is_selected,
        subject=v.subject,
        body=v.body,
        cta=v.cta,
        structure=draft.structure if draft is not None else None,
        claims=draft.claims if draft is not None else [],
        guard_issues=[GuardIssue.model_validate(i) for i in v.guard_issues],
        personalization_score=v.personalization_score,
        personalization_sources=[PersonalizationSource.model_validate(s) for s in v.personalization_sources],
        model_key=v.model_key,
        cost_usd=v.cost_usd,
        error=v.error,
        edited=v.edited_at is not None,
        edited_by=v.edited_by,
        edited_at=v.edited_at,
        created_at=v.created_at,
    )


def content_of(offer: Offer, selected: OfferVariant | None, contact: ContactOut | None) -> dict[str, Any]:
    """Exactly what an approval covers: the text that would be sent, the attachment, the recipient, the
    channel. Changing any of it invalidates the approval."""
    return {
        "offer_id": offer.id,
        "variant_id": selected.id if selected is not None else None,
        "subject": selected.subject if selected is not None else None,
        "body": selected.body if selected is not None else None,
        "cta": selected.cta if selected is not None else None,
        "attachment_variant_id": offer.attachment_variant_id,
        "contact_id": offer.contact_id,
        "contact": f"{contact.type.value}:{contact.value_normalized}" if contact is not None else None,
        "channel": offer.channel.value if offer.channel is not None else None,
    }


def blockers_of(
    offer: Offer, lead_status: LeadStatus, selected: OfferVariant | None, contact: ContactOut | None
) -> list[Blocker]:
    out: list[Blocker] = []
    if offer.status != OfferStatus.PENDING_APPROVAL:
        out.append("status")
    if selected is None or selected.status != OfferVariantStatus.READY:
        out.append("no_variant")
    elif selected.guard_issues:
        out.append("guard_issues")
    if offer.contact_id is None:
        out.append("contact_required")
    elif contact is None or not contact.usable:
        out.append("contact_unusable")
    if offer.channel is None:
        out.append("channel_required")
    elif contact is not None and not channel_fits(offer.channel, contact.type):
        out.append("channel_mismatch")
    if lead_status in CLOSED_LEAD:
        out.append("lead_closed")
    return out


@dataclass(frozen=True)
class OfferState:
    variants: list[OfferVariant]
    selected: OfferVariant | None
    contact: ContactOut | None
    content_hash: str
    blockers: list[Blocker]


async def offer_state(
    db: AsyncSession, offer: Offer, lead_status: LeadStatus, contacts: Sequence[ContactOut]
) -> OfferState:
    variants = list(
        await db.scalars(
            select(OfferVariant)
            .where(OfferVariant.offer_id == offer.id)
            .order_by(OfferVariant.round.desc(), OfferVariant.label)
            .execution_options(populate_existing=True)
        )
    )
    selected = next((v for v in variants if v.is_selected), None)
    contact = next((c for c in contacts if c.id == offer.contact_id), None)
    return OfferState(
        variants=variants,
        selected=selected,
        contact=contact,
        content_hash=approvals.content_hash(content_of(offer, selected, contact)),
        blockers=blockers_of(offer, lead_status, selected, contact),
    )


async def offer_out(
    db: AsyncSession,
    workspace_id: int,
    offer: Offer,
    *,
    lead_status: LeadStatus,
    contacts: Sequence[ContactOut],
    state: OfferState | None = None,
) -> OfferOut:
    await db.flush()
    await db.refresh(offer)  # server-side ``updated_at`` after the caller's changes
    st = state or await offer_state(db, offer, lead_status, contacts)
    inputs = OfferInputs.model_validate(offer.inputs)
    job = await db.get(JobRun, offer.job_run_id) if offer.job_run_id is not None else None
    provider = (
        await db.scalar(select(AIModel.provider).where(AIModel.key == offer.model_key))
        if offer.model_key
        else None
    )
    latest = await approvals.latest_decision(db, workspace_id, ApprovalTarget.OFFER, offer.id)
    channel_id = await db.scalar(select(Lead.channel_id).where(Lead.id == offer.lead_id))
    return OfferOut(
        id=offer.id,
        lead_id=offer.lead_id,
        channel_id=int(channel_id or 0),
        status=offer.status,
        round=offer.round,
        variant_count=offer.variant_count,
        language=offer.language,
        instructions=offer.instructions,
        template=inputs.template,
        inputs=inputs,
        contact=st.contact,
        channel=offer.channel,
        attachment=await _attachment(db, offer.attachment_variant_id),
        manually_edited=offer.manually_edited,
        scheduled_at=offer.scheduled_at,
        skipped_at=offer.skipped_at,
        model_key=offer.model_key,
        estimated_cost_usd=offer.estimated_cost_usd,
        spent_usd=sum((v.cost_usd or Decimal(0) for v in st.variants), Decimal(0)),
        error=offer.error,
        job=JobRunOut.model_validate(job) if job is not None else None,
        provider_is_mock=provider == "mock",
        selected_variant_id=st.selected.id if st.selected is not None else None,
        personalization_score=st.selected.personalization_score if st.selected is not None else None,
        content_hash=st.content_hash,
        blockers=st.blockers,
        can_approve=not st.blockers,
        approval=_approval_out(latest, st.content_hash),
        variants=[_variant_out(v) for v in st.variants],
        created_at=offer.created_at,
        updated_at=offer.updated_at,
    )


def _approval_out(a: Approval | None, current_hash: str) -> ApprovalOut | None:
    if a is None:
        return None
    return ApprovalOut(
        decision=a.decision,
        content_hash=a.content_hash,
        is_current=a.content_hash == current_hash,
        comment=a.comment,
        user_id=a.user_id,
        created_at=a.created_at,
    )


async def _offer(
    db: AsyncSession, workspace_id: int, offer_id: int, *, lock: bool = False
) -> tuple[Offer, Lead]:
    stmt = (
        select(Offer, Lead)
        .join(Lead, Lead.id == Offer.lead_id)
        .where(Offer.id == offer_id, Offer.workspace_id == workspace_id)
    )
    if lock:
        stmt = stmt.with_for_update(of=(Offer, Lead)).execution_options(populate_existing=True)
    row = (await db.execute(stmt)).tuples().first()
    if row is None:
        raise NotFoundError("Offer not found")
    return row


async def get_offer(db: AsyncSession, workspace_id: int, offer_id: int) -> OfferOut:
    offer, lead = await _offer(db, workspace_id, offer_id)
    contacts = (await channel_contacts(db, workspace_id, lead.channel_id)).items
    return await offer_out(db, workspace_id, offer, lead_status=lead.status, contacts=contacts)


async def lead_offers(db: AsyncSession, workspace_id: int, lead_id: int) -> LeadOffers:
    lead, channel = await _lead(db, workspace_id, lead_id)
    contacts = (await channel_contacts(db, workspace_id, channel.id)).items
    offers = list(await db.scalars(select(Offer).where(Offer.lead_id == lead.id).order_by(Offer.id.desc())))
    return LeadOffers(
        lead_id=lead.id,
        lead_status=lead.status,
        items=[
            await offer_out(db, workspace_id, o, lead_status=lead.status, contacts=contacts) for o in offers
        ],
        contacts=list(contacts),
        samples=await sample_candidates(db, lead.id),
    )


# ---------------------------------------------------------------------------
# Edit (ТЗ §23)
# ---------------------------------------------------------------------------
async def edit_offer(
    db: AsyncSession, workspace_id: int, actor_id: int, offer_id: int, body: OfferEdit
) -> OfferOut:
    offer, lead = await _offer(db, workspace_id, offer_id, lock=True)
    if offer.status not in EDITABLE:
        raise ConflictError(
            f"An offer in status '{offer.status.value}' cannot be edited",
            code="offer_not_editable",
            details={"status": offer.status.value},
        )
    contacts = (await channel_contacts(db, workspace_id, lead.channel_id)).items
    before = await offer_state(db, offer, lead.status, contacts)
    sent = body.model_fields_set
    changes: dict[str, Any] = {}
    selected = before.selected

    if (
        "variant_id" in sent
        and body.variant_id is not None
        and (selected is None or selected.id != body.variant_id)
    ):
        target = next((v for v in before.variants if v.id == body.variant_id), None)
        if target is None:
            raise NotFoundError("Variant not found in this offer")
        if target.status != OfferVariantStatus.READY:
            raise ConflictError("Only a generated draft can be selected", code="variant_not_ready")
        await db.execute(
            update(OfferVariant)
            .where(OfferVariant.offer_id == offer.id, OfferVariant.is_selected)
            .values(is_selected=False)
        )
        await db.flush()
        target.is_selected = True
        changes["variant_id"] = {"from": selected.id if selected else None, "to": target.id}
        selected = target

    text_fields = [k for k in ("subject", "body", "cta") if k in sent and getattr(body, k) is not None]
    if text_fields:
        if selected is None or selected.status != OfferVariantStatus.READY:
            raise ConflictError("There is no generated draft to edit", code="variant_not_ready")
        for k in text_fields:
            new = getattr(body, k)
            old = getattr(selected, k)
            if new != old:
                changes[k] = {"from": old, "to": new}
                setattr(selected, k, new)
        if any(k in changes for k in text_fields):
            now = datetime.now(UTC)
            selected.edited_by = actor_id
            selected.edited_at = now
            offer.manually_edited = True

    if "contact_id" in sent and body.contact_id != offer.contact_id:
        if body.contact_id is not None and all(c.id != body.contact_id for c in contacts):
            raise NotFoundError("Contact not found for this channel")
        changes["contact_id"] = {"from": offer.contact_id, "to": body.contact_id}
        offer.contact_id = body.contact_id
        new_contact = next((c for c in contacts if c.id == body.contact_id), None)
        if (
            new_contact is not None
            and "channel" not in sent
            and not channel_fits(offer.channel, new_contact.type)
        ):
            new_channel = CHANNEL_FOR_TYPE.get(new_contact.type, OutreachChannel.MANUAL)
            changes["channel"] = {"from": offer.channel, "to": new_channel}
            offer.channel = new_channel
    if "channel" in sent and body.channel != offer.channel:
        changes["channel"] = {"from": offer.channel, "to": body.channel}
        offer.channel = body.channel
    if "attachment_variant_id" in sent and body.attachment_variant_id != offer.attachment_variant_id:
        if body.attachment_variant_id is not None and all(
            s.variant_id != body.attachment_variant_id for s in await sample_candidates(db, lead.id)
        ):
            raise NotFoundError(
                "The attachment must be an approved or final generated thumbnail of this lead"
            )
        changes["attachment_variant_id"] = {
            "from": offer.attachment_variant_id,
            "to": body.attachment_variant_id,
        }
        offer.attachment_variant_id = body.attachment_variant_id

    if selected is not None and ({"variant_id", *text_fields} & changes.keys()):
        issues, score, sources = check_variant(
            OfferInputs.model_validate(offer.inputs), selected, await other_texts(db, workspace_id, offer.id)
        )
        selected.guard_issues = [i.model_dump() for i in issues]
        selected.personalization_score = score
        selected.personalization_sources = [s.model_dump() for s in sources]
    await db.flush()

    after = await offer_state(db, offer, lead.status, contacts)
    previous = offer.status
    if after.content_hash != before.content_hash and previous in (
        OfferStatus.APPROVED,
        OfferStatus.SCHEDULED,
    ):
        move_offer(db, offer, OfferStatus.PENDING_APPROVAL, actor_id, REASON_EDITED)
        offer.scheduled_at = None
        move_lead(db, lead, LeadStatus.PENDING_APPROVAL, actor_id, REASON_EDITED)
        changes["status"] = {"from": previous.value, "to": OfferStatus.PENDING_APPROVAL.value}
    if changes:
        safe = _jsonable(changes)
        db.add(
            FeedbackEvent(
                workspace_id=workspace_id,
                user_id=actor_id,
                kind=FeedbackKind.OFFER_EDIT,
                target_type="offer",
                target_id=offer.id,
                field="content",
                payload={
                    "changes": safe,
                    "lead_id": lead.id,
                    "round": offer.round,
                    "style": selected.style.value if selected is not None else None,
                },
            )
        )
        await audit(
            db,
            "offer.edited",
            workspace_id=workspace_id,
            actor_user_id=actor_id,
            entity_type="offer",
            entity_id=offer.id,
            diff=safe,
        )
    await db.flush()
    return await offer_out(db, workspace_id, offer, lead_status=lead.status, contacts=contacts)


def _jsonable(changes: dict[str, Any]) -> dict[str, Any]:
    def conv(x: Any) -> Any:
        if isinstance(x, OutreachChannel):
            return x.value
        if isinstance(x, dict):
            return {k: conv(v) for k, v in x.items()}
        return x

    return {k: conv(v) for k, v in changes.items()}


# ---------------------------------------------------------------------------
# Decisions (ТЗ §22: Approve / Reject / Skip) and Schedule
# ---------------------------------------------------------------------------
async def decide_offer(
    db: AsyncSession, workspace_id: int, actor_id: int, offer_id: int, body: OfferDecisionIn
) -> OfferOut:
    offer, lead = await _offer(db, workspace_id, offer_id, lock=True)
    contacts = (await channel_contacts(db, workspace_id, lead.channel_id)).items
    st = await offer_state(db, offer, lead.status, contacts)
    previous = offer.status
    if body.decision == "approve":
        if body.content_hash != st.content_hash:
            raise ConflictError(
                "The offer changed since you opened it; review it again",
                code="content_changed",
                details={"content_hash": st.content_hash},
            )
        if st.blockers:
            raise ConflictError(
                "The offer cannot be approved yet",
                code="offer_not_approvable",
                details={"blockers": list(st.blockers)},
            )
        await approvals.record_decision(
            db,
            workspace_id,
            actor_id,
            ApprovalTarget.OFFER,
            offer.id,
            ApprovalDecision.APPROVED,
            st.content_hash,
            body.comment,
        )
        move_offer(db, offer, OfferStatus.APPROVED, actor_id, body.comment or "approve")
        offer.skipped_at = None
        move_lead(db, lead, LeadStatus.APPROVED, actor_id, "оффер подтверждён")
    elif body.decision == "reject":
        if offer.status not in EDITABLE:
            raise ConflictError(
                f"An offer in status '{offer.status.value}' cannot be rejected",
                code="offer_status",
                details={"status": offer.status.value},
            )
        await approvals.record_decision(
            db,
            workspace_id,
            actor_id,
            ApprovalTarget.OFFER,
            offer.id,
            ApprovalDecision.REJECTED,
            st.content_hash,
            body.comment,
        )
        move_offer(db, offer, OfferStatus.REJECTED, actor_id, body.comment or "reject")
        offer.scheduled_at = None
        if lead.status not in CLOSED_LEAD:
            move_lead(db, lead, LeadStatus.OFFER_DRAFT, actor_id, "оффер отклонён")
    else:  # skip: stays in the queue, at its end
        if offer.status != OfferStatus.PENDING_APPROVAL:
            raise ConflictError(
                "Only an offer waiting for approval can be skipped",
                code="offer_status",
                details={"status": offer.status.value},
            )
        await approvals.record_decision(
            db,
            workspace_id,
            actor_id,
            ApprovalTarget.OFFER,
            offer.id,
            ApprovalDecision.SKIPPED,
            st.content_hash,
            body.comment,
        )
        offer.skipped_at = datetime.now(UTC)
    sel = st.selected
    db.add(
        FeedbackEvent(
            workspace_id=workspace_id,
            user_id=actor_id,
            kind=FeedbackKind.OFFER_DECISION,
            target_type="offer",
            target_id=offer.id,
            field="decision",
            ai_value=sel.personalization_score if sel is not None else None,
            payload={
                "decision": body.decision,
                "from": previous.value,
                "to": offer.status.value,
                "lead_id": lead.id,
                "round": offer.round,
                "label": sel.label if sel is not None else None,
                "style": sel.style.value if sel is not None else None,
                "manually_edited": offer.manually_edited,
                "guard_issues": len(sel.guard_issues) if sel is not None else 0,
                "personalization_score": str(sel.personalization_score)
                if sel is not None and sel.personalization_score is not None
                else None,
                "comment": body.comment,
            },
        )
    )
    await db.flush()
    return await offer_out(db, workspace_id, offer, lead_status=lead.status, contacts=contacts)


async def schedule_offer(
    db: AsyncSession, workspace_id: int, actor_id: int, offer_id: int, body: OfferScheduleIn
) -> OfferOut:
    """Store the send time of an approved offer (sending is Phase 8). The approval must match the current
    content and the contact must still be usable."""
    offer, lead = await _offer(db, workspace_id, offer_id, lock=True)
    if offer.status not in (OfferStatus.APPROVED, OfferStatus.SCHEDULED):
        raise ConflictError(
            "Only an approved offer can be scheduled",
            code="offer_status",
            details={"status": offer.status.value},
        )
    if body.scheduled_at <= datetime.now(UTC):
        raise AppError("scheduled_at must be in the future", code="invalid_schedule")
    contacts = (await channel_contacts(db, workspace_id, lead.channel_id)).items
    st = await offer_state(db, offer, lead.status, contacts)
    await approvals.require_approval(db, workspace_id, ApprovalTarget.OFFER, offer.id, st.content_hash)
    blockers = [b for b in st.blockers if b != "status"]
    if blockers:
        raise ConflictError(
            "The offer cannot be scheduled", code="offer_not_approvable", details={"blockers": blockers}
        )
    previous = offer.scheduled_at
    move_offer(db, offer, OfferStatus.SCHEDULED, actor_id, "schedule")
    offer.scheduled_at = body.scheduled_at
    move_lead(db, lead, LeadStatus.SCHEDULED, actor_id, "оффер запланирован")
    await db.flush()
    await audit(
        db,
        "offer.scheduled",
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        entity_type="offer",
        entity_id=offer.id,
        diff={
            "from": previous.isoformat() if previous else None,
            "to": body.scheduled_at.isoformat(),
            "content_hash": st.content_hash,
        },
    )
    return await offer_out(db, workspace_id, offer, lead_status=lead.status, contacts=contacts)


async def unschedule_offer(db: AsyncSession, workspace_id: int, actor_id: int, offer_id: int) -> OfferOut:
    offer, lead = await _offer(db, workspace_id, offer_id, lock=True)
    if offer.status != OfferStatus.SCHEDULED:
        raise ConflictError(
            "The offer is not scheduled", code="offer_status", details={"status": offer.status.value}
        )
    move_offer(db, offer, OfferStatus.APPROVED, actor_id, "unschedule")
    offer.scheduled_at = None
    move_lead(db, lead, LeadStatus.APPROVED, actor_id, "планирование отменено")
    await db.flush()
    await audit(
        db,
        "offer.unscheduled",
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        entity_type="offer",
        entity_id=offer.id,
        diff={},
    )
    contacts = (await channel_contacts(db, workspace_id, lead.channel_id)).items
    return await offer_out(db, workspace_id, offer, lead_status=lead.status, contacts=contacts)


# ---------------------------------------------------------------------------
# Approval Queue (ТЗ §22)
# ---------------------------------------------------------------------------
async def _card(
    db: AsyncSession, workspace_id: int, offer: Offer, lead: Lead, channel: Channel, score: LeadScore | None
) -> QueueCard:
    contacts = (await channel_contacts(db, workspace_id, channel.id)).items
    out = await offer_out(db, workspace_id, offer, lead_status=lead.status, contacts=contacts)
    candidates = await source_candidates(db, workspace_id, channel.id)
    source: SourceCandidate | None = None
    if out.attachment is not None:
        video_id = await db.scalar(
            select(GenerationRequest.source_video_id).where(
                GenerationRequest.id == out.attachment.generation_request_id
            )
        )
        source = next((c for c in candidates if c.video_id == video_id), None)
    source = source or default_source(candidates)
    channel_audit = await db.scalar(
        select(ChannelAnalysis).where(ChannelAnalysis.channel_id == channel.id, ChannelAnalysis.is_current)
    )
    analysis: QueueAnalysis | None = None
    if channel_audit is not None:
        analysis = QueueAnalysis(
            summary=channel_audit.summary,
            visual_opportunity=channel_audit.visual_opportunity.value,
            problem_codes=list(channel_audit.recurring_problem_codes),
        )
    elif source is not None:
        thumb = await _current_audit(db, source.video_id)
        if thumb is not None:
            analysis = QueueAnalysis(
                summary=thumb.visual_summary,
                visual_opportunity=thumb.improvement_opportunity.value,
                problem_codes=list(thumb.problem_codes),
            )
    return QueueCard(
        offer=out,
        channel=QueueChannel(
            id=channel.id,
            title=channel.title,
            handle=channel.handle,
            url=channel.url,
            avatar_url=channel.avatar_url,
            subscriber_count=None if channel.subscribers_hidden else channel.subscriber_count,
        ),
        lead=QueueLead(
            id=lead.id,
            status=lead.status,
            lead_score=score.total if score is not None else None,
            priority_score=score.priority_score if score is not None else None,
            contactability_score=score.contactability_score if score is not None else None,
        ),
        thumbnail=QueueThumbnail(
            video_id=source.video_id,
            title=source.title,
            image_url=source.image_url,
            overall_score=source.overall_score,
            problem_codes=source.problem_codes,
        )
        if source is not None
        else None,
        analysis=analysis,
        contacts=list(contacts),
        samples=await sample_candidates(db, lead.id),
    )


async def approval_queue(
    db: AsyncSession,
    workspace_id: int,
    *,
    status: QueueStatus,
    project_id: int | None,
    limit: int,
    offset: int,
) -> ApprovalQueue:
    """Offers of open leads in ``status``. Waiting offers: not skipped first, then by Priority Score."""
    conds: list[ColumnElement[bool]] = [
        Offer.workspace_id == workspace_id,
        Offer.status == OfferStatus(status),
        Lead.status.not_in(list(CLOSED_LEAD)),
    ]
    if project_id is not None:
        conds.append(
            exists(
                select(ProjectChannel.channel_id).where(
                    ProjectChannel.project_id == project_id, ProjectChannel.channel_id == Lead.channel_id
                )
            )
        )
    base = (
        select(Offer, Lead, Channel, LeadScore)
        .join(Lead, Lead.id == Offer.lead_id)
        .join(Channel, Channel.id == Lead.channel_id)
        .outerjoin(LeadScore, and_(LeadScore.lead_id == Lead.id, LeadScore.is_current))
        .where(*conds)
    )
    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = await db.execute(
        base.order_by(
            Offer.skipped_at.asc().nulls_first(),
            LeadScore.priority_score.desc().nulls_last(),
            Offer.id,
        )
        .limit(limit)
        .offset(offset)
    )
    items = [await _card(db, workspace_id, o, lead, ch, sc) for o, lead, ch, sc in rows.tuples()]
    counts_rows = await db.execute(
        select(Offer.status, func.count())
        .join(Lead, Lead.id == Offer.lead_id)
        .where(Offer.workspace_id == workspace_id, Lead.status.not_in(list(CLOSED_LEAD)))
        .group_by(Offer.status)
    )
    counts = {s.value: 0 for s in OfferStatus}
    counts.update({s.value: int(n) for s, n in counts_rows.tuples()})
    return ApprovalQueue(items=items, total=total, limit=limit, offset=offset, counts=counts)
