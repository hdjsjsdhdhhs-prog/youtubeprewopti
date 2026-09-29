"""The Procrastinate application (ADR-0003). Tasks are loaded from ``app.workers.tasks`` by the worker.

The API never imports this module: it enqueues through SQL in its own transaction
(``app.domains.jobs.service.enqueue_job``).
"""

from __future__ import annotations

import procrastinate

from app.core.config import get_settings, libpq_dsn


def _connector() -> procrastinate.PsycopgConnector:
    s = get_settings()
    # Pool: worker slots + listener + heartbeat/periodic deferrer.
    return procrastinate.PsycopgConnector(
        conninfo=libpq_dsn(s.database_url), min_size=1, max_size=max(4, s.worker_concurrency + 3)
    )


queue_app = procrastinate.App(connector=_connector(), import_paths=["app.workers.tasks"])
