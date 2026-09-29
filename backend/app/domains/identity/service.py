"""Identity use cases: owner bootstrap, login/logout, session resolution, audit (ADR-0009)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AuthenticationError, ConflictError, RateLimitedError
from app.core.security import (
    burn_verify_time,
    constant_time_equals,
    hash_password,
    needs_rehash,
    new_token,
    token_hash,
    verify_password,
)
from app.domains.identity.models import (
    ROLE_RANK,
    AuditLog,
    User,
    UserSession,
    Workspace,
    WorkspaceMember,
    WorkspaceRole,
)

LOGIN_FAILED_ACTION = "auth.login_failed"
# Refresh last_seen_at at most this often to avoid a write on every request.
LAST_SEEN_RESOLUTION = timedelta(minutes=1)


def utcnow() -> datetime:
    return datetime.now(UTC)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:100] or "workspace"


async def audit(
    db: AsyncSession,
    action: str,
    *,
    workspace_id: int | None = None,
    actor_user_id: int | None = None,
    entity_type: str | None = None,
    entity_id: str | int | None = None,
    diff: dict | None = None,
    ip: str | None = None,
) -> None:
    db.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action=action,
            entity_type=entity_type,
            entity_id=None if entity_id is None else str(entity_id),
            diff=diff or {},
            ip=ip,
        )
    )


# ---------------------------------------------------------------------------
# Owner bootstrap (CLI only; there is no public registration)
# ---------------------------------------------------------------------------
async def create_owner(
    db: AsyncSession, *, email: str, password: str, display_name: str, workspace_name: str
) -> tuple[User, Workspace]:
    email = normalize_email(email)
    if await db.scalar(select(User.id).where(User.email == email)) is not None:
        raise ConflictError(f"User {email} already exists", code="user_exists")
    slug = slugify(workspace_name)
    if await db.scalar(select(Workspace.id).where(Workspace.slug == slug)) is not None:
        raise ConflictError(f"Workspace with slug '{slug}' already exists", code="workspace_exists")

    user = User(email=email, password_hash=hash_password(password), display_name=display_name)
    workspace = Workspace(name=workspace_name, slug=slug)
    db.add_all([user, workspace])
    await db.flush()
    db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role=WorkspaceRole.OWNER))
    await audit(db, "user.owner_created", workspace_id=workspace.id, actor_user_id=user.id,
                entity_type="user", entity_id=user.id, diff={"email": email})
    return user, workspace


# ---------------------------------------------------------------------------
# Login rate limiting (PostgreSQL-backed via audit_logs: works across processes)
# ---------------------------------------------------------------------------
async def _check_login_rate_limit(db: AsyncSession, settings: Settings, email: str, ip: str | None) -> None:
    since = utcnow() - timedelta(seconds=settings.login_rate_limit_window_seconds)
    base = select(func.count()).select_from(AuditLog).where(
        AuditLog.action == LOGIN_FAILED_ACTION, AuditLog.created_at >= since
    )
    by_email = await db.scalar(base.where(AuditLog.diff["email"].astext == email)) or 0
    by_ip = (await db.scalar(base.where(AuditLog.ip == ip)) or 0) if ip else 0
    if max(by_email, by_ip) >= settings.login_rate_limit_attempts:
        raise RateLimitedError(
            "Too many failed login attempts. Try again later.",
            details={"retry_after_seconds": settings.login_rate_limit_window_seconds},
        )


@dataclass(frozen=True)
class IssuedSession:
    session: UserSession
    token: str
    csrf_token: str


async def login(
    db: AsyncSession,
    settings: Settings,
    *,
    email: str,
    password: str,
    ip: str | None,
    user_agent: str | None,
) -> IssuedSession:
    """Verify credentials and issue a fresh session (new token on every login = rotation).

    Failed attempts are committed by the caller even though an error is raised.
    """
    email = normalize_email(email)
    await _check_login_rate_limit(db, settings, email, ip)

    user = await db.scalar(select(User).where(User.email == email))
    if user is None:
        burn_verify_time(password)  # equalize timing for unknown users
        ok = False
    else:
        ok = verify_password(user.password_hash, password) and user.is_active

    membership = None
    if ok and user is not None:
        membership = await db.scalar(
            select(WorkspaceMember).where(WorkspaceMember.user_id == user.id).order_by(WorkspaceMember.id)
        )
    if not ok or user is None or membership is None:
        await audit(db, LOGIN_FAILED_ACTION, actor_user_id=user.id if user else None,
                    diff={"email": email}, ip=ip)
        raise AuthenticationError("Invalid email or password", code="invalid_credentials")

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    now = utcnow()
    user.last_login_at = now

    token, csrf = new_token(), new_token()
    session = UserSession(
        user_id=user.id,
        workspace_id=membership.workspace_id,
        token_hash=token_hash(token),
        csrf_hash=token_hash(csrf),
        ip=ip,
        user_agent=(user_agent or "")[:400] or None,
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(hours=settings.session_absolute_hours),
    )
    db.add(session)
    await db.flush()
    await audit(db, "auth.login", workspace_id=membership.workspace_id, actor_user_id=user.id,
                entity_type="session", entity_id=session.id, ip=ip)
    return IssuedSession(session=session, token=token, csrf_token=csrf)


@dataclass(frozen=True)
class AuthContext:
    user: User
    workspace: Workspace
    role: WorkspaceRole
    session: UserSession

    def has_role(self, minimum: WorkspaceRole) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[minimum]


async def resolve_session(db: AsyncSession, settings: Settings, token: str | None) -> AuthContext:
    """Return the auth context for a session token or raise AuthenticationError."""
    if not token:
        raise AuthenticationError("Authentication required")
    row = (
        await db.execute(
            select(UserSession, User, Workspace, WorkspaceMember.role)
            .join(User, User.id == UserSession.user_id)
            .join(Workspace, Workspace.id == UserSession.workspace_id)
            .join(
                WorkspaceMember,
                (WorkspaceMember.workspace_id == UserSession.workspace_id)
                & (WorkspaceMember.user_id == UserSession.user_id),
            )
            .where(UserSession.token_hash == token_hash(token))
        )
    ).first()
    if row is None:
        raise AuthenticationError("Session is invalid or expired", code="session_invalid")
    session, user, workspace, role = row
    now = utcnow()
    idle_deadline = session.last_seen_at + timedelta(minutes=settings.session_idle_minutes)
    expired = now >= session.expires_at or now >= idle_deadline
    if session.revoked_at is not None or not user.is_active or expired:
        raise AuthenticationError("Session is invalid or expired", code="session_invalid")
    if now - session.last_seen_at >= LAST_SEEN_RESOLUTION:
        session.last_seen_at = now
    return AuthContext(user=user, workspace=workspace, role=role, session=session)


def csrf_valid(ctx: AuthContext, cookie_token: str | None, header_token: str | None) -> bool:
    """Double-submit check, additionally bound to the session (header must match the issued token)."""
    if not cookie_token or not header_token:
        return False
    return constant_time_equals(cookie_token, header_token) and constant_time_equals(
        token_hash(header_token), ctx.session.csrf_hash
    )


async def logout(db: AsyncSession, ctx: AuthContext, *, ip: str | None) -> None:
    ctx.session.revoked_at = utcnow()
    await audit(db, "auth.logout", workspace_id=ctx.workspace.id, actor_user_id=ctx.user.id,
                entity_type="session", entity_id=ctx.session.id, ip=ip)
