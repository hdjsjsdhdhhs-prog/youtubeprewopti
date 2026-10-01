from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from app.core.db import Base, IdMixin, TimestampMixin
from app.core.types import pg_enum

COST = Numeric(14, 6)  # single calls cost fractions of a cent


class AIModel(IdMixin, TimestampMixin, Base):
    """Model registry (ADR-0007 §2). Business code refers to ``key``; ``api_model_id`` is what the
    provider is called with. Prices are per 1M tokens; NULL = not known (estimates are then unavailable)."""

    __tablename__ = "ai_models"
    key: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    api_model_id: Mapped[str] = mapped_column(String(120), nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(ARRAY(String(30)), nullable=False, default=list)
    price_input_per_1m: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    price_output_per_1m: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    price_per_image: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )  # image generation (Phase 5): {"<quality>@<size>": usd}
    pricing_verified_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")


class PromptTemplate(IdMixin, Base):
    """A registered prompt version (``app/prompts/{name}/v{N}.md``). A file whose content no longer
    matches the registered hash is rejected: changing a used prompt requires a new version."""

    __tablename__ = "prompt_templates"
    __table_args__ = (UniqueConstraint("name", "version"),)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    output_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )


class AICallStatus(StrEnum):
    PENDING = "pending"  # reserved (counted against budgets) and in flight / worker died mid-call
    OK = "ok"
    REPAIRED = "repaired"  # valid after the single repair attempt
    INVALID_OUTPUT = "invalid_output"  # schema validation failed; never stored as a result
    REFUSED = "refused"  # provider/model refused (moderation) — not an infrastructure failure
    ERROR = "error"


class AICall(IdMixin, TimestampMixin, Base):
    """One provider request (ADR-0007, AI_ARCHITECTURE §10). The row is inserted *before* the call with
    its estimated cost (so concurrent jobs see the reservation) and completed afterwards."""

    __tablename__ = "ai_calls"
    __table_args__ = (
        Index("ix_ai_calls_task_created", "task", "created_at"),
        Index("ix_ai_calls_workspace_created", "workspace_id", "created_at"),
        CheckConstraint("attempt >= 1", name="attempt_positive"),
    )
    workspace_id: Mapped[int | None] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("search_projects.id", ondelete="SET NULL"))
    job_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("job_runs.id", ondelete="SET NULL"), index=True
    )
    task: Mapped[str] = mapped_column(String(60), nullable=False)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    model_key: Mapped[str] = mapped_column(String(60), nullable=False)
    api_model_id: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_template_id: Mapped[int | None] = mapped_column(
        ForeignKey("prompt_templates.id", ondelete="SET NULL")
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)  # 2 = repair request
    status: Mapped[AICallStatus] = mapped_column(
        pg_enum(AICallStatus, "ai_call_status"), nullable=False, default=AICallStatus.PENDING,
        server_default=AICallStatus.PENDING.value,
    )
    input_ref: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # validated output only
    raw_output: Mapped[str | None] = mapped_column(Text)  # kept for debugging invalid output
    validation_errors: Mapped[list[Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_message: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    image_inputs: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    image_outputs: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(COST)
    actual_cost_usd: Mapped[Decimal | None] = mapped_column(COST)
    duration_ms: Mapped[int | None] = mapped_column(Integer)


class BudgetScope(StrEnum):
    GLOBAL = "global"  # all AI calls of the workspace
    PROJECT = "project"  # scope_ref = project id
    TASK = "task"  # scope_ref = AI task (thumbnail_analysis, …)


class BudgetPeriod(StrEnum):
    DAY = "day"  # UTC calendar day
    MONTH = "month"  # UTC calendar month
    TOTAL = "total"  # all time


class Budget(IdMixin, TimestampMixin, Base):
    """Optional spending limits (§33, §75). No rows = no limit; bulk runs still need confirmation."""

    __tablename__ = "budgets"
    __table_args__ = (
        UniqueConstraint("workspace_id", "scope", "scope_ref", "period", postgresql_nulls_not_distinct=True),
        CheckConstraint("limit_usd IS NOT NULL OR max_ai_operations IS NOT NULL", name="has_limit"),
        CheckConstraint("limit_usd IS NULL OR limit_usd >= 0", name="limit_nonneg"),
        CheckConstraint("max_ai_operations IS NULL OR max_ai_operations >= 0", name="ops_nonneg"),
        CheckConstraint("(scope = 'global') = (scope_ref IS NULL)", name="scope_ref_matches"),
    )
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scope: Mapped[BudgetScope] = mapped_column(pg_enum(BudgetScope, "budget_scope"), nullable=False)
    scope_ref: Mapped[str | None] = mapped_column(String(100))
    period: Mapped[BudgetPeriod] = mapped_column(pg_enum(BudgetPeriod, "budget_period"), nullable=False)
    limit_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    max_ai_operations: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
