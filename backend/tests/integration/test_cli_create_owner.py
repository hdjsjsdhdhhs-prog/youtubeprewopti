"""End-to-end CLI test: commits to the test database, so it cleans up after itself."""

import asyncio
import io

import pytest
from sqlalchemy import delete, select

from app import cli
from app.core.db import dispose_engine, get_sessionmaker
from app.core.security import verify_password
from app.domains.identity.models import User, Workspace, WorkspaceMember, WorkspaceRole

EMAIL = "cli-owner@example.com"
SLUG = "cli-workspace"


async def _cleanup() -> None:
    async with get_sessionmaker()() as s:
        await s.execute(delete(User).where(User.email == EMAIL))
        await s.execute(delete(Workspace).where(Workspace.slug == SLUG))
        await s.commit()
    await dispose_engine()


async def _load():
    async with get_sessionmaker()() as s:
        user = await s.scalar(select(User).where(User.email == EMAIL))
        member = await s.scalar(select(WorkspaceMember).where(WorkspaceMember.user_id == user.id))
    await dispose_engine()
    return user, member


@pytest.fixture
def cleanup():
    asyncio.run(_cleanup())
    yield
    asyncio.run(_cleanup())


def _run(monkeypatch, password: str) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(password + "\n"))
    return cli.main([
        "create-owner", "--email", EMAIL.upper(), "--name", "CLI",
        "--workspace", "CLI Workspace", "--password-stdin",
    ])


def test_create_owner_then_duplicate_is_rejected(monkeypatch, capsys, cleanup):
    assert _run(monkeypatch, "cli-password-123456") == 0
    assert "OK: owner cli-owner@example.com" in capsys.readouterr().out

    user, member = asyncio.run(_load())
    assert verify_password(user.password_hash, "cli-password-123456")
    assert member.role == WorkspaceRole.OWNER

    assert _run(monkeypatch, "cli-password-123456") == 1
    assert "already exists" in capsys.readouterr().err


def test_create_owner_rejects_short_password(monkeypatch, capsys, cleanup):
    assert _run(monkeypatch, "short") == 1
    assert "at least" in capsys.readouterr().err
