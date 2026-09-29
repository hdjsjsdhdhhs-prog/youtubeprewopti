"""Shared FastAPI dependencies: DB session, authentication, CSRF, RBAC (ADR-0009)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Coroutine
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.crypto import SecretCipher
from app.core.db import get_sessionmaker
from app.core.errors import PermissionDeniedError
from app.core.secrets import SecretStore
from app.domains.identity.models import WorkspaceRole
from app.domains.identity.service import AuthContext, csrf_valid, resolve_session
from app.providers.storage import LocalFSStorage, StorageBackend

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


async def _db_session() -> AsyncIterator[AsyncSession]:
    """One transaction per request: commit on success, rollback on any error."""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


# scope="function": commit happens *before* the response is sent, so commit errors surface as 5xx.
DbSession = Annotated[AsyncSession, Depends(_db_session, scope="function")]
AppSettings = Annotated[Settings, Depends(get_settings)]


@lru_cache
def _local_storage(root: Path) -> LocalFSStorage:
    return LocalFSStorage(root)


def get_storage(settings: AppSettings) -> StorageBackend:
    return _local_storage(settings.storage_path)


def get_secret_store(settings: AppSettings) -> SecretStore:
    return SecretStore(SecretCipher(settings.master_key.get_secret_value()))


Storage = Annotated[StorageBackend, Depends(get_storage)]
Secrets = Annotated[SecretStore, Depends(get_secret_store)]


class PageParams:
    def __init__(
        self,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
        offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    ) -> None:
        self.limit = limit
        self.offset = offset


Paging = Annotated[PageParams, Depends()]


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _auth_context(request: Request, db: DbSession, settings: AppSettings) -> AuthContext:
    ctx = await resolve_session(db, settings, request.cookies.get(settings.session_cookie_name))
    if request.method not in SAFE_METHODS and not csrf_valid(
        ctx, request.cookies.get(settings.csrf_cookie_name), request.headers.get(settings.csrf_header_name)
    ):
        raise PermissionDeniedError("CSRF token missing or invalid", code="csrf_failed")
    return ctx


CurrentAuth = Annotated[AuthContext, Depends(_auth_context)]


class Permission(StrEnum):
    """Coarse permissions mapped to the minimum workspace role."""

    READ = "read"                    # view any workspace data
    WRITE = "write"                  # create/modify projects, queries, leads
    OUTREACH_SEND = "outreach_send"  # approve / send (always audited)
    MANAGE_INTEGRATIONS = "manage_integrations"
    MANAGE_MEMBERS = "manage_members"


PERMISSION_MIN_ROLE: dict[Permission, WorkspaceRole] = {
    Permission.READ: WorkspaceRole.VIEWER,
    Permission.WRITE: WorkspaceRole.OPERATOR,
    Permission.OUTREACH_SEND: WorkspaceRole.OPERATOR,
    Permission.MANAGE_INTEGRATIONS: WorkspaceRole.ADMIN,
    Permission.MANAGE_MEMBERS: WorkspaceRole.ADMIN,
}


def require(permission: Permission) -> Callable[..., Coroutine[Any, Any, AuthContext]]:
    """Dependency factory: ``ctx: Annotated[AuthContext, Depends(require(Permission.WRITE))]``."""
    minimum = PERMISSION_MIN_ROLE[permission]

    async def _check(ctx: CurrentAuth) -> AuthContext:
        if not ctx.has_role(minimum):
            raise PermissionDeniedError(
                f"Permission '{permission.value}' requires role '{minimum.value}' or higher",
                details={"permission": permission.value, "role": ctx.role.value},
            )
        return ctx

    return _check


ReadAuth = Annotated[AuthContext, Depends(require(Permission.READ))]
WriteAuth = Annotated[AuthContext, Depends(require(Permission.WRITE))]
