from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.core.state_machine import JobStatus


class JobRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    status: JobStatus
    queue: str
    priority: int
    progress_total: int
    progress_done: int
    attempts: int
    budget_usd: Decimal | None
    estimated_cost_usd: Decimal | None
    actual_cost_usd: Decimal
    parent_id: int | None
    created_by: int | None
    error_code: str | None
    error_human: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    result: dict[str, Any]  # small summary written by the handler (counts, ≤ 50 failures)


class JobRunDetail(JobRunOut):
    params: dict[str, Any]


class EnqueueResult(BaseModel):
    job: JobRunOut | None  # None => nothing to do
    created: bool  # False => an identical active job already existed
    items: int
