"""Budgets and spend accounting (§33, §75–76, AI_ARCHITECTURE §10).

* **Spend** = Σ ``coalesce(actual_cost_usd, estimated_cost_usd)`` of ``ai_calls``; in-flight calls count with
  their estimate, so parallel jobs see each other's reservations.
* **Reservation**: before every provider call the runner inserts a ``pending`` ``ai_calls`` row under a
  per-workspace advisory lock, after checking every applicable active budget and the job's own
  ``budget_usd``. A call that would exceed a limit is not made (``budget_exceeded``).
* **No budgets = no limit.** Bulk runs still need explicit confirmation of the estimate (``confirm_token``).
* A USD limit cannot be enforced without prices: with an applicable USD limit and an unpriced model the
  call is refused (``pricing_unknown``) instead of silently ignoring the limit.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ConflictError
from app.domains.ai.models import AICall, Budget, BudgetPeriod, BudgetScope

_LOCK_SQL = text("SELECT pg_advisory_xact_lock(hashtext('ytl_ai_budget'), CAST(:ws AS integer))")
ZERO = Decimal(0)


class BudgetExceededError(ConflictError):
    code = "budget_exceeded"


class PricingUnknownError(ConflictError):
    code = "pricing_unknown"


def _period_start(period: BudgetPeriod) -> ColumnElement[Any] | None:
    if period == BudgetPeriod.DAY:
        return func.date_trunc("day", func.timezone("UTC", func.now()))
    if period == BudgetPeriod.MONTH:
        return func.date_trunc("month", func.timezone("UTC", func.now()))
    return None


def scope_condition(budget: Budget) -> ColumnElement[bool]:
    conds: list[ColumnElement[bool]] = [AICall.workspace_id == budget.workspace_id]
    if budget.scope == BudgetScope.PROJECT and budget.scope_ref is not None:
        conds.append(AICall.project_id == int(budget.scope_ref))
    elif budget.scope == BudgetScope.TASK:
        conds.append(AICall.task == budget.scope_ref)
    start = _period_start(budget.period)
    if start is not None:
        conds.append(func.timezone("UTC", AICall.created_at) >= start)
    return and_(*conds)


SPENT = func.coalesce(func.sum(func.coalesce(AICall.actual_cost_usd, AICall.estimated_cost_usd)), 0)


@dataclass(frozen=True)
class BudgetUsage:
    budget: Budget
    spent_usd: Decimal
    operations: int

    @property
    def remaining_usd(self) -> Decimal | None:
        return None if self.budget.limit_usd is None else max(ZERO, self.budget.limit_usd - self.spent_usd)

    @property
    def remaining_operations(self) -> int | None:
        limit = self.budget.max_ai_operations
        return None if limit is None else max(0, limit - self.operations)


async def budget_usage(db: AsyncSession, budget: Budget) -> BudgetUsage:
    row = (await db.execute(select(SPENT, func.count(AICall.id)).where(scope_condition(budget)))).one()
    return BudgetUsage(budget=budget, spent_usd=Decimal(row[0]), operations=int(row[1]))


async def applicable_budgets(
    db: AsyncSession, workspace_id: int, *, project_id: int | None, task: str
) -> list[Budget]:
    scope_match = or_(
        Budget.scope == BudgetScope.GLOBAL,
        and_(Budget.scope == BudgetScope.TASK, Budget.scope_ref == task),
        *(
            [and_(Budget.scope == BudgetScope.PROJECT, Budget.scope_ref == str(project_id))]
            if project_id is not None else []
        ),
    )
    return list(
        await db.scalars(
            select(Budget).where(Budget.workspace_id == workspace_id, Budget.is_active, scope_match)
            .order_by(Budget.id)
        )
    )


@dataclass(frozen=True)
class BudgetCheck:
    usage: BudgetUsage
    would_exceed: bool
    reason: str | None


def check_usage(usage: BudgetUsage, *, cost: Decimal | None, operations: int) -> BudgetCheck:
    """Would ``operations`` more calls costing ``cost`` in total (None = unknown) exceed the budget?"""
    b = usage.budget
    if b.max_ai_operations is not None and usage.operations + operations > b.max_ai_operations:
        return BudgetCheck(usage, True, f"operations limit {b.max_ai_operations} ({usage.operations} used)")
    if b.limit_usd is not None:
        if cost is None:
            return BudgetCheck(usage, True, "USD limit set but the model price is unknown")
        if usage.spent_usd + cost > b.limit_usd:
            return BudgetCheck(usage, True, f"USD limit {b.limit_usd} ({usage.spent_usd} spent)")
    return BudgetCheck(usage, False, None)


def describe_budget(b: Budget) -> str:
    target = {"global": "workspace", "project": f"project {b.scope_ref}", "task": f"task {b.scope_ref}"}
    return f"{b.period.value} budget of {target[b.scope.value]}"


async def lock_workspace_budget(db: AsyncSession, workspace_id: int | None) -> None:
    """Serialize reservations of one workspace until the end of the transaction."""
    await db.execute(_LOCK_SQL, {"ws": workspace_id or 0})


async def job_spent(db: AsyncSession, job_run_id: int) -> Decimal:
    return Decimal(await db.scalar(select(SPENT).where(AICall.job_run_id == job_run_id)) or 0)


async def assert_within_budgets(
    db: AsyncSession,
    *,
    workspace_id: int | None,
    project_id: int | None,
    task: str,
    cost: Decimal | None,
    job_run_id: int | None,
    job_budget_usd: Decimal | None,
) -> None:
    """Raise if one more call costing ``cost`` would exceed a limit. Call under ``lock_workspace_budget``."""
    if job_run_id is not None and job_budget_usd is not None:
        if cost is None:
            raise PricingUnknownError(
                "The job has a cost cap but the model price is unknown; set prices in the AI model registry."
            )
        spent = await job_spent(db, job_run_id)
        if spent + cost > job_budget_usd:
            raise BudgetExceededError(
                f"Job cost cap ${job_budget_usd} reached (${spent} spent, next call ≈ ${cost}). "
                "Already processed items are kept.",
                details={"job_budget_usd": str(job_budget_usd), "spent_usd": str(spent)},
            )
    if workspace_id is None:
        return
    for budget in await applicable_budgets(db, workspace_id, project_id=project_id, task=task):
        check = check_usage(await budget_usage(db, budget), cost=cost, operations=1)
        if not check.would_exceed:
            continue
        if budget.limit_usd is not None and cost is None:
            raise PricingUnknownError(
                f"{describe_budget(budget)} has a USD limit but the model price is unknown: set prices in "
                "the AI model registry or remove the USD limit.",
                details={"budget_id": budget.id},
            )
        raise BudgetExceededError(
            f"AI {describe_budget(budget)} reached: {check.reason}. Already processed items are kept; "
            "raise the limit or wait for the next period.",
            details={"budget_id": budget.id},
        )


# ---------------------------------------------------------------------------
# Confirmation of bulk estimates (§76): the start request must echo the token of the estimate the user saw.
# ---------------------------------------------------------------------------
def confirmation_token(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


class ConfirmationRequiredError(AppError):
    status_code = 409
    code = "confirmation_required"


def require_confirmation(token: str | None, payload: dict[str, Any]) -> None:
    """Raise unless ``token`` matches the current estimate (selection/model/price changed => re-confirm)."""
    if token != confirmation_token(payload):
        raise ConfirmationRequiredError(
            "The cost estimate changed or was not confirmed; review the new estimate and confirm again.",
            details={
                "confirm_token": confirmation_token(payload),
                "generated_at": datetime.now(UTC).isoformat(),
            },
        )
