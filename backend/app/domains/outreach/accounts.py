"""SMTP accounts of a workspace (Phase 8.1, ТЗ §27). The password is stored only in the encrypted secret
store (ADR-0009) and is never returned; sent-today / bounces / replies / opt-outs are computed from
messages."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, IntegrationError, NotFoundError
from app.core.secrets import SecretStore
from app.domains.identity.service import audit
from app.domains.outreach.models import (
    AccountStatus,
    EmailAccount,
    Message,
    MessageEvent,
    MessageEventType,
    MessageStatus,
)
from app.domains.outreach.schemas import AccountCheck, EmailAccountIn, EmailAccountOut, EmailAccountPatch
from app.providers.outreach import EmailTransport, SmtpConfig

SECRET_PURPOSE = "smtp_password"  # noqa: S105 — the purpose label of the secret, not a password
PASSWORD_HINT = "••••••••"  # noqa: S105 — a display mask


def utc_day_start(now: datetime | None = None) -> datetime:
    n = (now or datetime.now(UTC)).astimezone(UTC)
    return n.replace(hour=0, minute=0, second=0, microsecond=0)


async def sent_today(
    db: AsyncSession, account_ids: Sequence[int], now: datetime | None = None
) -> dict[int, int]:
    """Messages per account sent (or being sent) since the start of the UTC day — the daily quota."""
    if not account_ids:
        return {}
    rows = await db.execute(
        select(Message.email_account_id, func.count())
        .where(
            Message.email_account_id.in_(list(account_ids)),
            Message.status.in_([MessageStatus.SENT, MessageStatus.SENDING]),
            func.coalesce(Message.sent_at, Message.updated_at) >= utc_day_start(now),
        )
        .group_by(Message.email_account_id)
    )
    return {int(a): int(n) for a, n in rows.tuples() if a is not None}


async def _event_counts(db: AsyncSession, account_ids: Sequence[int]) -> dict[tuple[int, str], int]:
    if not account_ids:
        return {}
    rows = await db.execute(
        select(Message.email_account_id, MessageEvent.type, func.count())
        .join(Message, Message.id == MessageEvent.message_id)
        .where(Message.email_account_id.in_(list(account_ids)))
        .group_by(Message.email_account_id, MessageEvent.type)
    )
    return {(int(a), t.value): int(n) for a, t, n in rows.tuples() if a is not None}


async def accounts_out(db: AsyncSession, rows: Sequence[EmailAccount]) -> list[EmailAccountOut]:
    ids = [a.id for a in rows]
    today = await sent_today(db, ids)
    events = await _event_counts(db, ids)
    out: list[EmailAccountOut] = []
    for a in rows:
        sent = today.get(a.id, 0)
        out.append(
            EmailAccountOut(
                id=a.id,
                name=a.name,
                from_email=a.from_email,
                from_name=a.from_name,
                reply_to=a.reply_to,
                smtp_host=a.smtp_host,
                smtp_port=a.smtp_port,
                smtp_security=a.smtp_security,
                smtp_username=a.smtp_username,
                password_configured=a.secret_id is not None,
                password_hint=PASSWORD_HINT if a.secret_id is not None else None,
                status=a.status,
                paused_reason=a.paused_reason,
                daily_limit=a.daily_limit,
                sent_today=sent,
                remaining_today=max(0, a.daily_limit - sent),
                cooldown_min_seconds=a.cooldown_min_seconds,
                cooldown_max_seconds=a.cooldown_max_seconds,
                next_send_at=a.next_send_at,
                opt_out_text=a.opt_out_text,
                consecutive_failures=a.consecutive_failures,
                bounces=events.get((a.id, MessageEventType.BOUNCED.value), 0),
                replies=sum(
                    events.get((a.id, t.value), 0)
                    for t in (
                        MessageEventType.REPLIED,
                        MessageEventType.POSITIVE_REPLY,
                        MessageEventType.NEGATIVE_REPLY,
                    )
                ),
                opt_outs=events.get((a.id, MessageEventType.OPT_OUT.value), 0),
                last_sent_at=a.last_sent_at,
                last_error_code=a.last_error_code,
                last_error_message=a.last_error_message,
                last_checked_at=a.last_checked_at,
                created_at=a.created_at,
            )
        )
    return out


async def get_account(
    db: AsyncSession, workspace_id: int, account_id: int, *, lock: bool = False
) -> EmailAccount:
    stmt = select(EmailAccount).where(
        EmailAccount.id == account_id, EmailAccount.workspace_id == workspace_id
    )
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = await db.scalar(stmt)
    if row is None:
        raise NotFoundError("Email account not found")
    return row


async def list_accounts(db: AsyncSession, workspace_id: int) -> list[EmailAccountOut]:
    rows = list(
        await db.scalars(
            select(EmailAccount).where(EmailAccount.workspace_id == workspace_id).order_by(EmailAccount.id)
        )
    )
    return await accounts_out(db, rows)


async def _one(db: AsyncSession, account: EmailAccount) -> EmailAccountOut:
    await db.flush()
    await db.refresh(account)
    [out] = await accounts_out(db, [account])
    return out


async def create_account(
    db: AsyncSession, secrets: SecretStore, workspace_id: int, actor_id: int, body: EmailAccountIn
) -> EmailAccountOut:
    data = body.model_dump(exclude={"password"})
    row = EmailAccount(workspace_id=workspace_id, created_by=actor_id, **data)
    if body.password is not None:
        secret = await secrets.put(
            db,
            purpose=SECRET_PURPOSE,
            plaintext=body.password,
            workspace_id=workspace_id,
            description=f"SMTP password of {body.from_email}",
        )
        row.secret_id = secret.id
    db.add(row)
    await db.flush()
    await audit(
        db,
        "email_account.created",
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        entity_type="email_account",
        entity_id=row.id,
        diff={
            "from_email": row.from_email,
            "smtp_host": row.smtp_host,
            "password": body.password is not None,
        },
    )
    return await _one(db, row)


async def update_account(
    db: AsyncSession,
    secrets: SecretStore,
    workspace_id: int,
    actor_id: int,
    account_id: int,
    body: EmailAccountPatch,
) -> EmailAccountOut:
    row = await get_account(db, workspace_id, account_id, lock=True)
    changes = body.model_dump(exclude_unset=True, exclude={"password"})
    changes = {k: v for k, v in changes.items() if v is not None or k == "reply_to"}
    lo = changes.get("cooldown_min_seconds", row.cooldown_min_seconds)
    hi = changes.get("cooldown_max_seconds", row.cooldown_max_seconds)
    if hi < lo:
        raise AppError("cooldown_max_seconds must be ≥ cooldown_min_seconds", code="invalid_cooldown")
    for k, v in changes.items():
        setattr(row, k, v)
    if body.password is not None:
        if row.secret_id is None:
            secret = await secrets.put(
                db,
                purpose=SECRET_PURPOSE,
                plaintext=body.password,
                workspace_id=workspace_id,
                description=f"SMTP password of {row.from_email}",
            )
            row.secret_id = secret.id
        else:
            await secrets.replace(db, row.secret_id, body.password)
    await audit(
        db,
        "email_account.updated",
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        entity_type="email_account",
        entity_id=row.id,
        diff={**{k: str(v) for k, v in changes.items()}, "password_changed": body.password is not None},
    )
    return await _one(db, row)


async def set_status(
    db: AsyncSession, workspace_id: int, actor_id: int, account_id: int, *, pause: bool, reason: str = ""
) -> EmailAccountOut:
    row = await get_account(db, workspace_id, account_id, lock=True)
    previous = row.status
    if pause:
        row.status = AccountStatus.PAUSED
        row.paused_reason = reason or "пауза владельцем"
    else:
        row.status = AccountStatus.ACTIVE
        row.paused_reason = None
        row.consecutive_failures = 0
    await audit(
        db,
        "email_account.paused" if pause else "email_account.resumed",
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        entity_type="email_account",
        entity_id=row.id,
        diff={"from": previous.value, "to": row.status.value, "reason": row.paused_reason},
    )
    return await _one(db, row)


async def smtp_config(db: AsyncSession, secrets: SecretStore, account: EmailAccount) -> SmtpConfig:
    password = (
        await secrets.get(db, account.secret_id, workspace_id=account.workspace_id)
        if account.secret_id is not None
        else ""
    )
    return SmtpConfig(
        host=account.smtp_host,
        port=account.smtp_port,
        security=account.smtp_security.value,
        username=account.smtp_username,
        password=password,
    )


async def verify_account(
    db: AsyncSession,
    secrets: SecretStore,
    transport: EmailTransport,
    workspace_id: int,
    actor_id: int,
    account_id: int,
) -> AccountCheck:
    """Connect and log in without sending anything. Success clears an «error» status (a wrong password was
    fixed)."""
    row = await get_account(db, workspace_id, account_id, lock=True)
    config = await smtp_config(db, secrets, row)
    row.last_checked_at = datetime.now(UTC)
    try:
        await transport.verify(config)
    except IntegrationError as exc:
        row.last_error_code, row.last_error_message = exc.code.value, exc.human_message
        await audit(
            db,
            "email_account.verify_failed",
            workspace_id=workspace_id,
            actor_user_id=actor_id,
            entity_type="email_account",
            entity_id=row.id,
            diff={"error_code": exc.code.value},
        )
        return AccountCheck(
            ok=False, transport=transport.name, error_code=exc.code.value, message=exc.human_message
        )
    if row.status == AccountStatus.ERROR:
        row.status, row.paused_reason, row.consecutive_failures = AccountStatus.ACTIVE, None, 0
    row.last_error_code = row.last_error_message = None
    await audit(
        db,
        "email_account.verified",
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        entity_type="email_account",
        entity_id=row.id,
        diff={"transport": transport.name},
    )
    note = " (mock: реального подключения не было)" if transport.name == "mock" else ""
    return AccountCheck(
        ok=True, transport=transport.name, error_code=None, message=f"Подключение и вход успешны{note}."
    )
