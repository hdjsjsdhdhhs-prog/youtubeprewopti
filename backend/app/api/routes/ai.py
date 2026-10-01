from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import AppSettings, DbSession, Permission, ReadAuth, require
from app.domains.ai import service
from app.domains.ai.schemas import (
    AIModelOut,
    AIModelUpdate,
    AIStatusOut,
    AIUsageOut,
    BudgetIn,
    BudgetOut,
    BudgetUpdate,
)
from app.domains.identity.service import AuthContext

router = APIRouter(tags=["ai"])
AdminAuth = Annotated[AuthContext, Depends(require(Permission.MANAGE_INTEGRATIONS))]


@router.get("/ai/status", response_model=AIStatusOut)
async def ai_status(ctx: ReadAuth, db: DbSession, settings: AppSettings) -> AIStatusOut:
    """Active AI provider and the model registry (API IDs, capabilities, prices, verification date)."""
    return await service.ai_status(db, settings)


@router.patch("/ai/models/{key}", response_model=AIModelOut)
async def update_ai_model(
    key: str, body: AIModelUpdate, ctx: AdminAuth, db: DbSession, settings: AppSettings
) -> AIModelOut:
    """Set the provider's API model ID, prices (USD per 1M tokens) or enable/disable a model."""
    return await service.update_model(db, settings, ctx.workspace.id, ctx.user.id, key, body)


@router.get("/ai/usage", response_model=AIUsageOut)
async def ai_usage(ctx: ReadAuth, db: DbSession) -> AIUsageOut:
    """Workspace AI spend (UTC day / month / total) and budgets with their current usage."""
    return await service.usage_summary(db, ctx.workspace.id)


@router.get("/budgets", response_model=list[BudgetOut])
async def list_budgets(ctx: ReadAuth, db: DbSession) -> list[BudgetOut]:
    """Spending limits; an empty list means no limit (bulk runs still require confirmation)."""
    return await service.list_budgets(db, ctx.workspace.id)


@router.post("/budgets", response_model=BudgetOut, status_code=status.HTTP_201_CREATED)
async def create_budget(body: BudgetIn, ctx: AdminAuth, db: DbSession) -> BudgetOut:
    return await service.create_budget(db, ctx.workspace.id, ctx.user.id, body)


@router.patch("/budgets/{budget_id}", response_model=BudgetOut)
async def update_budget(budget_id: int, body: BudgetUpdate, ctx: AdminAuth, db: DbSession) -> BudgetOut:
    return await service.update_budget(db, ctx.workspace.id, ctx.user.id, budget_id, body)


@router.delete("/budgets/{budget_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_budget(budget_id: int, ctx: AdminAuth, db: DbSession) -> Response:
    await service.delete_budget(db, ctx.workspace.id, ctx.user.id, budget_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
