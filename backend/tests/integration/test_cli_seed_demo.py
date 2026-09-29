"""End-to-end ``seed-demo`` CLI test: commits to the test database, so it cleans up after itself.
Storage is redirected to a temporary directory (never the real ``storage/``)."""

import asyncio

import pytest
from sqlalchemy import delete, func, select

from app import cli
from app.core.config import get_settings
from app.core.db import dispose_engine, get_sessionmaker
from app.domains.demo.service import purge_storage_files, remove_demo
from app.domains.identity.models import Workspace
from app.domains.media.models import ImageAsset, ImageSource
from app.domains.projects.models import SearchProject
from app.domains.youtube.models import Channel
from app.providers.storage import LocalFSStorage

SLUG = "cli-demo-ws"


async def _cleanup(storage: LocalFSStorage) -> None:
    async with get_sessionmaker()() as s:
        ws_id = await s.scalar(select(Workspace.id).where(Workspace.slug == SLUG))
        if ws_id is not None:
            removed = await remove_demo(s, storage.name, ws_id)
            await s.execute(delete(Workspace).where(Workspace.id == ws_id))
            await s.commit()
            await purge_storage_files(s, storage, removed.storage_keys)
    await dispose_engine()


async def _create_workspace() -> None:
    async with get_sessionmaker()() as s:
        s.add(Workspace(name="CLI demo", slug=SLUG))
        await s.commit()
    await dispose_engine()


async def _counts() -> tuple[int, int, int]:
    async with get_sessionmaker()() as s:
        ws_id = await s.scalar(select(Workspace.id).where(Workspace.slug == SLUG))
        projects = await s.scalar(
            select(func.count()).select_from(SearchProject)
            .where(SearchProject.workspace_id == ws_id, SearchProject.is_demo.is_(True))
        )
        channels = await s.scalar(select(func.count()).select_from(Channel).where(Channel.is_demo.is_(True)))
        images = await s.scalar(
            select(func.count()).select_from(ImageAsset).where(ImageAsset.source == ImageSource.DEMO)
        )
    await dispose_engine()
    return projects, channels, images


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setenv("YTL_STORAGE_PATH", str(tmp_path / "storage"))
    get_settings.cache_clear()
    st = LocalFSStorage(tmp_path / "storage")
    asyncio.run(_cleanup(st))
    asyncio.run(_create_workspace())
    yield st
    asyncio.run(_cleanup(st))
    monkeypatch.undo()
    get_settings.cache_clear()


def _files(storage: LocalFSStorage) -> list:
    return [p for p in storage.root.rglob("*.jpg") if p.is_file()]


def test_seed_demo_cli_seed_rerun_reset_remove(storage, capsys):
    assert cli.main(["seed-demo", "--workspace", SLUG]) == 0
    out = capsys.readouterr().out
    assert "OK: demo data seeded into workspace 'cli-demo-ws': 2 projects, 11 channels" in out
    assert asyncio.run(_counts()) == (2, 11, 132)
    assert len(_files(storage)) == 132

    assert cli.main(["seed-demo", "--workspace", SLUG]) == 0
    assert "already present" in capsys.readouterr().out
    assert asyncio.run(_counts()) == (2, 11, 132)

    assert cli.main(["seed-demo", "--workspace", SLUG, "--reset"]) == 0
    out = capsys.readouterr().out
    assert "removed demo data" in out and "demo data seeded" in out
    assert asyncio.run(_counts()) == (2, 11, 132)
    assert len(_files(storage)) == 132  # reset must not delete files of re-created assets

    assert cli.main(["seed-demo", "--workspace", SLUG, "--remove"]) == 0
    assert "11 channels, 132 images" in capsys.readouterr().out
    assert asyncio.run(_counts()) == (0, 0, 0)
    assert _files(storage) == []


def test_seed_demo_cli_unknown_workspace(storage, capsys):
    assert cli.main(["seed-demo", "--workspace", "no-such-workspace"]) == 1
    assert "not found" in capsys.readouterr().err


def test_seed_demo_cli_reset_and_remove_are_exclusive(storage):
    with pytest.raises(SystemExit):
        cli.main(["seed-demo", "--workspace", SLUG, "--reset", "--remove"])
