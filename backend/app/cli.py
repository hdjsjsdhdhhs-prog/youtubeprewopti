"""Admin CLI.

    python -m app.cli create-owner --email owner@example.com --name "Owner" --workspace "My workspace"

The password is read interactively (no echo) or from stdin with ``--password-stdin``;
it is never accepted as a command-line argument (would leak into shell history / process list).

    python -m app.cli seed-demo [--workspace SLUG] [--reset | --remove]

Creates clearly labelled synthetic demo data (see ``app.domains.demo.service``). ``--workspace`` may be
omitted when exactly one workspace exists. ``--reset`` removes the workspace's demo data and seeds it
again; ``--remove`` only removes it.

    python -m app.cli ai-models

Lists the AI model registry and, with a configured provider, checks every API model ID against the
provider's ``GET /v1/models`` (Q-004). Exit code 1 if the provider could not be queried.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import dispose_engine, get_sessionmaker, use_selector_event_loop_on_windows
from app.core.errors import AppError, IntegrationError, NotFoundError
from app.core.security import MIN_PASSWORD_LENGTH
from app.domains.demo.service import purge_storage_files, remove_demo, seed_demo
from app.domains.identity.models import Workspace
from app.domains.identity.service import create_owner
from app.providers.storage import LocalFSStorage


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    first = getpass.getpass(f"Password (min {MIN_PASSWORD_LENGTH} chars): ")
    if first != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords do not match")
    return first


async def _create_owner(args: argparse.Namespace, password: str) -> int:
    try:
        async with get_sessionmaker()() as db:
            user, ws = await create_owner(
                db, email=args.email, password=password, display_name=args.name, workspace_name=args.workspace
            )
            await db.commit()
    except (AppError, ValueError) as exc:
        print(f"ERROR: {getattr(exc, 'message', exc)}", file=sys.stderr)
        return 1
    finally:
        await dispose_engine()
    print(f"OK: owner {user.email} (id={user.id}) created in workspace '{ws.name}' (slug={ws.slug}).")
    return 0


async def _resolve_workspace(db: AsyncSession, slug: str | None) -> Workspace:
    if slug:
        ws = await db.scalar(select(Workspace).where(Workspace.slug == slug))
        if ws is None:
            raise NotFoundError(f"Workspace with slug '{slug}' not found")
        return ws
    workspaces = list(await db.scalars(select(Workspace).order_by(Workspace.id).limit(2)))
    if len(workspaces) != 1:
        raise AppError(
            "No workspaces exist; run create-owner first" if not workspaces
            else "Several workspaces exist; pass --workspace SLUG"
        )
    return workspaces[0]


async def _seed_demo(args: argparse.Namespace) -> int:
    storage = LocalFSStorage(get_settings().storage_path)
    try:
        async with get_sessionmaker()() as db:
            ws = await _resolve_workspace(db, args.workspace)
            removed = None
            if args.reset or args.remove:
                removed = await remove_demo(db, storage.name, ws.id)
            seeded = None if args.remove else await seed_demo(db, storage, ws.id)
            await db.commit()
            if removed is not None:
                await purge_storage_files(db, storage, removed.storage_keys)
    except AppError as exc:
        print(f"ERROR: {exc.message}", file=sys.stderr)
        return 1
    finally:
        await dispose_engine()
    if removed is not None:
        print(
            f"OK: removed demo data from workspace '{ws.slug}': {removed.projects_deleted} projects, "
            f"{removed.channels_deleted} channels, {removed.images_deleted} images."
        )
    if seeded is not None:
        if seeded.created_anything:
            print(
                f"OK: demo data seeded into workspace '{ws.slug}': {seeded.projects_created} projects, "
                f"{seeded.channels_created} channels, {seeded.videos_created} videos, "
                f"{seeded.thumbnails_stored} thumbnails."
            )
        else:
            print(f"OK: demo data already present in workspace '{ws.slug}'; nothing to do.")
    return 0


async def _ai_models() -> int:
    """Registry vs. what the configured provider actually offers (Q-004)."""
    from app.domains.ai.models import AIModel
    from app.domains.ai.registry import sync_registry
    from app.providers.ai import open_ai_provider

    settings = get_settings()
    try:
        async with get_sessionmaker()() as db:
            await sync_registry(db, settings)
            await db.commit()
            models = list(await db.scalars(select(AIModel).order_by(AIModel.provider, AIModel.key)))
        provider_name = settings.effective_ai_provider
        print(f"Active AI provider: {provider_name or 'not configured'}")
        available: set[str] | None = None
        if provider_name is not None:
            try:
                async with open_ai_provider(settings) as provider:
                    available = set(await provider.list_models())
            except IntegrationError as exc:
                print(f"ERROR: cannot list provider models: {exc.human_message}", file=sys.stderr)
        for m in models:
            price = (
                f"${m.price_input_per_1m}/${m.price_output_per_1m} per 1M"
                if m.price_input_per_1m is not None and m.price_output_per_1m is not None else "price unknown"
            )
            if available is None or m.provider != provider_name:
                state = "-"
            else:
                state = "AVAILABLE" if m.api_model_id in available else "NOT FOUND"
            flag = "" if m.enabled else " (disabled)"
            print(f"  {m.key:<16} {m.provider:<7} {m.api_model_id:<22} {state:<10} {price}{flag}")
        if available is not None and provider_name == "openai":
            print("Provider models (first 50): " + ", ".join(sorted(available)[:50]))
    finally:
        await dispose_engine()
    return 0 if available is not None or settings.effective_ai_provider is None else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("create-owner", help="Create the first user (workspace owner)")
    p.add_argument("--email", required=True)
    p.add_argument("--name", default="Owner", help="Display name")
    p.add_argument("--workspace", default="Default", help="Workspace name")
    p.add_argument("--password-stdin", action="store_true", help="Read the password from stdin")
    d = sub.add_parser("seed-demo", help="Create labelled synthetic demo data (idempotent)")
    d.add_argument("--workspace", help="Workspace slug (optional if only one workspace exists)")
    mode = d.add_mutually_exclusive_group()
    mode.add_argument("--reset", action="store_true", help="Remove the workspace's demo data and seed again")
    mode.add_argument("--remove", action="store_true", help="Only remove the workspace's demo data")
    sub.add_parser("ai-models", help="Show the AI model registry and check the IDs against the provider")
    args = parser.parse_args(argv)

    if args.command == "create-owner":
        password = _read_password(args.password_stdin)
        use_selector_event_loop_on_windows()
        return asyncio.run(_create_owner(args, password))
    if args.command == "seed-demo":
        use_selector_event_loop_on_windows()
        return asyncio.run(_seed_demo(args))
    if args.command == "ai-models":
        use_selector_event_loop_on_windows()
        return asyncio.run(_ai_models())
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
