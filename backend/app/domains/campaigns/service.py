"""Campaigns (Phase 8.2, ТЗ §28–29, §35, §59; ADR-0010, ADR-0011).

* **Setup** — name, project, offer template / instructions / language, the A/B styles, allowed channels,
  senders, the send window and limits. ``draft`` ⇄ ``ready`` follows whether it can be started
  (``issues``: an active lead; an active email account when email is allowed).
* **Leads** — chosen leads or «all filtered» (like the leads bulk actions). A lead is active in at most one
  campaign; closed or already contacted leads are skipped. Each new lead gets the A/B style with the
  fewest leads so far (ties at random). A lead that already has an open offer keeps it (no new cost).
* **Offers** — estimate → ``confirm_token`` → start (§76): one draft per lead in its style, one
  ``offer_generation`` job per lead on the bulk queue, the per-offer price as each job's hard cap. Every
  offer then waits in the Approval Queue like any other — nothing is approved here.
* **Run** (``tick``, every minute before the dispatcher): a *running* campaign queues the message of each
  offer a human approved (once per approval; a cancelled or failed one is not retried automatically) for
  the next moment inside its window, from its least loaded sender. Outcomes are synced from the offers:
  sent / rejected / failed / skipped (lead closed). With no active lead left the campaign completes.
* **Pause / complete** — pause keeps the queue (messages wait); complete and archive cancel the queued
  messages (the offers stay approved) and release the remaining leads.
"""

from __future__ import annotations

import hashlib
import secrets as pysecrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.state_machine import LeadStatus
from app.domains.ai import budget
from app.domains.ai.cost import pricing_known, token_cost
from app.domains.ai.models import AIModel
from app.domains.ai.prompts import load_prompt
from app.domains.ai.registry import TASKS, resolve_models
from app.domains.ai.runner import AINotConfiguredError
from app.domains.analysis.service import budget_checks
from app.domains.approvals import service as approvals
from app.domains.approvals.models import ApprovalTarget
from app.domains.campaigns import rules
from app.domains.campaigns.models import (
    CAMPAIGN_MACHINE,
    GENERATING_CAMPAIGN,
    OPEN_CAMPAIGN,
    Campaign,
    CampaignEmailAccount,
    CampaignLead,
    CampaignLeadStatus,
    CampaignStatus,
)
from app.domains.campaigns.schemas import (
    CampaignAccountRef,
    CampaignCounts,
    CampaignGenerationEstimate,
    CampaignGenerationResult,
    CampaignGenerationStart,
    CampaignIn,
    CampaignLeadList,
    CampaignLeadOut,
    CampaignLeadsIn,
    CampaignLeadsResult,
    CampaignList,
    CampaignOut,
    CampaignPatch,
    CampaignStage,
    CampaignStats,
    Issue,
    VariantStats,
)
from app.domains.contacts.service import channel_contacts
from app.domains.identity.service import audit
from app.domains.jobs.service import JobQueue
from app.domains.leads.models import Lead
from app.domains.leads.workflow import resolve_selection
from app.domains.offers import service as offers
from app.domains.offers.drafting import OfferDraft
from app.domains.offers.models import (
    OPEN_STATUSES,
    Offer,
    OfferStatus,
    OfferStyle,
    OfferTemplate,
    OfferVariant,
    OutreachChannel,
)
from app.domains.offers.schemas import OfferIn
from app.domains.outreach import service as outreach
from app.domains.outreach.accounts import sent_today as account_sent_today
from app.domains.outreach.models import (
    AccountStatus,
    EmailAccount,
    Message,
    MessageEvent,
    MessageEventType,
    MessageStatus,
)
from app.domains.projects.models import SearchProject
from app.domains.youtube.models import Channel

MAX_GENERATION_BATCH = 200  # offers started per «Generate» (the rest in the next run)
TICK_QUEUE_LIMIT = 100  # approved offers queued per campaign per minute
MIN_SAMPLE = 30  # sent messages per variant before a difference is worth looking at
STATS_NOTE = (
    "Это счётчики, а не выводы: разница между вариантами может быть случайной. "
    f"Пока у каждого варианта меньше {MIN_SAMPLE} отправленных писем, сравнивать доли ответов бессмысленно; "
    "лиды распределяются по вариантам случайно, но ниши, время отправки и ручные правки текста тоже влияют "
    "на результат."
)
# leads that are not added: closed, or already contacted
NOT_ADDABLE = frozenset(
    {LeadStatus.REJECTED, LeadStatus.OPTED_OUT, LeadStatus.SENT, LeadStatus.REPLIED, LeadStatus.DECLINED}
)
REPLIED_LEAD = frozenset({LeadStatus.REPLIED, LeadStatus.DECLINED})
_A = CampaignLeadStatus.ACTIVE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _styles(c: Campaign) -> list[OfferStyle]:
    return [OfferStyle(s) for s in c.styles]


def _channels(c: Campaign) -> list[OutreachChannel]:
    return [OutreachChannel(s) for s in c.channels]


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def pick_style(counts: dict[OfferStyle, int], styles: Sequence[OfferStyle]) -> OfferStyle:
    """Balanced random A/B assignment: one of the styles with the fewest leads so far."""
    least = min(counts.get(s, 0) for s in styles)
    return pysecrets.choice([s for s in styles if counts.get(s, 0) == least])


def move_campaign(c: Campaign, target: CampaignStatus) -> None:
    if c.status != target:
        CAMPAIGN_MACHINE.assert_can(c.status, target)
        c.status = target


def _require(c: Campaign, allowed: frozenset[CampaignStatus] | set[CampaignStatus], what: str) -> None:
    if c.status not in allowed:
        raise ConflictError(
            f"{what} is not possible for a campaign in status '{c.status.value}'",
            code="campaign_status",
            details={"status": c.status.value},
        )


async def _campaign(db: AsyncSession, workspace_id: int, campaign_id: int, *, lock: bool = False) -> Campaign:
    stmt = select(Campaign).where(Campaign.id == campaign_id, Campaign.workspace_id == workspace_id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = await db.scalar(stmt)
    if row is None:
        raise NotFoundError("Campaign not found")
    return row


async def _account_ids(db: AsyncSession, campaign_id: int) -> list[int]:
    return list(
        await db.scalars(
            select(CampaignEmailAccount.email_account_id)
            .where(CampaignEmailAccount.campaign_id == campaign_id)
            .order_by(CampaignEmailAccount.email_account_id)
        )
    )


async def _senders(db: AsyncSession, c: Campaign) -> list[EmailAccount]:
    """Active accounts the campaign may send from: its own, or any of the workspace when none is set."""
    ids = await _account_ids(db, c.id)
    conds: list[ColumnElement[bool]] = [
        EmailAccount.workspace_id == c.workspace_id,
        EmailAccount.status == AccountStatus.ACTIVE,
    ]
    if ids:
        conds.append(EmailAccount.id.in_(ids))
    return list(await db.scalars(select(EmailAccount).where(*conds).order_by(EmailAccount.id)))


async def _check_refs(
    db: AsyncSession,
    workspace_id: int,
    *,
    project_id: int | None,
    template_id: int | None,
    account_ids: Sequence[int] | None,
) -> None:
    if project_id is not None and (
        await db.scalar(
            select(SearchProject.id).where(
                SearchProject.id == project_id, SearchProject.workspace_id == workspace_id
            )
        )
        is None
    ):
        raise NotFoundError("Project not found")
    if template_id is not None and (
        await db.scalar(
            select(OfferTemplate.id).where(
                OfferTemplate.id == template_id,
                OfferTemplate.workspace_id == workspace_id,
                OfferTemplate.is_archived.is_(False),
            )
        )
        is None
    ):
        raise NotFoundError("Offer template not found")
    if account_ids:
        found = set(
            await db.scalars(
                select(EmailAccount.id).where(
                    EmailAccount.id.in_(list(account_ids)), EmailAccount.workspace_id == workspace_id
                )
            )
        )
        missing = [i for i in account_ids if i not in found]
        if missing:
            raise NotFoundError("Email account not found", details={"email_account_ids": missing})


async def _current_template_id(db: AsyncSession, workspace_id: int, template_id: int | None) -> int | None:
    """The newest active version of the campaign's template (editing a template adds a version)."""
    if template_id is None:
        return None
    key = await db.scalar(
        select(OfferTemplate.key).where(
            OfferTemplate.id == template_id, OfferTemplate.workspace_id == workspace_id
        )
    )
    current = await db.scalar(
        select(OfferTemplate.id)
        .where(
            OfferTemplate.workspace_id == workspace_id,
            OfferTemplate.key == key,
            OfferTemplate.is_archived.is_(False),
        )
        .order_by(OfferTemplate.version.desc())
        .limit(1)
    )
    if current is None:
        raise ConflictError(
            "The campaign's offer template was archived: choose another one", code="template_archived"
        )
    return int(current)


async def _active_count(db: AsyncSession, campaign_id: int) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(CampaignLead)
            .where(CampaignLead.campaign_id == campaign_id, CampaignLead.status == _A)
        )
        or 0
    )


async def issues_of(db: AsyncSession, c: Campaign) -> list[Issue]:
    out: list[Issue] = []
    if await _active_count(db, c.id) == 0:
        out.append("no_leads")
    if OutreachChannel.EMAIL.value in c.channels and not await _senders(db, c):
        out.append("email_account_required")
    return out


async def _refresh(db: AsyncSession, c: Campaign) -> None:
    """``draft`` ⇄ ``ready`` follows whether the campaign could be started now."""
    if c.status not in (CampaignStatus.DRAFT, CampaignStatus.READY):
        return
    await db.flush()
    ok = not await issues_of(db, c)
    if c.status == CampaignStatus.DRAFT and ok:
        move_campaign(c, CampaignStatus.READY)
    elif c.status == CampaignStatus.READY and not ok:
        move_campaign(c, CampaignStatus.DRAFT)


def _finish(cl: CampaignLead, status: CampaignLeadStatus, reason: str | None) -> None:
    cl.status, cl.reason, cl.finished_at = status, reason, datetime.now(UTC)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _Row:
    cl: CampaignLead
    lead: Lead
    channel: Channel
    offer: Offer | None
    message: Message | None  # the latest message of the offer in this campaign


_FINISHED_STAGE: dict[CampaignLeadStatus, CampaignStage] = {
    CampaignLeadStatus.FAILED: "failed",
    CampaignLeadStatus.REJECTED: "rejected",
    CampaignLeadStatus.SKIPPED: "skipped",
    CampaignLeadStatus.REMOVED: "removed",
}


def stage_of(
    cl: CampaignLead, lead_status: LeadStatus, offer: Offer | None, message: Message | None
) -> CampaignStage:
    if cl.status == CampaignLeadStatus.SENT:
        return "replied" if lead_status in REPLIED_LEAD else "sent"
    if cl.status != _A:
        return _FINISHED_STAGE[cl.status]
    if offer is None:
        return "no_offer"
    if offer.status == OfferStatus.DRAFT:
        return "drafting"
    if offer.status == OfferStatus.FAILED:
        return "generation_failed"
    if offer.status == OfferStatus.PENDING_APPROVAL:
        return "review"
    if offer.status == OfferStatus.REJECTED:
        return "rejected"
    if offer.status == OfferStatus.SENT:
        return "sent"
    if message is not None:
        if message.status in (MessageStatus.QUEUED, MessageStatus.SENDING):
            return "queued"
        if message.status == MessageStatus.MANUAL:
            return "manual"
        if message.status == MessageStatus.FAILED:
            return "failed"
        if message.status == MessageStatus.CANCELLED and offer.status == OfferStatus.APPROVED:
            return "send_cancelled"
    return "approved"


async def _latest_messages(
    db: AsyncSession, campaign_id: int, offer_ids: Sequence[int]
) -> dict[int, Message]:
    if not offer_ids:
        return {}
    latest = (
        select(func.max(Message.id))
        .where(Message.campaign_id == campaign_id, Message.offer_id.in_(list(offer_ids)))
        .group_by(Message.offer_id)
    )
    return {m.offer_id: m for m in await db.scalars(select(Message).where(Message.id.in_(latest)))}


async def _rows(
    db: AsyncSession,
    campaign_id: int,
    conds: Sequence[ColumnElement[bool]] = (),
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[_Row]:
    stmt = (
        select(CampaignLead, Lead, Channel, Offer)
        .join(Lead, Lead.id == CampaignLead.lead_id)
        .join(Channel, Channel.id == Lead.channel_id)
        .outerjoin(Offer, Offer.id == CampaignLead.offer_id)
        .where(CampaignLead.campaign_id == campaign_id, *conds)
        .order_by(CampaignLead.created_at, CampaignLead.lead_id)
        .offset(offset)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    raw = (await db.execute(stmt)).tuples().all()
    messages = await _latest_messages(db, campaign_id, [o.id for *_, o in raw if o is not None])
    return [
        _Row(cl=cl, lead=lead, channel=ch, offer=o, message=messages.get(o.id) if o is not None else None)
        for cl, lead, ch, o in raw
    ]


async def _counts(db: AsyncSession, campaign_id: int) -> CampaignCounts:
    by_status = {s: 0 for s in CampaignLeadStatus}
    for s, n in (
        await db.execute(
            select(CampaignLead.status, func.count())
            .where(CampaignLead.campaign_id == campaign_id)
            .group_by(CampaignLead.status)
        )
    ).tuples():
        by_status[s] = int(n)
    stages: dict[str, int] = {}
    for r in await _rows(db, campaign_id, [CampaignLead.status == _A]):
        stage = stage_of(r.cl, r.lead.status, r.offer, r.message)
        stages[stage] = stages.get(stage, 0) + 1
    after = {
        s: int(n)
        for s, n in (
            await db.execute(
                select(Lead.status, func.count())
                .join(CampaignLead, CampaignLead.lead_id == Lead.id)
                .where(
                    CampaignLead.campaign_id == campaign_id,
                    CampaignLead.status == CampaignLeadStatus.SENT,
                )
                .group_by(Lead.status)
            )
        ).tuples()
    }
    return CampaignCounts(
        leads=sum(n for s, n in by_status.items() if s != CampaignLeadStatus.REMOVED),
        active=by_status[_A],
        sent=by_status[CampaignLeadStatus.SENT],
        failed=by_status[CampaignLeadStatus.FAILED],
        rejected=by_status[CampaignLeadStatus.REJECTED],
        skipped=by_status[CampaignLeadStatus.SKIPPED],
        removed=by_status[CampaignLeadStatus.REMOVED],
        stages=stages,
        replied=sum(after.get(s, 0) for s in REPLIED_LEAD),
        opted_out=after.get(LeadStatus.OPTED_OUT, 0),
    )


async def campaign_out(db: AsyncSession, c: Campaign, now: datetime | None = None) -> CampaignOut:
    now = now or datetime.now(UTC)
    await db.flush()
    await db.refresh(c)
    ids = await _account_ids(db, c.id)
    accounts = (
        list(await db.scalars(select(EmailAccount).where(EmailAccount.id.in_(ids)).order_by(EmailAccount.id)))
        if ids
        else []
    )
    project = (
        await db.scalar(select(SearchProject.name).where(SearchProject.id == c.project_id))
        if c.project_id
        else None
    )
    template = (
        await db.scalar(select(OfferTemplate.name).where(OfferTemplate.id == c.template_id))
        if c.template_id
        else None
    )
    return CampaignOut(
        id=c.id,
        name=c.name,
        description=c.description,
        status=c.status,
        project_id=c.project_id,
        project_name=project,
        template_id=c.template_id,
        template_name=template,
        instructions=c.instructions,
        language=c.language,
        styles=_styles(c),
        channels=_channels(c),
        email_accounts=[
            CampaignAccountRef(id=a.id, name=a.name, from_email=a.from_email, status=a.status.value)
            for a in accounts
        ],
        send_days=sorted(c.send_days),
        send_from=c.send_from,
        send_to=c.send_to,
        timezone=c.timezone,
        starts_at=c.starts_at,
        daily_limit=c.daily_limit,
        sent_today=(await rules.sent_today(db, [c], now))[c.id],
        max_consecutive_failures=c.max_consecutive_failures,
        consecutive_failures=c.consecutive_failures,
        paused_reason=c.paused_reason,
        in_window_now=rules.in_window(c, now),
        next_window_at=rules.next_window_start(c, now),
        issues=await issues_of(db, c),
        target_filters=c.target_filters,
        counts=await _counts(db, c.id),
        started_at=c.started_at,
        completed_at=c.completed_at,
        archived_at=c.archived_at,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


async def list_campaigns(
    db: AsyncSession, workspace_id: int, *, status: CampaignStatus | None, include_archived: bool
) -> CampaignList:
    conds: list[ColumnElement[bool]] = [Campaign.workspace_id == workspace_id]
    if status is not None:
        conds.append(Campaign.status == status)
    elif not include_archived:
        conds.append(Campaign.status != CampaignStatus.ARCHIVED)
    rows = list(
        await db.scalars(
            select(Campaign).where(*conds).order_by(Campaign.created_at.desc(), Campaign.id.desc())
        )
    )
    counts = {s.value: 0 for s in CampaignStatus}
    for s, n in (
        await db.execute(
            select(Campaign.status, func.count())
            .where(Campaign.workspace_id == workspace_id)
            .group_by(Campaign.status)
        )
    ).tuples():
        counts[s.value] = int(n)
    now = datetime.now(UTC)
    return CampaignList(items=[await campaign_out(db, c, now) for c in rows], counts=counts)


async def get_campaign(db: AsyncSession, workspace_id: int, campaign_id: int) -> CampaignOut:
    return await campaign_out(db, await _campaign(db, workspace_id, campaign_id))


async def list_campaign_leads(
    db: AsyncSession,
    workspace_id: int,
    campaign_id: int,
    *,
    status: CampaignLeadStatus | None,
    limit: int,
    offset: int,
) -> CampaignLeadList:
    c = await _campaign(db, workspace_id, campaign_id)
    conds: list[ColumnElement[bool]] = []
    if status is not None:
        conds.append(CampaignLead.status == status)
    total = int(
        await db.scalar(
            select(func.count()).select_from(CampaignLead).where(CampaignLead.campaign_id == c.id, *conds)
        )
        or 0
    )
    rows = await _rows(db, c.id, conds, limit=limit, offset=offset)
    return CampaignLeadList(
        items=[
            CampaignLeadOut(
                lead_id=r.lead.id,
                channel_id=r.channel.id,
                channel_title=r.channel.title,
                lead_status=r.lead.status,
                style=r.cl.style,
                label=offers.label_of(r.cl.style),
                status=r.cl.status,
                stage=stage_of(r.cl, r.lead.status, r.offer, r.message),
                reason=r.cl.reason,
                offer_id=r.offer.id if r.offer is not None else None,
                offer_status=r.offer.status if r.offer is not None else None,
                message_id=r.message.id if r.message is not None else None,
                message_status=r.message.status if r.message is not None else None,
                sent_at=r.message.sent_at if r.message is not None else None,
                added_at=r.cl.created_at,
            )
            for r in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# Create / edit
# ---------------------------------------------------------------------------
async def _set_accounts(db: AsyncSession, campaign_id: int, account_ids: Sequence[int]) -> None:
    for row in await db.scalars(
        select(CampaignEmailAccount).where(CampaignEmailAccount.campaign_id == campaign_id)
    ):
        await db.delete(row)
    await db.flush()
    for aid in dict.fromkeys(account_ids):
        db.add(CampaignEmailAccount(campaign_id=campaign_id, email_account_id=aid))


def _check_window(send_from: time, send_to: time) -> None:
    if send_from >= send_to:
        raise AppError(
            "The send window must end after it starts (send_from < send_to)", code="invalid_window"
        )


async def create_campaign(
    db: AsyncSession, workspace_id: int, actor_id: int, body: CampaignIn
) -> CampaignOut:
    await _check_refs(
        db,
        workspace_id,
        project_id=body.project_id,
        template_id=body.template_id,
        account_ids=body.email_account_ids,
    )
    _check_window(body.send_from, body.send_to)
    c = Campaign(
        workspace_id=workspace_id,
        project_id=body.project_id,
        name=body.name,
        description=body.description,
        status=CampaignStatus.DRAFT,
        target_filters={},
        template_id=body.template_id,
        instructions=body.instructions,
        language=body.language,
        styles=[s.value for s in body.styles],
        channels=[ch.value for ch in body.channels],
        send_days=sorted(body.send_days),
        send_from=body.send_from,
        send_to=body.send_to,
        timezone=body.timezone,
        starts_at=body.starts_at,
        daily_limit=body.daily_limit,
        max_consecutive_failures=body.max_consecutive_failures,
        created_by=actor_id,
    )
    db.add(c)
    await db.flush()
    await _set_accounts(db, c.id, body.email_account_ids)
    await audit(
        db,
        "campaign.created",
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        entity_type="campaign",
        entity_id=c.id,
        diff=body.model_dump(mode="json"),
    )
    return await campaign_out(db, c)


async def update_campaign(
    db: AsyncSession, workspace_id: int, actor_id: int, campaign_id: int, body: CampaignPatch
) -> CampaignOut:
    c = await _campaign(db, workspace_id, campaign_id, lock=True)
    _require(c, OPEN_CAMPAIGN, "Editing")
    sent = body.model_fields_set
    await _check_refs(
        db,
        workspace_id,
        project_id=body.project_id if "project_id" in sent else None,
        template_id=body.template_id if "template_id" in sent else None,
        account_ids=body.email_account_ids if "email_account_ids" in sent else None,
    )
    for field in (
        "name",
        "description",
        "instructions",
        "send_from",
        "send_to",
        "timezone",
        "daily_limit",
        "max_consecutive_failures",
    ):
        value = getattr(body, field)
        if field in sent and value is not None:
            setattr(c, field, value)
    for field in ("project_id", "template_id", "language", "starts_at"):  # null clears
        if field in sent:
            setattr(c, field, getattr(body, field))
    if "styles" in sent and body.styles is not None:
        c.styles = [s.value for s in body.styles]
    if "channels" in sent and body.channels is not None:
        c.channels = [ch.value for ch in body.channels]
    if "send_days" in sent and body.send_days is not None:
        c.send_days = sorted(body.send_days)
    _check_window(c.send_from, c.send_to)
    if "email_account_ids" in sent and body.email_account_ids is not None:
        await _set_accounts(db, c.id, body.email_account_ids)
    await _refresh(db, c)
    await audit(
        db,
        "campaign.updated",
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        entity_type="campaign",
        entity_id=c.id,
        diff=body.model_dump(mode="json", include=sent),
    )
    return await campaign_out(db, c)


# ---------------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------------
async def add_leads(
    db: AsyncSession, workspace_id: int, actor_id: int, campaign_id: int, body: CampaignLeadsIn
) -> CampaignLeadsResult:
    c = await _campaign(db, workspace_id, campaign_id, lock=True)
    _require(c, OPEN_CAMPAIGN, "Adding leads")
    ids = await resolve_selection(db, workspace_id, actor_id, body.selection)
    styles = _styles(c)
    skipped = {"already_in_campaign": 0, "other_campaign": 0, "lead_status": 0}
    assigned: dict[str, int] = {}
    counts: dict[OfferStyle, int] = {
        s: int(n)
        for s, n in (
            await db.execute(
                select(CampaignLead.style, func.count())
                .where(CampaignLead.campaign_id == c.id, CampaignLead.status != CampaignLeadStatus.REMOVED)
                .group_by(CampaignLead.style)
            )
        ).tuples()
    }
    added = 0
    for start in range(0, len(ids), 500):
        batch = ids[start : start + 500]
        # lock the leads (id order) so two campaigns cannot take the same lead at once
        leads = {
            lead.id: lead
            for lead in await db.scalars(
                select(Lead).where(Lead.id.in_(batch)).order_by(Lead.id).with_for_update()
            )
        }
        mine = {
            cl.lead_id: cl
            for cl in await db.scalars(
                select(CampaignLead).where(CampaignLead.campaign_id == c.id, CampaignLead.lead_id.in_(batch))
            )
        }
        elsewhere = set(
            await db.scalars(
                select(CampaignLead.lead_id).where(
                    CampaignLead.lead_id.in_(batch),
                    CampaignLead.status == _A,
                    CampaignLead.campaign_id != c.id,
                )
            )
        )
        open_offers = {
            lead_id: offer_id
            for lead_id, offer_id in (
                await db.execute(
                    select(Offer.lead_id, Offer.id).where(
                        Offer.lead_id.in_(batch), Offer.status.in_(OPEN_STATUSES)
                    )
                )
            ).tuples()
        }
        for lead_id in batch:
            existing = mine.get(lead_id)
            if existing is not None and existing.status not in (
                CampaignLeadStatus.REMOVED,
                CampaignLeadStatus.SKIPPED,
            ):
                skipped["already_in_campaign"] += 1
                continue
            if lead_id in elsewhere:
                skipped["other_campaign"] += 1
                continue
            if leads[lead_id].status in NOT_ADDABLE:
                skipped["lead_status"] += 1
                continue
            if existing is not None:  # back in the campaign: keeps its style
                style = existing.style
                if existing.status == CampaignLeadStatus.REMOVED:  # removed ones are not counted
                    counts[style] = counts.get(style, 0) + 1
                existing.status, existing.reason, existing.finished_at = _A, None, None
                existing.offer_id = open_offers.get(lead_id, existing.offer_id)
                existing.added_by = actor_id
            else:
                style = pick_style(counts, styles)
                counts[style] = counts.get(style, 0) + 1
                db.add(
                    CampaignLead(
                        campaign_id=c.id,
                        lead_id=lead_id,
                        style=style,
                        status=_A,
                        offer_id=open_offers.get(lead_id),
                        added_by=actor_id,
                    )
                )
            assigned[style.value] = assigned.get(style.value, 0) + 1
            added += 1
        await db.flush()
    if body.selection.filters is not None:
        c.target_filters = body.selection.filters.model_dump(mode="json", exclude_defaults=True)
    await _refresh(db, c)
    await audit(
        db,
        "campaign.leads_added",
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        entity_type="campaign",
        entity_id=c.id,
        diff={"matched": len(ids), "added": added, "skipped": skipped, "styles": assigned},
    )
    return CampaignLeadsResult(matched=len(ids), added=added, skipped=skipped, styles=assigned)


async def _cancel_campaign_message(
    db: AsyncSession, c: Campaign, cl: CampaignLead, actor_id: int | None, reason: str
) -> bool:
    """Cancel the queued / manual message of the lead's offer in this campaign (the offer stays approved)."""
    if cl.offer_id is None:
        return False
    offer, lead = await outreach.lock_offer_and_lead(db, c.workspace_id, cl.offer_id)
    live = await outreach.active_message(db, offer.id, lock=True)
    if live is None or live.campaign_id != c.id:
        return False
    await outreach.cancel_live_message(db, offer, actor_id, reason)
    outreach.back_to_approved(db, offer, lead, actor_id, reason)
    return True


async def _sending(db: AsyncSession, campaign_id: int, offer_ids: Sequence[int] | None = None) -> bool:
    conds = [Message.campaign_id == campaign_id, Message.status == MessageStatus.SENDING]
    if offer_ids is not None:
        conds.append(Message.offer_id.in_(list(offer_ids)))
    return (await db.scalar(select(Message.id).where(*conds).limit(1))) is not None


async def remove_lead(
    db: AsyncSession, workspace_id: int, actor_id: int, campaign_id: int, lead_id: int
) -> CampaignOut:
    c = await _campaign(db, workspace_id, campaign_id, lock=True)
    _require(c, OPEN_CAMPAIGN, "Removing leads")
    cl = await db.scalar(
        select(CampaignLead)
        .where(CampaignLead.campaign_id == c.id, CampaignLead.lead_id == lead_id)
        .with_for_update()
    )
    if cl is None:
        raise NotFoundError("The lead is not in this campaign")
    if cl.status != _A:
        raise ConflictError(
            f"The lead is already out of work here ({cl.status.value})",
            code="campaign_lead_status",
            details={"status": cl.status.value},
        )
    if cl.offer_id is not None and await _sending(db, c.id, [cl.offer_id]):
        raise ConflictError(
            "The message is being sent right now; wait for the result", code="message_sending"
        )
    cancelled = await _cancel_campaign_message(db, c, cl, actor_id, "лид убран из кампании")
    _finish(cl, CampaignLeadStatus.REMOVED, "убран вручную")
    await _refresh(db, c)
    await audit(
        db,
        "campaign.lead_removed",
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        entity_type="campaign",
        entity_id=c.id,
        diff={"lead_id": lead_id, "message_cancelled": cancelled},
    )
    return await campaign_out(db, c)


# ---------------------------------------------------------------------------
# Offer generation (estimate → confirm → start)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class GenerationPlan:
    estimate: CampaignGenerationEstimate
    targets: list[tuple[int, OfferStyle]]  # (lead_id, style)
    model: AIModel | None
    per_offer: Decimal | None
    template_id: int | None
    token_payload: dict[str, Any]


async def plan_generation(
    db: AsyncSession, settings: Settings, workspace_id: int, c: Campaign
) -> GenerationPlan:
    """Active leads without an offer (or whose drafts failed) and with a contact for the campaign's
    channels; one draft each in the lead's style."""
    template_id = await _current_template_id(db, workspace_id, c.template_id)
    channels = _channels(c)
    skipped = {"no_contact": 0, "lead_status": 0, "generation_running": 0, "offer_generating": 0}
    eligible: list[tuple[int, OfferStyle]] = []
    for r in await _rows(db, c.id, [CampaignLead.status == _A]):
        offer = r.offer
        if offer is not None and offer.status != OfferStatus.FAILED:
            if offer.status == OfferStatus.DRAFT:
                skipped["offer_generating"] += 1
            continue  # it has its offer
        if r.lead.status == LeadStatus.GENERATION_PENDING:
            skipped["generation_running"] += 1
            continue
        if r.lead.status not in offers.STARTABLE:
            skipped["lead_status"] += 1
            continue
        contacts = (await channel_contacts(db, workspace_id, r.channel.id)).items
        if offers.campaign_contact(contacts, channels) is None:
            skipped["no_contact"] += 1
            continue
        eligible.append((r.lead.id, r.cl.style))
    targets, remaining = eligible[:MAX_GENERATION_BATCH], max(0, len(eligible) - MAX_GENERATION_BATCH)
    models = await resolve_models(db, settings, offers.TASK)
    model = models[0] if models else None
    spec = TASKS[offers.TASK]
    per = token_cost(model, spec.est_input_tokens, spec.est_output_tokens) if model is not None else None
    total = (per * len(targets)).quantize(Decimal("0.000001")) if per is not None else None
    checks = (
        await budget_checks(db, workspace_id, c.project_id or 0, offers.TASK, total, len(targets))
        if model is not None and targets
        else []
    )
    styles: dict[str, int] = {}
    for _, s in targets:
        styles[s.value] = styles.get(s.value, 0) + 1
    token_payload: dict[str, Any] = {
        "campaign_id": c.id,
        "task": offers.TASK.value,
        "leads": _digest(",".join(f"{lid}:{s.value}" for lid, s in targets)),
        "count": len(targets),
        "model_key": model.key if model else None,
        "api_model_id": model.api_model_id if model else None,
        "per_offer": str(per) if per is not None else None,
        "template_id": template_id,
        "instructions": _digest(c.instructions),
        "language": c.language,
        "prompt": f"{offers.OFFER_PROMPT[0]}/v{offers.OFFER_PROMPT[1]}",
    }
    estimate = CampaignGenerationEstimate(
        campaign_id=c.id,
        lead_ids=[lid for lid, _ in targets],
        styles=styles,
        skipped=skipped,
        remaining=remaining,
        provider=model.provider if model else settings.effective_ai_provider,
        model_key=model.key if model else None,
        api_model_id=model.api_model_id if model else None,
        cost_per_offer_usd=per,
        estimated_cost_usd=total,
        pricing_verified=bool(model and pricing_known(model) and model.pricing_verified_at is not None),
        budgets=checks,
        within_budgets=not any(ch.would_exceed for ch in checks),
        confirm_token=budget.confirmation_token(token_payload),
    )
    return GenerationPlan(
        estimate=estimate,
        targets=targets,
        model=model,
        per_offer=per,
        template_id=template_id,
        token_payload=token_payload,
    )


async def estimate_generation(
    db: AsyncSession, settings: Settings, workspace_id: int, campaign_id: int
) -> CampaignGenerationEstimate:
    c = await _campaign(db, workspace_id, campaign_id)
    _require(c, GENERATING_CAMPAIGN, "Writing offers")
    return (await plan_generation(db, settings, workspace_id, c)).estimate


async def start_generation(
    db: AsyncSession,
    settings: Settings,
    workspace_id: int,
    actor_id: int,
    campaign_id: int,
    body: CampaignGenerationStart,
) -> CampaignGenerationResult:
    c = await _campaign(db, workspace_id, campaign_id, lock=True)
    _require(c, GENERATING_CAMPAIGN, "Writing offers")
    plan = await plan_generation(db, settings, workspace_id, c)
    if plan.model is None:
        raise AINotConfiguredError(
            "No text model is configured: set YTL_VIBECODE_API_KEY (or YTL_AI_PROVIDER=mock for demo drafts)."
        )
    if not plan.targets:
        raise ConflictError("No lead of the campaign needs an offer now", code="nothing_to_generate")
    budget.require_confirmation(body.confirm_token, plan.token_payload)
    prompt = await load_prompt(
        db, offers.OFFER_PROMPT[0], offers.OFFER_PROMPT[1], OfferDraft.model_json_schema()
    )
    channels = _channels(c)
    job_ids: list[int] = []
    for lead_id, style in plan.targets:
        lead, channel = await offers.lead_and_channel(db, workspace_id, lead_id, lock=True)
        if lead.status not in offers.STARTABLE:  # changed since the estimate
            continue
        existing = await offers.latest_offer(db, lead.id, lock=True)
        if (
            existing is not None
            and existing.status != OfferStatus.FAILED
            and existing.status != OfferStatus.REJECTED
        ):
            continue
        gathered = await offers.gather_inputs(
            db,
            workspace_id,
            lead,
            channel,
            OfferIn(
                variant_count=1,
                instructions=c.instructions,
                template_id=plan.template_id,
                language=c.language,
            ),
        )
        offer, _ = await offers.open_round(
            db,
            workspace_id,
            actor_id,
            lead,
            channel,
            existing,
            gathered=gathered,
            instructions=c.instructions,
            model=plan.model,
            prompt_template_id=prompt.template_id,
            styles=[style],
            estimated_cost_usd=plan.per_offer,
            queue=JobQueue.BULK,
            confirm_token=body.confirm_token,
            channels=channels,
        )
        cl = await db.scalar(
            select(CampaignLead).where(CampaignLead.campaign_id == c.id, CampaignLead.lead_id == lead.id)
        )
        assert cl is not None
        cl.offer_id, cl.reason = offer.id, None
        if offer.job_run_id is not None:
            job_ids.append(offer.job_run_id)
    if c.status == CampaignStatus.READY and job_ids:
        move_campaign(c, CampaignStatus.PENDING_APPROVAL)
    await audit(
        db,
        "campaign.generation_started",
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        entity_type="campaign",
        entity_id=c.id,
        diff={
            "offers": len(job_ids),
            "styles": plan.estimate.styles,
            "model_key": plan.model.key,
            "estimated_cost_usd": str(plan.estimate.estimated_cost_usd),
            "confirm_token": body.confirm_token,
        },
    )
    return CampaignGenerationResult(campaign=await campaign_out(db, c), started=len(job_ids), job_ids=job_ids)


# ---------------------------------------------------------------------------
# Run: sync outcomes, queue approved offers, complete
# ---------------------------------------------------------------------------
async def pick_sender(db: AsyncSession, c: Campaign, now: datetime) -> EmailAccount | None:
    """The campaign's active sender with the most room today (sent + still queued vs its daily limit)."""
    senders = await _senders(db, c)
    if not senders:
        return None
    ids = [a.id for a in senders]
    used = await account_sent_today(db, ids, now)
    queued = {
        int(a): int(n)
        for a, n in (
            await db.execute(
                select(Message.email_account_id, func.count())
                .where(Message.email_account_id.in_(ids), Message.status == MessageStatus.QUEUED)
                .group_by(Message.email_account_id)
            )
        ).tuples()
        if a is not None
    }
    return min(senders, key=lambda a: ((used.get(a.id, 0) + queued.get(a.id, 0)) / a.daily_limit, a.id))


async def _sync(db: AsyncSession, c: Campaign) -> dict[str, int]:
    """Outcomes of the active leads from their offers / messages; adopt an offer written meanwhile."""
    out = {"sent": 0, "rejected": 0, "failed": 0, "skipped": 0, "adopted": 0}
    for r in await _rows(db, c.id, [CampaignLead.status == _A]):
        cl, offer = r.cl, r.offer
        if offer is None:
            opened = await db.scalar(
                select(Offer).where(Offer.lead_id == r.lead.id, Offer.status.in_(OPEN_STATUSES)).limit(1)
            )
            if opened is not None:
                cl.offer_id, offer = opened.id, opened
                out["adopted"] += 1
        if offer is not None and offer.status == OfferStatus.SENT:
            _finish(cl, CampaignLeadStatus.SENT, None)
            out["sent"] += 1
        elif r.lead.status in offers.CLOSED_LEAD:
            _finish(cl, CampaignLeadStatus.SKIPPED, f"лид закрыт ({r.lead.status.value})")
            out["skipped"] += 1
        elif offer is not None and offer.status == OfferStatus.REJECTED:
            _finish(cl, CampaignLeadStatus.REJECTED, "оффер отклонён при подтверждении")
            out["rejected"] += 1
        elif (
            offer is not None
            and offer.status == OfferStatus.APPROVED
            and r.message is not None
            and r.message.status == MessageStatus.FAILED
        ):
            _finish(cl, CampaignLeadStatus.FAILED, r.message.error_code or "send_failed")
            out["failed"] += 1
    await db.flush()
    return out


async def _queue_approved(db: AsyncSession, c: Campaign, now: datetime) -> int:
    """Queue the message of every approved offer of the campaign once per approval."""
    candidates = list(
        await db.scalars(
            select(CampaignLead)
            .join(Offer, Offer.id == CampaignLead.offer_id)
            .where(
                CampaignLead.campaign_id == c.id,
                CampaignLead.status == _A,
                Offer.status == OfferStatus.APPROVED,
            )
            .order_by(CampaignLead.created_at, CampaignLead.lead_id)
            .limit(TICK_QUEUE_LIMIT)
        )
    )
    when = rules.next_window_start(c, now)
    queued = 0
    for cl in candidates:
        assert cl.offer_id is not None
        offer, lead = await outreach.lock_offer_and_lead(db, c.workspace_id, cl.offer_id)
        if offer.status != OfferStatus.APPROVED or await outreach.active_message(db, offer.id) is not None:
            continue
        contacts = (await channel_contacts(db, c.workspace_id, lead.channel_id)).items
        st = await offers.offer_state(db, offer, lead.status, contacts)
        try:
            approval = await approvals.require_approval(
                db, c.workspace_id, ApprovalTarget.OFFER, offer.id, st.content_hash
            )
        except ConflictError as exc:
            cl.reason = exc.code
            continue
        tried = await db.scalar(
            select(Message.id).where(
                Message.offer_id == offer.id, Message.campaign_id == c.id, Message.approval_id == approval.id
            )
        )
        if tried is not None:  # cancelled / failed once for this approval: the human decides now
            continue
        account_id: int | None = None
        if offer.channel == OutreachChannel.EMAIL:
            sender = await pick_sender(db, c, now)
            if sender is None:
                cl.reason = "email_account_required"
                continue
            account_id = sender.id
        try:
            async with db.begin_nested():
                await outreach.queue_message(
                    db, c.workspace_id, None, offer, lead, when, email_account_id=account_id
                )
        except ConflictError as exc:
            cl.reason = exc.code
            continue
        cl.reason = None
        queued += 1
    return queued


async def _close_leads(db: AsyncSession, c: Campaign, actor_id: int | None, reason: str) -> int:
    """Cancel queued messages of the campaign and release its active leads (complete / archive)."""
    if await _sending(db, c.id):
        raise ConflictError(
            "A message of the campaign is being sent right now; try again in a minute", code="message_sending"
        )
    cancelled = 0
    for cl in list(
        await db.scalars(
            select(CampaignLead)
            .where(CampaignLead.campaign_id == c.id, CampaignLead.status == _A)
            .with_for_update()
        )
    ):
        if await _cancel_campaign_message(db, c, cl, actor_id, reason):
            cancelled += 1
        _finish(cl, CampaignLeadStatus.SKIPPED, reason)
    return cancelled


async def tick(db: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    """Every minute (before the dispatcher). Caller commits."""
    now = now or datetime.now(UTC)
    totals = {
        "campaigns": 0,
        "queued": 0,
        "sent": 0,
        "rejected": 0,
        "failed": 0,
        "skipped": 0,
        "completed": 0,
    }
    campaigns = list(
        await db.scalars(
            select(Campaign)
            .where(Campaign.status.in_(list(OPEN_CAMPAIGN)))
            .order_by(Campaign.id)
            .with_for_update(skip_locked=True)
        )
    )
    for c in campaigns:
        totals["campaigns"] += 1
        synced = await _sync(db, c)
        for k in ("sent", "rejected", "failed", "skipped"):
            totals[k] += synced[k]
        await _refresh(db, c)  # draft ⇄ ready (an account added / a lead closed meanwhile)
        if c.status == CampaignStatus.RUNNING:
            totals["queued"] += await _queue_approved(db, c, now)
            if await _active_count(db, c.id) == 0:
                move_campaign(c, CampaignStatus.COMPLETED)
                c.completed_at = now
                totals["completed"] += 1
                await audit(
                    db,
                    "campaign.completed",
                    workspace_id=c.workspace_id,
                    actor_user_id=None,
                    entity_type="campaign",
                    entity_id=c.id,
                    diff={"auto": True},
                )
    return totals


# ---------------------------------------------------------------------------
# Status actions
# ---------------------------------------------------------------------------
async def _act(db: AsyncSession, c: Campaign, actor_id: int, action: str, **diff: Any) -> CampaignOut:
    await audit(
        db,
        f"campaign.{action}",
        workspace_id=c.workspace_id,
        actor_user_id=actor_id,
        entity_type="campaign",
        entity_id=c.id,
        diff={"status": c.status.value, **diff},
    )
    return await campaign_out(db, c)


async def _require_startable(db: AsyncSession, c: Campaign) -> None:
    issues = await issues_of(db, c)
    if issues:
        raise ConflictError(
            "The campaign cannot be started yet", code="campaign_not_ready", details={"issues": issues}
        )


async def start_campaign(db: AsyncSession, workspace_id: int, actor_id: int, campaign_id: int) -> CampaignOut:
    """Launch = consent to send every offer of the campaign a human approves (window and limits apply)."""
    c = await _campaign(db, workspace_id, campaign_id, lock=True)
    await _refresh(db, c)  # e.g. an email account was added since the last change
    if c.status == CampaignStatus.DRAFT:
        await _require_startable(db, c)  # raises with the issues
    _require(c, {CampaignStatus.READY, CampaignStatus.PENDING_APPROVAL}, "Starting")
    await _require_startable(db, c)
    move_campaign(c, CampaignStatus.RUNNING)
    now = datetime.now(UTC)
    c.started_at = c.started_at or now
    c.consecutive_failures, c.paused_reason = 0, None
    await _sync(db, c)
    queued = await _queue_approved(db, c, now)
    return await _act(db, c, actor_id, "started", queued=queued)


async def pause_campaign(db: AsyncSession, workspace_id: int, actor_id: int, campaign_id: int) -> CampaignOut:
    c = await _campaign(db, workspace_id, campaign_id, lock=True)
    _require(c, {CampaignStatus.RUNNING}, "Pausing")
    move_campaign(c, CampaignStatus.PAUSED)
    c.paused_reason = "на паузе вручную"
    return await _act(db, c, actor_id, "paused")


async def resume_campaign(
    db: AsyncSession, workspace_id: int, actor_id: int, campaign_id: int
) -> CampaignOut:
    c = await _campaign(db, workspace_id, campaign_id, lock=True)
    _require(c, {CampaignStatus.PAUSED}, "Resuming")
    if OutreachChannel.EMAIL.value in c.channels and not await _senders(db, c):
        raise ConflictError(
            "The campaign cannot be resumed yet",
            code="campaign_not_ready",
            details={"issues": ["email_account_required"]},
        )
    move_campaign(c, CampaignStatus.RUNNING)
    c.consecutive_failures, c.paused_reason = 0, None
    now = datetime.now(UTC)
    await _sync(db, c)
    queued = await _queue_approved(db, c, now)
    return await _act(db, c, actor_id, "resumed", queued=queued)


async def complete_campaign(
    db: AsyncSession, workspace_id: int, actor_id: int, campaign_id: int
) -> CampaignOut:
    c = await _campaign(db, workspace_id, campaign_id, lock=True)
    _require(
        c, {CampaignStatus.PENDING_APPROVAL, CampaignStatus.RUNNING, CampaignStatus.PAUSED}, "Completing"
    )
    await _sync(db, c)
    cancelled = await _close_leads(db, c, actor_id, "кампания завершена")
    move_campaign(c, CampaignStatus.COMPLETED)
    c.completed_at = datetime.now(UTC)
    return await _act(db, c, actor_id, "completed", messages_cancelled=cancelled)


async def archive_campaign(
    db: AsyncSession, workspace_id: int, actor_id: int, campaign_id: int
) -> CampaignOut:
    c = await _campaign(db, workspace_id, campaign_id, lock=True)
    _require(
        c,
        {
            CampaignStatus.DRAFT,
            CampaignStatus.READY,
            CampaignStatus.PENDING_APPROVAL,
            CampaignStatus.COMPLETED,
        },
        "Archiving",
    )
    cancelled = await _close_leads(db, c, actor_id, "кампания в архиве")
    move_campaign(c, CampaignStatus.ARCHIVED)
    c.archived_at = datetime.now(UTC)
    return await _act(db, c, actor_id, "archived", messages_cancelled=cancelled)


# ---------------------------------------------------------------------------
# A/B results (ТЗ §28)
# ---------------------------------------------------------------------------
REPLY_EVENTS = frozenset(
    {MessageEventType.REPLIED, MessageEventType.POSITIVE_REPLY, MessageEventType.NEGATIVE_REPLY}
)
ANSWERED_EVENTS = REPLY_EVENTS | {MessageEventType.OPT_OUT, MessageEventType.BOUNCED}


async def campaign_stats(db: AsyncSession, workspace_id: int, campaign_id: int) -> CampaignStats:
    c = await _campaign(db, workspace_id, campaign_id)
    styles = _styles(c)
    leads: dict[OfferStyle, int] = {}
    for s, n in (
        await db.execute(
            select(CampaignLead.style, func.count())
            .where(CampaignLead.campaign_id == c.id, CampaignLead.status != CampaignLeadStatus.REMOVED)
            .group_by(CampaignLead.style)
        )
    ).tuples():
        leads[s] = int(n)
    approved: dict[OfferStyle, int] = {}
    for s, n in (
        await db.execute(
            select(CampaignLead.style, func.count())
            .join(Offer, Offer.id == CampaignLead.offer_id)
            .where(
                CampaignLead.campaign_id == c.id,
                CampaignLead.status != CampaignLeadStatus.REMOVED,
                Offer.status.in_([OfferStatus.APPROVED, OfferStatus.SCHEDULED, OfferStatus.SENT]),
            )
            .group_by(CampaignLead.style)
        )
    ).tuples():
        approved[s] = int(n)
    # what was actually sent: the style of the sent variant (the lead's style if the variant is gone)
    sent_rows = (
        (
            await db.execute(
                select(Message.id, OfferVariant.style, CampaignLead.style)
                .join(
                    CampaignLead,
                    and_(
                        CampaignLead.campaign_id == Message.campaign_id,
                        CampaignLead.lead_id == Message.lead_id,
                    ),
                )
                .outerjoin(OfferVariant, OfferVariant.id == Message.offer_variant_id)
                .where(Message.campaign_id == c.id, Message.status == MessageStatus.SENT)
            )
        )
        .tuples()
        .all()
    )
    style_of = {mid: (vs or ls) for mid, vs, ls in sent_rows}
    events: dict[int, set[MessageEventType]] = {mid: set() for mid in style_of}
    if style_of:
        for mid, t in (
            await db.execute(
                select(MessageEvent.message_id, MessageEvent.type).where(
                    MessageEvent.message_id.in_(list(style_of))
                )
            )
        ).tuples():
            events[mid].add(t)
    present = [s for s in (*styles, *leads, *style_of.values()) if s is not None]
    out: list[VariantStats] = []
    for style in dict.fromkeys(present):
        mids = [m for m, s in style_of.items() if s == style]

        def has(types: frozenset[MessageEventType] | set[MessageEventType], ms: list[int] = mids) -> int:
            return sum(1 for m in ms if events[m] & types)

        sent = len(mids)
        replied = has(REPLY_EVENTS)
        out.append(
            VariantStats(
                style=style,
                label=offers.label_of(style),
                leads=leads.get(style, 0),
                approved=approved.get(style, 0),
                sent=sent,
                delivered=has({MessageEventType.DELIVERED}),
                opened=has({MessageEventType.OPENED}),
                replied=replied,
                positive_reply=has({MessageEventType.POSITIVE_REPLY}),
                negative_reply=has({MessageEventType.NEGATIVE_REPLY}),
                no_reply=sum(1 for m in mids if not events[m] & ANSWERED_EVENTS),
                bounced=has({MessageEventType.BOUNCED}),
                opt_out=has({MessageEventType.OPT_OUT}),
                reply_rate=(Decimal(replied) / Decimal(sent)).quantize(Decimal("0.0001")) if sent else None,
                small_sample=sent < MIN_SAMPLE,
            )
        )
    out.sort(key=lambda v: v.label)
    return CampaignStats(campaign_id=c.id, variants=out, min_sample=MIN_SAMPLE, note=STATS_NOTE)
