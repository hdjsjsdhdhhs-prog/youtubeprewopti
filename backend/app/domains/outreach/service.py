"""Messages built from approved offers (Phase 8.1, ТЗ §24–27, §59; ADR-0010 guardrails).

Guardrails live here, not in transports:

* a message is created only from an **approved** offer whose current content matches the approval
  (``require_approval``) — and, for «Send now», matches the content the human looked at (``content_hash``);
* the contact must be usable (not opted out / invalid / suppressed / gone), the channel must fit it, the text
  must have no guard issues, the lead must not be closed (the same blockers as «Approve»);
* ``idempotency_key`` = hash(variant, contact, channel) is unique among live messages: no second send;
* email goes through an active account of the workspace, with its opt-out footer; Telegram / VK / manual are
  sent by the operator (status ``manual``) — no automatic provider for them (Q-001, owner's decision);
* the actual send (``dispatch.py``) re-checks all of this right before talking to the server.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.core.state_machine import LEAD_MACHINE, LeadStatus
from app.domains.approvals import service as approvals
from app.domains.approvals.models import ApprovalTarget
from app.domains.contacts.models import SuppressionReason
from app.domains.contacts.service import channel_contacts, suppress_after_message
from app.domains.generation.models import GeneratedThumbnail
from app.domains.identity.service import audit
from app.domains.leads.models import Lead, LeadStatusHistory
from app.domains.offers.models import Offer, OfferStatus, OutreachChannel
from app.domains.outreach.models import (
    ACTIVE_STATUSES,
    MESSAGE_MACHINE,
    AccountStatus,
    EmailAccount,
    Message,
    MessageEvent,
    MessageEventType,
    MessageStatus,
)
from app.domains.outreach.schemas import MessageEventIn, MessageEventOut, MessageList, MessageOut
from app.domains.youtube.models import Channel
from app.domains.youtube.service import image_url

LIVE_WHERE = "status IN ('queued', 'sending', 'sent', 'manual')"


def idempotency_key(variant_id: int, contact_id: int, channel: OutreachChannel) -> str:
    return hashlib.sha256(f"{variant_id}:{contact_id}:{channel.value}".encode("ascii")).hexdigest()


def with_footer(body: str, opt_out_text: str) -> str:
    return f"{body.rstrip()}\n\n—\n{opt_out_text.strip()}"


def _move_lead(db: AsyncSession, lead: Lead, target: LeadStatus, actor_id: int | None, reason: str) -> None:
    from app.domains.offers.service import move_lead  # offers ↔ outreach

    move_lead(db, lead, target, actor_id, reason)


def _move_offer(
    db: AsyncSession, offer: Offer, target: OfferStatus, actor_id: int | None, reason: str
) -> None:
    from app.domains.offers.service import move_offer

    move_offer(db, offer, target, actor_id, reason)


def add_event(
    db: AsyncSession,
    message: Message,
    type_: MessageEventType,
    actor_id: int | None,
    note: str = "",
    **payload: Any,
) -> None:
    db.add(MessageEvent(message_id=message.id, type=type_, note=note, payload=payload, user_id=actor_id))


def set_status(message: Message, target: MessageStatus) -> None:
    MESSAGE_MACHINE.assert_can(message.status, target)
    message.status = target


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
async def messages_out(db: AsyncSession, rows: list[Message]) -> list[MessageOut]:
    if not rows:
        return []
    ids = [m.id for m in rows]
    events: dict[int, list[MessageEventOut]] = {}
    for e in await db.scalars(
        select(MessageEvent)
        .where(MessageEvent.message_id.in_(ids))
        .order_by(MessageEvent.occurred_at, MessageEvent.id)
    ):
        events.setdefault(e.message_id, []).append(MessageEventOut.model_validate(e))
    channels = {
        lead_id: (cid, title)
        for lead_id, cid, title in (
            await db.execute(
                select(Lead.id, Channel.id, Channel.title)
                .join(Channel, Channel.id == Lead.channel_id)
                .where(Lead.id.in_([m.lead_id for m in rows]))
            )
        ).tuples()
    }
    accounts = {
        a.id: a.name
        for a in await db.scalars(
            select(EmailAccount).where(
                EmailAccount.id.in_([m.email_account_id for m in rows if m.email_account_id])
            )
        )
    }
    out = []
    for m in rows:
        cid, title = channels.get(m.lead_id, (0, ""))
        out.append(
            MessageOut(
                id=m.id,
                lead_id=m.lead_id,
                offer_id=m.offer_id,
                channel_id=cid,
                channel_title=title,
                channel=m.channel,
                status=m.status,
                email_account_id=m.email_account_id,
                email_account_name=accounts.get(m.email_account_id) if m.email_account_id else None,
                contact_id=m.contact_id,
                contact_type=m.contact_type,
                to_address=m.to_address,
                subject=m.subject,
                body=m.body,
                attachment_url=image_url(m.attachment_asset_id) if m.attachment_asset_id else None,
                content_hash=m.content_hash,
                attempts=m.attempts,
                scheduled_at=m.scheduled_at,
                next_attempt_at=m.next_attempt_at,
                sent_at=m.sent_at,
                provider_message_id=m.provider_message_id,
                error_code=m.error_code,
                error_message=m.error_message,
                events=events.get(m.id, []),
                created_at=m.created_at,
            )
        )
    return out


async def message_out(db: AsyncSession, message: Message) -> MessageOut:
    await db.flush()
    await db.refresh(message)
    [out] = await messages_out(db, [message])
    return out


async def latest_message(db: AsyncSession, offer_id: int) -> Message | None:
    return await db.scalar(
        select(Message).where(Message.offer_id == offer_id).order_by(Message.id.desc()).limit(1)
    )


async def active_message(db: AsyncSession, offer_id: int, *, lock: bool = False) -> Message | None:
    stmt = select(Message).where(Message.offer_id == offer_id, Message.status.in_(ACTIVE_STATUSES))
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return await db.scalar(stmt.order_by(Message.id.desc()).limit(1))


async def _message(db: AsyncSession, workspace_id: int, message_id: int, *, lock: bool = False) -> Message:
    stmt = select(Message).where(Message.id == message_id, Message.workspace_id == workspace_id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = await db.scalar(stmt)
    if row is None:
        raise NotFoundError("Message not found")
    return row


async def get_message(db: AsyncSession, workspace_id: int, message_id: int) -> MessageOut:
    return await message_out(db, await _message(db, workspace_id, message_id))


async def list_messages(
    db: AsyncSession,
    workspace_id: int,
    *,
    status: MessageStatus | None,
    lead_id: int | None,
    limit: int,
    offset: int,
) -> MessageList:
    conds: list[ColumnElement[bool]] = [Message.workspace_id == workspace_id]
    if status is not None:
        conds.append(Message.status == status)
    if lead_id is not None:
        conds.append(Message.lead_id == lead_id)
    total = await db.scalar(select(func.count()).select_from(Message).where(*conds)) or 0
    rows = list(
        await db.scalars(
            select(Message)
            .where(*conds)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    counts = {s.value: 0 for s in MessageStatus}
    for s, n in (
        await db.execute(
            select(Message.status, func.count())
            .where(Message.workspace_id == workspace_id)
            .group_by(Message.status)
        )
    ).tuples():
        counts[s.value] = int(n)
    return MessageList(
        items=await messages_out(db, rows), total=total, limit=limit, offset=offset, counts=counts
    )


# ---------------------------------------------------------------------------
# Queueing (Send now / Schedule)
# ---------------------------------------------------------------------------
async def resolve_account(
    db: AsyncSession, workspace_id: int, offer: Offer, requested_id: int | None
) -> EmailAccount:
    account_id = requested_id if requested_id is not None else offer.email_account_id
    if account_id is not None:
        account = await db.scalar(
            select(EmailAccount).where(
                EmailAccount.id == account_id, EmailAccount.workspace_id == workspace_id
            )
        )
        if account is None:
            raise NotFoundError("Email account not found")
    else:
        account = await db.scalar(
            select(EmailAccount)
            .where(EmailAccount.workspace_id == workspace_id, EmailAccount.status == AccountStatus.ACTIVE)
            .order_by(EmailAccount.id)
            .limit(1)
        )
        if account is None:
            raise ConflictError(
                "No active email account: add an SMTP account to send email", code="email_account_required"
            )
    if account.status != AccountStatus.ACTIVE:
        raise ConflictError(
            f"The email account '{account.name}' is {account.status.value}",
            code="email_account_inactive",
            details={"status": account.status.value, "reason": account.paused_reason},
        )
    return account


async def queue_message(
    db: AsyncSession,
    workspace_id: int,
    actor_id: int | None,
    offer: Offer,
    lead: Lead,
    when: datetime,
    *,
    expected_hash: str | None = None,
    email_account_id: int | None = None,
) -> Message:
    """Create the message of an approved offer (caller holds the offer / lead row locks)."""
    from app.domains.offers.service import offer_state  # offers ↔ outreach

    if offer.status not in (OfferStatus.APPROVED, OfferStatus.SCHEDULED):
        raise ConflictError(
            f"Only an approved offer can be sent (status '{offer.status.value}')",
            code="offer_status",
            details={"status": offer.status.value},
        )
    if await active_message(db, offer.id) is not None:
        raise ConflictError("A message of this offer is already queued", code="message_exists")
    contacts = (await channel_contacts(db, workspace_id, lead.channel_id)).items
    st = await offer_state(db, offer, lead.status, contacts)
    if expected_hash is not None and expected_hash != st.content_hash:
        raise ConflictError(
            "The offer changed since you opened it; review it again",
            code="content_changed",
            details={"content_hash": st.content_hash},
        )
    approval = await approvals.require_approval(
        db, workspace_id, ApprovalTarget.OFFER, offer.id, st.content_hash
    )
    blockers = [b for b in st.blockers if b != "status"]
    if blockers:
        raise ConflictError(
            "The offer cannot be sent", code="offer_not_sendable", details={"blockers": blockers}
        )
    assert st.selected is not None and st.contact is not None and offer.channel is not None
    contact, variant, channel = st.contact, st.selected, offer.channel

    account: EmailAccount | None = None
    body = variant.body
    if channel == OutreachChannel.EMAIL:
        account = await resolve_account(db, workspace_id, offer, email_account_id)
        body = with_footer(variant.body, account.opt_out_text)
        offer.email_account_id = account.id
    asset_id = (
        await db.scalar(
            select(GeneratedThumbnail.image_asset_id).where(
                GeneratedThumbnail.id == offer.attachment_variant_id
            )
        )
        if offer.attachment_variant_id is not None
        else None
    )
    key = idempotency_key(variant.id, contact.id, channel)
    message_id = await db.scalar(
        insert(Message)
        .values(
            workspace_id=workspace_id,
            lead_id=lead.id,
            offer_id=offer.id,
            offer_variant_id=variant.id,
            contact_id=contact.id,
            approval_id=approval.id,
            channel=channel,
            email_account_id=account.id if account else None,
            contact_type=contact.type.value,
            to_address=contact.value_normalized if channel == OutreachChannel.EMAIL else contact.value,
            subject=variant.subject,
            body=body,
            attachment_asset_id=asset_id,
            content_hash=st.content_hash,
            idempotency_key=key,
            status=MessageStatus.QUEUED if channel == OutreachChannel.EMAIL else MessageStatus.MANUAL,
            scheduled_at=when,
            created_by=actor_id,
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"], index_where=text(LIVE_WHERE))
        .returning(Message.id)
    )
    if message_id is None:
        raise ConflictError(
            "This text was already sent (or is being sent) to this contact",
            code="already_sent",
            details={"idempotency_key": key},
        )
    if offer.status == OfferStatus.APPROVED:
        _move_offer(db, offer, OfferStatus.SCHEDULED, actor_id, "сообщение поставлено в очередь")
    offer.scheduled_at = when
    if LEAD_MACHINE.can(lead.status, LeadStatus.SCHEDULED) or lead.status == LeadStatus.SCHEDULED:
        _move_lead(db, lead, LeadStatus.SCHEDULED, actor_id, "сообщение поставлено в очередь")
    message = await db.get(Message, message_id)
    assert message is not None
    await audit(
        db,
        "message.queued",
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        entity_type="message",
        entity_id=message.id,
        diff={
            "offer_id": offer.id,
            "channel": channel.value,
            "to": message.to_address,
            "scheduled_at": when.isoformat(),
            "content_hash": st.content_hash,
            "approval_id": approval.id,
        },
    )
    return message


async def _offer_and_lead(db: AsyncSession, workspace_id: int, offer_id: int) -> tuple[Offer, Lead]:
    row = (
        (
            await db.execute(
                select(Offer, Lead)
                .join(Lead, Lead.id == Offer.lead_id)
                .where(Offer.id == offer_id, Offer.workspace_id == workspace_id)
                .with_for_update(of=(Offer, Lead))
                .execution_options(populate_existing=True)
            )
        )
        .tuples()
        .first()
    )
    if row is None:
        raise NotFoundError("Offer not found")
    return row


async def send_now(
    db: AsyncSession,
    workspace_id: int,
    actor_id: int,
    offer_id: int,
    content_hash: str,
    email_account_id: int | None,
) -> MessageOut:
    """«Send»: queue the approved offer for now. Email goes out with the next dispatch (≤ 1 min, respecting
    the account's cooldown and daily limit); other channels wait for the operator (``manual``)."""
    offer, lead = await _offer_and_lead(db, workspace_id, offer_id)
    message = await queue_message(
        db,
        workspace_id,
        actor_id,
        offer,
        lead,
        datetime.now(UTC),
        expected_hash=content_hash,
        email_account_id=email_account_id,
    )
    return await message_out(db, message)


def back_to_approved(db: AsyncSession, offer: Offer, lead: Lead, actor_id: int | None, reason: str) -> None:
    """The message did not go out: the offer stays approved (send again later), no send time."""
    if offer.status == OfferStatus.SCHEDULED:
        _move_offer(db, offer, OfferStatus.APPROVED, actor_id, reason)
    offer.scheduled_at = None
    if lead.status == LeadStatus.SCHEDULED:
        _move_lead(db, lead, LeadStatus.APPROVED, actor_id, reason)


async def cancel_live_message(
    db: AsyncSession, offer: Offer, actor_id: int | None, reason: str
) -> Message | None:
    """Cancel the offer's queued / manual message (an edit, a reject, unschedule). A message being sent right
    now cannot be cancelled: 409 ``message_sending``."""
    message = await active_message(db, offer.id, lock=True)
    if message is None:
        return None
    if message.status == MessageStatus.SENDING:
        raise ConflictError(
            "The message is being sent right now; wait for the result", code="message_sending"
        )
    set_status(message, MessageStatus.CANCELLED)
    add_event(db, message, MessageEventType.CANCELLED, actor_id, reason)
    return message


async def cancel_message(db: AsyncSession, workspace_id: int, actor_id: int, message_id: int) -> MessageOut:
    message = await _message(db, workspace_id, message_id, lock=True)
    if message.status not in (MessageStatus.QUEUED, MessageStatus.MANUAL):
        raise ConflictError(
            f"A message in status '{message.status.value}' cannot be cancelled",
            code="message_status",
            details={"status": message.status.value},
        )
    offer, lead = await _offer_and_lead(db, workspace_id, message.offer_id)
    set_status(message, MessageStatus.CANCELLED)
    add_event(db, message, MessageEventType.CANCELLED, actor_id, "отменено оператором")
    back_to_approved(db, offer, lead, actor_id, "отправка отменена")
    await audit(
        db,
        "message.cancelled",
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        entity_type="message",
        entity_id=message.id,
        diff={"offer_id": offer.id},
    )
    return await message_out(db, message)


def mark_offer_sent(db: AsyncSession, offer: Offer, lead: Lead, actor_id: int | None) -> None:
    if offer.status != OfferStatus.SENT:
        _move_offer(db, offer, OfferStatus.SENT, actor_id, "сообщение отправлено")
    if LEAD_MACHINE.can(lead.status, LeadStatus.SENT):
        _move_lead(db, lead, LeadStatus.SENT, actor_id, "сообщение отправлено")


async def mark_sent(
    db: AsyncSession, workspace_id: int, actor_id: int, message_id: int, note: str
) -> MessageOut:
    """The operator sent a manual message himself (Telegram, VK …)."""
    message = await _message(db, workspace_id, message_id, lock=True)
    if message.status != MessageStatus.MANUAL:
        raise ConflictError(
            "Only a manual message can be marked as sent",
            code="message_status",
            details={"status": message.status.value},
        )
    offer, lead = await _offer_and_lead(db, workspace_id, message.offer_id)
    set_status(message, MessageStatus.SENT)
    message.sent_at = datetime.now(UTC)
    add_event(db, message, MessageEventType.SENT, actor_id, note, manual=True)
    mark_offer_sent(db, offer, lead, actor_id)
    await audit(
        db,
        "message.marked_sent",
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        entity_type="message",
        entity_id=message.id,
        diff={"note": note},
    )
    return await message_out(db, message)


_LEAD_AFTER: dict[MessageEventType, LeadStatus] = {
    MessageEventType.REPLIED: LeadStatus.REPLIED,
    MessageEventType.POSITIVE_REPLY: LeadStatus.REPLIED,
    MessageEventType.NEGATIVE_REPLY: LeadStatus.DECLINED,
    MessageEventType.OPT_OUT: LeadStatus.OPTED_OUT,
}


async def record_event(
    db: AsyncSession, workspace_id: int, actor_id: int, message_id: int, body: MessageEventIn
) -> MessageOut:
    """What happened after the send (SMTP reports nothing by itself). Opt-out / bounce put the address on the
    suppression list; replies move the lead (replied / declined / opted out)."""
    message = await _message(db, workspace_id, message_id, lock=True)
    if message.status != MessageStatus.SENT:
        raise ConflictError(
            "Events can be recorded only for a sent message",
            code="message_status",
            details={"status": message.status.value},
        )
    type_ = MessageEventType(body.type)
    add_event(db, message, type_, actor_id, body.note)
    if type_ in (MessageEventType.OPT_OUT, MessageEventType.BOUNCED) and message.contact_id is not None:
        reason = SuppressionReason.OPT_OUT if type_ == MessageEventType.OPT_OUT else SuppressionReason.BOUNCE
        await suppress_after_message(
            db, workspace_id, actor_id, message.contact_id, reason, message.id, body.note
        )
    target = _LEAD_AFTER.get(type_)
    if target is not None:
        lead = await db.scalar(
            select(Lead)
            .where(Lead.id == message.lead_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if lead is not None and lead.status != target and LEAD_MACHINE.can(lead.status, target):
            db.add(
                LeadStatusHistory(
                    lead_id=lead.id,
                    from_status=lead.status,
                    to_status=target,
                    actor_user_id=actor_id,
                    reason=f"сообщение: {type_.value}",
                )
            )
            lead.status = target
            lead.status_changed_at = datetime.now(UTC)
    await audit(
        db,
        f"message.{type_.value}",
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        entity_type="message",
        entity_id=message.id,
        diff={"note": body.note},
    )
    return await message_out(db, message)
