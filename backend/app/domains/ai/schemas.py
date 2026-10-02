from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.domains.ai.models import BudgetPeriod, BudgetScope


class AIModelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    provider: str
    api_model_id: str
    capabilities: list[str]
    price_input_per_1m: Decimal | None
    price_output_per_1m: Decimal | None
    price_per_image: dict[str, str]  # image models: {"default": "<usd>"} (flat) or {"<quality>@<size>": …}
    pricing_verified_at: datetime | None
    enabled: bool
    notes: str


ModelId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
ScopeRef = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Price = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=6)]


class AIModelUpdate(BaseModel):
    """Prices are USD per 1M tokens as published by the provider. Setting both prices marks them verified
    (by you) with the current time; send ``null`` to clear a price (estimates become unavailable)."""

    model_config = ConfigDict(extra="forbid")
    api_model_id: ModelId | None = None
    price_input_per_1m: Price | None = None
    price_output_per_1m: Price | None = None
    enabled: bool | None = None
    notes: Annotated[str, StringConstraints(max_length=2000)] | None = None


class AIStatusOut(BaseModel):
    provider: str | None  # active provider: "vibecode" | "openai" | "mock" | None (not configured)
    configured: bool
    models: list[AIModelOut]


class BudgetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: BudgetScope
    scope_ref: ScopeRef | None = None
    period: BudgetPeriod
    limit_usd: Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=4)] | None = None
    max_ai_operations: int | None = Field(default=None, ge=0)
    is_active: bool = True

    @model_validator(mode="after")
    def _consistent(self) -> BudgetIn:
        if self.limit_usd is None and self.max_ai_operations is None:
            raise ValueError("set limit_usd and/or max_ai_operations")
        if (self.scope == BudgetScope.GLOBAL) != (self.scope_ref is None):
            raise ValueError("scope_ref is required for project/task budgets and empty for global ones")
        return self


class BudgetUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit_usd: Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=4)] | None = None
    max_ai_operations: int | None = Field(default=None, ge=0)
    is_active: bool | None = None


class BudgetOut(BaseModel):
    id: int
    scope: BudgetScope
    scope_ref: str | None
    period: BudgetPeriod
    limit_usd: Decimal | None
    max_ai_operations: int | None
    is_active: bool
    spent_usd: Decimal  # in the current period (in-flight calls counted with their estimate)
    operations: int
    remaining_usd: Decimal | None
    remaining_operations: int | None
    created_at: datetime


class SpendOut(BaseModel):
    period: BudgetPeriod
    spent_usd: Decimal
    operations: int
    unpriced_operations: int  # calls whose cost is unknown (no registry price) — not in spent_usd


class AIUsageOut(BaseModel):
    spend: list[SpendOut]  # day / month / total for the workspace
    budgets: list[BudgetOut]  # empty => no limits
