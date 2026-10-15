"""Outreach (Phase 8.1, ТЗ §24–27, §59): SMTP accounts, «Send» of approved offers, messages and their events.
Every send is re-checked against the approval, the suppression list and the account limits right before the
server (``domains/outreach/dispatch.py``)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import (
    AppSettings,
    DbSession,
    IntegrationsAuth,
    Paging,
    ReadAuth,
    Secrets,
    SendAuth,
    WriteAuth,
)
from app.domains.outreach import accounts, service
from app.domains.outreach.models import MessageStatus
from app.domains.outreach.schemas import (
    AccountCheck,
    EmailAccountIn,
    EmailAccountOut,
    EmailAccountPatch,
    MarkSentIn,
    MessageEventIn,
    MessageList,
    MessageOut,
    SendIn,
)
from app.providers.outreach import EmailTransport, email_transport

router = APIRouter(tags=["outreach"])


def get_email_transport(settings: AppSettings) -> EmailTransport:
    return email_transport(settings)


Transport = Annotated[EmailTransport, Depends(get_email_transport)]


@router.get("/email-accounts", response_model=list[EmailAccountOut])
async def list_accounts(ctx: ReadAuth, db: DbSession) -> list[EmailAccountOut]:
    return await accounts.list_accounts(db, ctx.workspace.id)


@router.post("/email-accounts", response_model=EmailAccountOut, status_code=status.HTTP_201_CREATED)
async def create_account(
    body: EmailAccountIn, ctx: IntegrationsAuth, db: DbSession, secrets: Secrets
) -> EmailAccountOut:
    """Admin. The password is encrypted (SecretStore) and never returned."""
    return await accounts.create_account(db, secrets, ctx.workspace.id, ctx.user.id, body)


@router.patch("/email-accounts/{account_id}", response_model=EmailAccountOut)
async def update_account(
    account_id: int, body: EmailAccountPatch, ctx: IntegrationsAuth, db: DbSession, secrets: Secrets
) -> EmailAccountOut:
    return await accounts.update_account(db, secrets, ctx.workspace.id, ctx.user.id, account_id, body)


@router.post("/email-accounts/{account_id}/verify", response_model=AccountCheck)
async def verify_account(
    account_id: int, ctx: IntegrationsAuth, db: DbSession, secrets: Secrets, transport: Transport
) -> AccountCheck:
    """Connect and log in to the SMTP server; nothing is sent."""
    return await accounts.verify_account(db, secrets, transport, ctx.workspace.id, ctx.user.id, account_id)


@router.post("/email-accounts/{account_id}/pause", response_model=EmailAccountOut)
async def pause_account(account_id: int, ctx: IntegrationsAuth, db: DbSession) -> EmailAccountOut:
    return await accounts.set_status(db, ctx.workspace.id, ctx.user.id, account_id, pause=True)


@router.post("/email-accounts/{account_id}/resume", response_model=EmailAccountOut)
async def resume_account(account_id: int, ctx: IntegrationsAuth, db: DbSession) -> EmailAccountOut:
    """Back to active; the failure counter is reset."""
    return await accounts.set_status(db, ctx.workspace.id, ctx.user.id, account_id, pause=False)


@router.post("/offers/{offer_id}/send", response_model=MessageOut, status_code=status.HTTP_202_ACCEPTED)
async def send_offer(offer_id: int, body: SendIn, ctx: SendAuth, db: DbSession) -> MessageOut:
    """Queue the approved offer now (echo ``content_hash``). Email leaves with the next dispatch (≤ 1 min, the
    account's pause and daily limit apply); other channels become «manual» for the operator. 409:
    ``content_changed``, ``not_approved`` / ``approval_outdated``, ``offer_not_sendable`` (blockers),
    ``message_exists``, ``already_sent``, ``email_account_required`` / ``email_account_inactive``."""
    return await service.send_now(
        db, ctx.workspace.id, ctx.user.id, offer_id, body.content_hash, body.email_account_id
    )


@router.get("/messages", response_model=MessageList)
async def list_messages(
    ctx: ReadAuth,
    db: DbSession,
    page: Paging,
    status_: Annotated[MessageStatus | None, Query(alias="status")] = None,
    lead_id: int | None = None,
) -> MessageList:
    return await service.list_messages(
        db, ctx.workspace.id, status=status_, lead_id=lead_id, limit=min(page.limit, 200), offset=page.offset
    )


@router.get("/messages/{message_id}", response_model=MessageOut)
async def get_message(message_id: int, ctx: ReadAuth, db: DbSession) -> MessageOut:
    return await service.get_message(db, ctx.workspace.id, message_id)


@router.post("/messages/{message_id}/cancel", response_model=MessageOut)
async def cancel_message(message_id: int, ctx: SendAuth, db: DbSession) -> MessageOut:
    """Queued / manual only; the offer stays approved."""
    return await service.cancel_message(db, ctx.workspace.id, ctx.user.id, message_id)


@router.post("/messages/{message_id}/mark-sent", response_model=MessageOut)
async def mark_sent(message_id: int, body: MarkSentIn, ctx: SendAuth, db: DbSession) -> MessageOut:
    """A manual message (Telegram, VK …) was sent by the operator."""
    return await service.mark_sent(db, ctx.workspace.id, ctx.user.id, message_id, body.note)


@router.post("/messages/{message_id}/events", response_model=MessageOut)
async def record_event(message_id: int, body: MessageEventIn, ctx: WriteAuth, db: DbSession) -> MessageOut:
    """delivered / opened / replied / positive_reply / negative_reply / bounced / opt_out on a sent message.
    opt_out and bounced put the address on the suppression list."""
    return await service.record_event(db, ctx.workspace.id, ctx.user.id, message_id, body)
