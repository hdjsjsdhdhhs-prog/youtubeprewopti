"""Test setup (ADR-0014): real PostgreSQL (``YTL_TEST_DATABASE_URL``), schema via Alembic once per
session, every test inside a transaction that is rolled back.

Config source: ``YTL_ENV_FILE`` or, if unset, ``%USERPROFILE%/.ytlead-secrets/backend.conf``.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

_default_conf = Path.home() / ".ytlead-secrets" / "backend.conf"
if "YTL_ENV_FILE" not in os.environ and _default_conf.is_file():
    os.environ["YTL_ENV_FILE"] = str(_default_conf)

from app.core.config import get_settings  # noqa: E402
from app.core.db import use_selector_event_loop_on_windows  # noqa: E402

_test_url = os.environ.get("YTL_TEST_DATABASE_URL") or get_settings().test_database_url
if not _test_url:
    raise RuntimeError("YTL_TEST_DATABASE_URL is not configured; tests never run against the dev database")
# Everything in the test process (app engine, CLI) talks to the test database only.
os.environ["YTL_DATABASE_URL"] = _test_url
os.environ["YTL_MIGRATION_DATABASE_URL"] = _test_url
os.environ["YTL_ENV"] = "test"
os.environ["YTL_LOG_JSON"] = "false"
os.environ["YTL_LOG_LEVEL"] = "WARNING"
# Real AI keys from the private config must never reach tests (paid calls, flaky network): tests that need
# a provider set it explicitly and use mocked transports.
os.environ["YTL_VIBECODE_API_KEY"] = ""
os.environ["YTL_OPENAI_API_KEY"] = ""
get_settings.cache_clear()

use_selector_event_loop_on_windows()

import httpx  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from alembic import command  # noqa: E402
from app.api.deps import _db_session, get_storage  # noqa: E402
from app.core.config import BACKEND_DIR, Settings  # noqa: E402
from app.domains.identity.models import User, Workspace, WorkspaceMember, WorkspaceRole  # noqa: E402
from app.domains.identity.service import create_owner  # noqa: E402
from app.main import create_app  # noqa: E402
from app.providers.storage import LocalFSStorage  # noqa: E402

TEST_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(scope="session", autouse=True)
def _migrated_database() -> None:
    """Recreate the schema from scratch (also exercises downgrade + upgrade of all migrations)."""
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.attributes["database_url"] = _test_url
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
async def engine():
    eng = create_async_engine(_test_url, poolclass=NullPool)
    yield eng
    await eng.dispose()


@pytest.fixture
async def db(engine) -> AsyncIterator[AsyncSession]:
    """Session bound to an outer transaction; code-level commits become SAVEPOINT releases."""
    async with engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()


@pytest.fixture
def settings() -> Settings:
    return get_settings()


@pytest.fixture
def storage(tmp_path) -> LocalFSStorage:
    return LocalFSStorage(tmp_path / "storage")


@pytest.fixture
async def other_owner(db: AsyncSession) -> tuple[User, Workspace]:
    """A second, unrelated workspace for isolation tests."""
    user, ws = await create_owner(
        db, email="other@example.com", password=TEST_PASSWORD, display_name="Other", workspace_name="Other WS"
    )
    await db.flush()
    return user, ws


@pytest.fixture
def app(db: AsyncSession, storage: LocalFSStorage) -> FastAPI:
    application = create_app()

    async def _override() -> AsyncIterator[AsyncSession]:
        yield db
        await db.commit()

    application.dependency_overrides[_db_session] = _override
    application.dependency_overrides[get_storage] = lambda: storage
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, client=("10.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest.fixture
async def owner(db: AsyncSession) -> tuple[User, Workspace]:
    user, ws = await create_owner(
        db, email="Owner@Example.com", password=TEST_PASSWORD, display_name="Owner", workspace_name="Test WS"
    )
    await db.flush()
    return user, ws


async def add_member(db: AsyncSession, ws: Workspace, email: str, role: WorkspaceRole) -> User:
    from app.core.security import hash_password

    user = User(email=email, password_hash=hash_password(TEST_PASSWORD), display_name=email)
    db.add(user)
    await db.flush()
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role=role))
    await db.flush()
    return user


async def login(client: httpx.AsyncClient, email: str, password: str = TEST_PASSWORD) -> httpx.Response:
    return await client.post("/api/auth/login", json={"email": email, "password": password})


def csrf_headers(client: httpx.AsyncClient, settings: Settings) -> dict[str, str]:
    return {settings.csrf_header_name: client.cookies.get(settings.csrf_cookie_name) or ""}
