"""Worker entrypoint::

    python -m app.workers                       # all queues
    python -m app.workers --queues bulk,interactive --concurrency 2
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import psycopg
from procrastinate.app import WorkerOptions

from app.core.config import get_settings, libpq_dsn
from app.core.db import dispose_engine, use_selector_event_loop_on_windows
from app.core.logging import configure_logging, get_logger
from app.domains.jobs.service import JobQueue

log = get_logger("app.workers")


async def check_lc_messages(dsn: str) -> str:
    """Procrastinate detects unique violations by the English message text (ADR-0003)."""
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        cur = await conn.execute("SHOW lc_messages")
        row = await cur.fetchone()
    return str(row[0]) if row else ""


async def _run(args: argparse.Namespace) -> int:
    from app.workers.queue import queue_app  # after logging/settings are configured

    settings = get_settings()
    lc = await check_lc_messages(libpq_dsn(settings.database_url))
    if lc not in ("C", "POSIX") and not lc.lower().startswith("en"):
        log.error("worker.bad_lc_messages", lc_messages=lc,
                  hint="Run as superuser: ALTER SYSTEM SET lc_messages TO 'C'; SELECT pg_reload_conf();")
        return 1

    queues: list[str] = [q.strip() for q in args.queues.split(",") if q.strip()] if args.queues else []
    log.info("worker.starting", queues=queues or "all", concurrency=args.concurrency,
             thumbnail_fetcher=settings.effective_thumbnail_fetcher)
    options: WorkerOptions = {
        "name": args.name,
        "concurrency": args.concurrency,
        "install_signal_handlers": sys.platform != "win32",  # not supported on Windows
    }
    if queues:  # omitted => all queues (an empty list would match none)
        options["queues"] = queues
    try:
        async with queue_app.open_async():
            await queue_app.run_worker_async(**options)
    finally:
        await dispose_engine()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.workers")
    parser.add_argument("--queues", help=f"Comma-separated subset of: {', '.join(q.value for q in JobQueue)}")
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--name", default="worker")
    args = parser.parse_args(argv)
    if args.concurrency is None:
        args.concurrency = get_settings().worker_concurrency
    if args.queues:
        unknown = {q.strip() for q in args.queues.split(",")} - {q.value for q in JobQueue} - {""}
        if unknown:
            parser.error(f"unknown queue(s): {', '.join(sorted(unknown))}")

    use_selector_event_loop_on_windows()
    configure_logging()
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        log.info("worker.stopped", reason="keyboard interrupt")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
