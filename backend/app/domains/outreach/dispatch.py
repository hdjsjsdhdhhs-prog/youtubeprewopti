"""Dispatching and sending email (Phase 8.1, ТЗ §26–27; ADR-0010).

* **Dispatcher** (``dispatch_due``, every minute): for every *active* account whose cooldown has passed and
  whose UTC-day limit is not reached, the oldest due queued message gets an ``outreach_send`` job; the
  account's next slot is reserved right away (random pause between ``cooldown_min`` and ``cooldown_max``), so
  sends of one account are never bunched together.
* **Send** (``send_message``): locks the message and re-checks everything right before the server: the
  offer still has exactly the approved content (``require_approval`` with the message's hash), the contact is
  still usable (opt-out / suppression since queueing), the lead is not closed, the account is active and
  within its limit. Then ``sending`` is committed *before* talking to the server: a worker that dies there
  never re-sends automatically (the message fails as ``send_interrupted`` — check the «Sent» folder).
* Errors: a refused recipient (hard bounce) → failed + suppression list; transient (4xx, timeouts) →
  retried with backoff up to ``outreach_max_attempts``; login refused → the account goes to ``error``;
  ``outreach_autopause_failures`` consecutive failures → the account is paused (ТЗ §26 «pause on abnormal
  failures»). A final failure leaves the offer approved (send again later) and the lead ``failed``.
"""

from __future__ import annotations

import secrets as pysecrets
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, IntegrationError, IntegrationErrorCode
from app.core.secrets import SecretStore
from app.core.state_machine import LEAD_MACHINE, LeadStatus
from app.domains.approvals import service as approvals
from app.domains.approvals.models import ApprovalTarget
from app.domains.contacts.models import SuppressionReason
from app.domains.contacts.service import channel_contacts, suppress_after_message
from app.domains.identity.service import audit
from app.domains.jobs.service import JobQueue, JobType, enqueue_job
from app.domains.leads.models import Lead, LeadStatusHistory
from app.domains.media.models import ImageAsset
from app.domains.offers.models import Offer
from app.domains.outreach import service
from app.domains.outreach.accounts import sent_today, smtp_config
from app.domains.outreach.models import (
    AccountStatus,
    EmailAccount,
    Message,
    MessageEventType,
    MessageStatus,
)
from app.providers.outreach import (
    Attachment,
    EmailTransport,
    OutgoingEmail,
    RecipientRejectedError,
)
from app.providers.storage import StorageBackend

Sessions = Callable[[], AbstractAsyncContextManager[AsyncSession]]


def _rng_seconds(lo: int, hi: int) -> int:
    return lo if hi <= lo else lo + pysecrets.randbelow(hi - lo + 1)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------
async def dispatch_due(db: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    """One pass: at most one message per account. Caller commits (the jobs and reservations together)."""
    now = now or datetime.now(UTC)
    accounts = list(
        await db.scalars(
            select(EmailAccount)
            .where(
                EmailAccount.status == AccountStatus.ACTIVE,
                or_(EmailAccount.next_send_at.is_(None), EmailAccount.next_send_at <= now),
            )
            .order_by(EmailAccount.id)
            .with_for_update(skip_locked=True)
        )
    )
    used = await sent_today(db, [a.id for a in accounts], now)
    counts = {"accounts": len(accounts), "enqueued": 0, "limit_reached": 0}
    for account in accounts:
        if used.get(account.id, 0) >= account.daily_limit:
            counts["limit_reached"] += 1
            continue
        message = await db.scalar(
            select(Message)
            .where(
                Message.email_account_id == account.id,
                Message.status == MessageStatus.QUEUED,
                Message.scheduled_at <= now,
                or_(Message.next_attempt_at.is_(None), Message.next_attempt_at <= now),
            )
            .order_by(Message.scheduled_at, Message.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if message is None:
            continue
        enq = await enqueue_job(
            db,
            type_=JobType.OUTREACH_SEND,
            queue=JobQueue.OUTREACH,
            params={"message_id": message.id},
            workspace_id=message.workspace_id,
            progress_total=1,
        )
        if enq.created:
            message.job_run_id = enq.job.id
            account.next_send_at = now + timedelta(
                seconds=_rng_seconds(account.cooldown_min_seconds, account.cooldown_max_seconds)
            )
            counts["enqueued"] += 1
    return counts


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Prepared:
    message_id: int
    workspace_id: int
    account_id: int
    email: OutgoingEmail
    config: Any  # SmtpConfig (password inside: never logged)


async def _lock_all(db: AsyncSession, message_id: int) -> tuple[Message, Offer, Lead] | None:
    """Offer + lead first, then the message: the same order as the API (edit / reject / send), no deadlock."""
    offer_id = await db.scalar(select(Message.offer_id).where(Message.id == message_id))
    if offer_id is None:
        return None
    row = (
        (
            await db.execute(
                select(Offer, Lead)
                .join(Lead, Lead.id == Offer.lead_id)
                .where(Offer.id == offer_id)
                .with_for_update(of=(Offer, Lead))
                .execution_options(populate_existing=True)
            )
        )
        .tuples()
        .first()
    )
    message = await db.scalar(
        select(Message)
        .where(Message.id == message_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None or message is None:
        return None
    return message, row[0], row[1]


def _fail_lead(db: AsyncSession, lead: Lead, reason: str) -> None:
    if lead.status != LeadStatus.FAILED and LEAD_MACHINE.can(lead.status, LeadStatus.FAILED):
        db.add(
            LeadStatusHistory(
                lead_id=lead.id,
                from_status=lead.status,
                to_status=LeadStatus.FAILED,
                actor_user_id=None,
                reason=reason,
            )
        )
        lead.status = LeadStatus.FAILED
        lead.status_changed_at = datetime.now(UTC)


async def _cancel_guard(
    db: AsyncSession, message: Message, offer: Offer, lead: Lead, code: str, why: str
) -> None:
    service.set_status(message, MessageStatus.CANCELLED)
    message.error_code, message.error_message = code, why
    service.add_event(db, message, MessageEventType.CANCELLED, None, why, code=code)
    service.back_to_approved(db, offer, lead, None, f"отправка остановлена: {why}")


async def _prepare(
    db: AsyncSession, secrets: SecretStore, storage: StorageBackend, message_id: int, now: datetime
) -> Prepared | dict[str, Any]:
    from app.domains.offers.service import offer_state  # offers ↔ outreach

    locked = await _lock_all(db, message_id)
    if locked is None:
        return {"skipped": "message not found"}
    message, offer, lead = locked
    if message.status == MessageStatus.SENDING:  # a previous attempt died while talking to the server
        service.set_status(message, MessageStatus.FAILED)
        message.error_code = "send_interrupted"
        message.error_message = (
            "Отправка прервалась на стороне сервера приложения: неизвестно, ушло ли письмо. Проверьте папку "
            "«Отправленные» почтового ящика; повторно автоматически не отправляется."
        )
        service.add_event(
            db, message, MessageEventType.FAILED, None, message.error_message, code="send_interrupted"
        )
        service.back_to_approved(db, offer, lead, None, "отправка прервалась")
        _fail_lead(db, lead, "отправка прервалась")
        return {"failed": "send_interrupted"}
    if message.status != MessageStatus.QUEUED:
        return {"skipped": f"message is {message.status.value}"}

    contacts = (await channel_contacts(db, message.workspace_id, lead.channel_id)).items
    st = await offer_state(db, offer, lead.status, contacts)
    if st.content_hash != message.content_hash:
        await _cancel_guard(
            db, message, offer, lead, "approval_outdated", "оффер изменён после постановки в очередь"
        )
        return {"cancelled": "approval_outdated"}
    try:
        await approvals.require_approval(
            db, message.workspace_id, ApprovalTarget.OFFER, offer.id, message.content_hash
        )
    except ConflictError as exc:
        await _cancel_guard(db, message, offer, lead, exc.code, "нет действующего подтверждения")
        return {"cancelled": exc.code}
    blockers = [b for b in st.blockers if b != "status"]
    if blockers:
        await _cancel_guard(db, message, offer, lead, "offer_not_sendable", "; ".join(blockers))
        return {"cancelled": "offer_not_sendable", "blockers": blockers}

    account = await db.scalar(
        select(EmailAccount).where(EmailAccount.id == message.email_account_id).with_for_update()
    )
    if account is None or account.status != AccountStatus.ACTIVE:
        message.error_code = "account_inactive"
        message.error_message = "Почтовый аккаунт на паузе или удалён: письмо ждёт в очереди."
        return {"waiting": "account_inactive"}
    if (await sent_today(db, [account.id], now)).get(account.id, 0) >= account.daily_limit:
        message.error_code, message.error_message = (
            "daily_limit",
            "Дневной лимит аккаунта исчерпан: письмо уйдёт завтра.",
        )
        return {"waiting": "daily_limit"}

    attachments: tuple[Attachment, ...] = ()
    if message.attachment_asset_id is not None:
        asset = await db.get(ImageAsset, message.attachment_asset_id)
        if asset is None or asset.storage_backend != storage.name or not storage.exists(asset.storage_key):
            await _cancel_guard(
                db, message, offer, lead, "attachment_missing", "файл вложения не найден в хранилище"
            )
            return {"cancelled": "attachment_missing"}
        attachments = (
            Attachment(
                filename=f"preview.{asset.format}", data=storage.get(asset.storage_key), mime=asset.mime
            ),
        )
    domain = account.from_email.rsplit("@", 1)[-1]
    reply = account.reply_to or account.from_email
    email = OutgoingEmail(
        from_email=account.from_email,
        from_name=account.from_name,
        to=message.to_address,
        subject=message.subject,
        text=message.body,
        message_id=f"<ytl-{message.id}-{pysecrets.token_hex(6)}@{domain}>",
        reply_to=account.reply_to,
        attachments=attachments,
        list_unsubscribe=f"mailto:{reply}?subject=unsubscribe",
    )
    config = await smtp_config(db, secrets, account)
    service.set_status(message, MessageStatus.SENDING)
    message.attempts += 1
    message.error_code = message.error_message = None
    return Prepared(
        message_id=message.id,
        workspace_id=message.workspace_id,
        account_id=account.id,
        email=email,
        config=config,
    )


async def _account_failure(
    db: AsyncSession, account: EmailAccount, exc: IntegrationError, settings: Settings
) -> None:
    account.consecutive_failures += 1
    account.last_error_code, account.last_error_message = exc.code.value, exc.human_message
    if exc.code == IntegrationErrorCode.AUTH_EXPIRED:
        account.status, account.paused_reason = AccountStatus.ERROR, exc.human_message
    elif account.consecutive_failures >= settings.outreach_autopause_failures:
        account.status = AccountStatus.PAUSED
        account.paused_reason = f"автопауза: {account.consecutive_failures} ошибок отправки подряд"


async def send_message(
    sessions: Sessions,
    transport: EmailTransport,
    storage: StorageBackend,
    secrets: SecretStore,
    settings: Settings,
    message_id: int,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with sessions() as db:
        prepared = await _prepare(db, secrets, storage, message_id, now)
        await db.commit()
    if not isinstance(prepared, Prepared):
        return prepared

    error: IntegrationError | None = None
    provider_id: str | None = None
    try:
        provider_id = (await transport.send(prepared.config, prepared.email)).provider_message_id
    except IntegrationError as exc:
        error = exc

    async with sessions() as db:
        locked = await _lock_all(db, message_id)
        assert locked is not None
        message, offer, lead = locked
        account = await db.scalar(
            select(EmailAccount).where(EmailAccount.id == prepared.account_id).with_for_update()
        )
        assert account is not None
        if error is None:
            service.set_status(message, MessageStatus.SENT)
            message.sent_at, message.provider_message_id = datetime.now(UTC), provider_id
            service.add_event(db, message, MessageEventType.SENT, None, transport=transport.name)
            service.mark_offer_sent(db, offer, lead, None)
            account.consecutive_failures, account.last_sent_at = 0, message.sent_at
            account.last_error_code = account.last_error_message = None
            result: dict[str, Any] = {"sent": message.id, "transport": transport.name}
        else:
            message.error_code, message.error_message = error.code.value, error.human_message
            bounce = isinstance(error, RecipientRejectedError)
            if not bounce:
                await _account_failure(db, account, error, settings)
            retry = error.retryable and message.attempts < settings.outreach_max_attempts
            if retry:
                service.set_status(message, MessageStatus.QUEUED)
                message.next_attempt_at = datetime.now(UTC) + timedelta(
                    seconds=settings.outreach_retry_base_seconds * 2 ** (message.attempts - 1)
                )
                result = {"retry": message.id, "attempt": message.attempts, "error": error.code.value}
            else:
                service.set_status(message, MessageStatus.FAILED)
                service.add_event(
                    db,
                    message,
                    MessageEventType.BOUNCED if bounce else MessageEventType.FAILED,
                    None,
                    error.human_message,
                    code=error.code.value,
                    smtp_code=error.http_status,
                )
                service.back_to_approved(db, offer, lead, None, "письмо не отправлено")
                _fail_lead(db, lead, f"письмо не отправлено: {error.code.value}")
                if bounce and message.contact_id is not None:
                    await suppress_after_message(
                        db,
                        message.workspace_id,
                        None,
                        message.contact_id,
                        SuppressionReason.BOUNCE,
                        message.id,
                        error.human_message,
                    )
                result = {"failed": message.id, "error": error.code.value, "bounce": bounce}
        await audit(
            db,
            "message.sent" if error is None else "message.send_failed",
            workspace_id=message.workspace_id,
            actor_user_id=None,
            entity_type="message",
            entity_id=message.id,
            diff={
                "attempt": message.attempts,
                "transport": transport.name,
                "error": error.code.value if error else None,
                "to": message.to_address,
            },
        )
        await db.commit()
    return result
