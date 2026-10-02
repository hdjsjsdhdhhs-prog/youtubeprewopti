"""Workspace-facing AI administration: model registry view/edit, budgets, spend summary."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError
from app.domains.ai import budget as budget_mod
from app.domains.ai.models import AICall, AIModel, Budget, BudgetPeriod, BudgetScope
from app.domains.ai.registry import AITask, sync_registry
from app.domains.ai.schemas import (
    AIModelOut,
    AIModelUpdate,
    AIStatusOut,
    AIUsageOut,
    BudgetIn,
    BudgetOut,
    BudgetUpdate,
    SpendOut,
)
from app.domains.identity.service import audit
from app.domains.projects.service import get_project


async def ai_status(db: AsyncSession, settings: Settings) -> AIStatusOut:
    await sync_registry(db, settings)
    models = await db.scalars(select(AIModel).order_by(AIModel.provider, AIModel.key))
    provider = settings.effective_ai_provider
    return AIStatusOut(
        provider=provider,
        configured=provider == "mock"
        or (provider == "openai" and settings.openai_api_key is not None)
        or (provider == "vibecode" and settings.vibecode_api_key is not None),
        models=[AIModelOut.model_validate(m) for m in models],
    )


async def update_model(
    db: AsyncSession, settings: Settings, workspace_id: int, actor_id: int, key: str, data: AIModelUpdate
) -> AIModelOut:
    """The registry is global (one instance, ADR-0001); edits are audited in the editor's workspace."""
    await sync_registry(db, settings)
    model = await db.scalar(select(AIModel).where(AIModel.key == key).with_for_update())
    if model is None:
        raise NotFoundError("AI model not found")
    changes = data.model_dump(exclude_unset=True)
    for field in ("api_model_id", "enabled", "notes"):
        if field in changes and changes[field] is None:
            changes.pop(field)
    before = {k: str(getattr(model, k)) for k in changes}
    for k, v in changes.items():
        setattr(model, k, v)
    if {"price_input_per_1m", "price_output_per_1m"} & changes.keys():
        model.pricing_verified_at = (
            datetime.now(UTC)
            if model.price_input_per_1m is not None and model.price_output_per_1m is not None else None
        )
    await db.flush()
    if changes:
        await audit(
            db, "ai_model.updated", workspace_id=workspace_id, actor_user_id=actor_id, entity_type="ai_model",
            entity_id=model.id, diff={"before": before, "after": {k: str(v) for k, v in changes.items()}},
        )
    return AIModelOut.model_validate(model)


def _budget_out(usage: budget_mod.BudgetUsage) -> BudgetOut:
    b = usage.budget
    return BudgetOut(
        id=b.id, scope=b.scope, scope_ref=b.scope_ref, period=b.period, limit_usd=b.limit_usd,
        max_ai_operations=b.max_ai_operations, is_active=b.is_active, spent_usd=usage.spent_usd,
        operations=usage.operations, remaining_usd=usage.remaining_usd,
        remaining_operations=usage.remaining_operations, created_at=b.created_at,
    )


async def list_budgets(db: AsyncSession, workspace_id: int) -> list[BudgetOut]:
    budgets = await db.scalars(select(Budget).where(Budget.workspace_id == workspace_id).order_by(Budget.id))
    return [_budget_out(await budget_mod.budget_usage(db, b)) for b in budgets]


async def _get_budget(db: AsyncSession, workspace_id: int, budget_id: int) -> Budget:
    b = await db.scalar(select(Budget).where(Budget.id == budget_id, Budget.workspace_id == workspace_id))
    if b is None:
        raise NotFoundError("Budget not found")
    return b


async def create_budget(db: AsyncSession, workspace_id: int, actor_id: int, data: BudgetIn) -> BudgetOut:
    if data.scope == BudgetScope.PROJECT:
        if data.scope_ref is None or not data.scope_ref.isdigit():
            raise ConflictError(
                "scope_ref of a project budget must be the project id", code="invalid_scope_ref"
            )
        await get_project(db, workspace_id, int(data.scope_ref))
    elif data.scope == BudgetScope.TASK and data.scope_ref not in {t.value for t in AITask}:
        raise ConflictError(
            f"Unknown AI task {data.scope_ref!r}; one of: {', '.join(t.value for t in AITask)}",
            code="invalid_scope_ref",
        )
    exists = await db.scalar(
        select(Budget.id).where(
            Budget.workspace_id == workspace_id, Budget.scope == data.scope, Budget.period == data.period,
            Budget.scope_ref.is_(None) if data.scope_ref is None else Budget.scope_ref == data.scope_ref,
        )
    )
    if exists is not None:
        raise ConflictError(
            "A budget for this scope and period already exists; edit it", code="budget_exists"
        )
    b = Budget(workspace_id=workspace_id, **data.model_dump())
    db.add(b)
    await db.flush()
    await audit(db, "budget.created", workspace_id=workspace_id, actor_user_id=actor_id, entity_type="budget",
                entity_id=b.id, diff={"after": data.model_dump(mode="json")})
    await db.refresh(b)
    return _budget_out(await budget_mod.budget_usage(db, b))


async def update_budget(
    db: AsyncSession, workspace_id: int, actor_id: int, budget_id: int, data: BudgetUpdate
) -> BudgetOut:
    b = await _get_budget(db, workspace_id, budget_id)
    changes = data.model_dump(exclude_unset=True)
    if changes.get("is_active", False) is None:
        changes.pop("is_active")
    limit_usd = changes.get("limit_usd", b.limit_usd)
    max_ops = changes.get("max_ai_operations", b.max_ai_operations)
    if limit_usd is None and max_ops is None:
        raise ConflictError("A budget needs limit_usd and/or max_ai_operations; delete it instead",
                            code="budget_without_limit")
    before = {k: str(getattr(b, k)) for k in changes}
    for k, v in changes.items():
        setattr(b, k, v)
    await db.flush()
    await audit(db, "budget.updated", workspace_id=workspace_id, actor_user_id=actor_id, entity_type="budget",
                entity_id=b.id, diff={"before": before, "after": {k: str(v) for k, v in changes.items()}})
    return _budget_out(await budget_mod.budget_usage(db, b))


async def delete_budget(db: AsyncSession, workspace_id: int, actor_id: int, budget_id: int) -> None:
    b = await _get_budget(db, workspace_id, budget_id)
    await audit(db, "budget.deleted", workspace_id=workspace_id, actor_user_id=actor_id, entity_type="budget",
                entity_id=b.id,
                diff={"scope": b.scope.value, "scope_ref": b.scope_ref, "period": b.period.value})
    await db.delete(b)
    await db.flush()


async def usage_summary(db: AsyncSession, workspace_id: int) -> AIUsageOut:
    spend: list[SpendOut] = []
    for period in BudgetPeriod:
        probe = Budget(workspace_id=workspace_id, scope=BudgetScope.GLOBAL, scope_ref=None, period=period)
        cond = budget_mod.scope_condition(probe)
        row = (
            await db.execute(
                select(
                    budget_mod.SPENT, func.count(AICall.id),
                    func.count(AICall.id).filter(
                        and_(AICall.actual_cost_usd.is_(None), AICall.estimated_cost_usd.is_(None))
                    ),
                ).where(cond)
            )
        ).one()
        spend.append(
            SpendOut(period=period, spent_usd=Decimal(row[0]), operations=row[1], unpriced_operations=row[2])
        )
    return AIUsageOut(spend=spend, budgets=await list_budgets(db, workspace_id))
