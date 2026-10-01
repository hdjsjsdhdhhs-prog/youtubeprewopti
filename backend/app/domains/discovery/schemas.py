from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.domains.jobs.schemas import JobRunOut


class DiscoveryStart(BaseModel):
    """Which queries to run: explicit ``query_ids``, or every pending/failed query of the project
    (``include_done=true`` also re-runs finished ones)."""

    model_config = ConfigDict(extra="forbid")
    query_ids: list[int] | None = Field(default=None, min_length=1, max_length=1000)
    include_done: bool = False


class QuotaOut(BaseModel):
    provider: str | None  # None => YouTube is not configured
    mode: str | None  # "api" | "mock"
    quota_day: date | None  # Pacific Time day the counter belongs to
    used: int
    limit: int
    remaining: int


class DiscoveryStartResult(BaseModel):
    job: JobRunOut | None  # None => nothing to run
    created: bool  # False => an identical discovery job is already active (returned)
    queries: int
    quota_search_units: int  # search.list cost of this run (upper bound)
    quota_max_units: int  # upper bound incl. channel/video enrichment
    quota: QuotaOut
