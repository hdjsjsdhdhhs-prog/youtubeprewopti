"""Schema-first AI calls (ADR-0007 §4, AI_ARCHITECTURE "Общие механизмы").

``AIRunner.run`` = route → (per candidate model) reserve → call → validate → [1 repair] → record.

* Every provider request is one ``ai_calls`` row, written in its own committed transaction (the row
  survives a rollback of the caller's work and a crashed job still shows what was spent).
* Output is validated with Pydantic; one repair request carries the validation errors. Still invalid =>
  ``invalid_output`` (raw text kept for debugging) and ``AIInvalidOutputError`` — never a result.
* ``MODEL_UNAVAILABLE`` from the provider => next model of the route (fallback without breaking the job);
  other provider errors propagate (retryable ones retry the job).
* A refusal is recorded as ``refused`` and raised as ``AIRefusedError`` (not an infrastructure failure).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError, IntegrationError, IntegrationErrorCode
from app.domains.ai import budget
from app.domains.ai.cost import estimate_call_cost, token_cost
from app.domains.ai.models import AICall, AICallStatus, AIModel
from app.domains.ai.prompts import PROMPTS_DIR, LoadedPrompt, load_prompt
from app.domains.ai.registry import TASKS, AITask, resolve_models
from app.domains.jobs.models import JobRun
from app.providers.ai import AIProvider, ImageDetail, ImageInput, StructuredRequest, StructuredResponse

Sessions = Callable[[], AbstractAsyncContextManager[AsyncSession]]
MAX_ERRORS_SHOWN = 10
MAX_RAW_CHARS = 20_000


class AINotConfiguredError(AppError):
    status_code = 409
    code = "ai_not_configured"


class AIInvalidOutputError(AppError):
    status_code = 502
    code = "ai_invalid_output"


class AIRefusedError(AppError):
    status_code = 422
    code = "ai_refused"


@dataclass(frozen=True)
class CallContext:
    workspace_id: int | None
    project_id: int | None = None
    job_run_id: int | None = None
    input_ref: dict[str, Any] | None = None  # what was analysed (ids/hashes), for traceability


@dataclass(frozen=True)
class AIResult[T: BaseModel]:
    output: T
    call_id: int
    model_key: str
    status: AICallStatus  # OK or REPAIRED
    cost_usd: Decimal | None  # sum over the attempts of this result (None = price unknown)


def _validation_errors(exc: ValidationError) -> list[dict[str, Any]]:
    return [
        {"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"], "type": e["type"]}
        for e in exc.errors()[:MAX_ERRORS_SHOWN]
    ]


def _repair_message(user: str, raw: str | None, errors: list[dict[str, Any]]) -> str:
    listed = "\n".join(f"- {e['loc'] or '(root)'}: {e['msg']}" for e in errors)
    return (
        f"{user}\n\n---\nYour previous answer did not match the required JSON schema:\n{listed}\n\n"
        f"Previous answer:\n{(raw or '')[:MAX_RAW_CHARS]}\n\n"
        "Return the corrected answer as a single JSON object that follows the schema exactly."
    )


class AIRunner:
    """One runner per job/request: caches the resolved route and loaded prompts."""

    def __init__(
        self,
        sessions: Sessions,
        provider: AIProvider,
        settings: Settings,
        *,
        prompts_root: Path = PROMPTS_DIR,
    ) -> None:
        self._sessions = sessions
        self._provider = provider
        self._settings = settings
        self._prompts_root = prompts_root
        self._routes: dict[AITask, list[AIModel]] = {}
        self._prompts: dict[tuple[str, int], LoadedPrompt] = {}
        self.spent_usd = Decimal(0)
        self.calls = 0

    async def candidates(self, task: AITask) -> list[AIModel]:
        if task not in self._routes:
            async with self._sessions() as db:
                models = await resolve_models(db, self._settings, task)
                await db.commit()
            self._routes[task] = [m for m in models if m.provider == self._provider.name]
        return self._routes[task]

    async def _prompt(self, name: str, version: int, schema: dict[str, Any]) -> LoadedPrompt:
        key = (name, version)
        if key not in self._prompts:
            async with self._sessions() as db:
                self._prompts[key] = await load_prompt(db, name, version, schema, root=self._prompts_root)
                await db.commit()
        return self._prompts[key]

    async def run[T: BaseModel](
        self,
        *,
        task: AITask,
        schema: type[T],
        prompt: tuple[str, int],
        variables: dict[str, Any],
        ctx: CallContext,
        images: Sequence[ImageInput] = (),
        detail: ImageDetail = "low",
        max_output_tokens: int | None = None,
    ) -> AIResult[T]:
        models = await self.candidates(task)
        if not models:
            raise AINotConfiguredError(
                f"No enabled AI model for task '{task.value}' (provider: "
                f"{self._settings.effective_ai_provider or 'not configured'}). Set YTL_VIBECODE_API_KEY "
                "(or YTL_OPENAI_API_KEY, or YTL_AI_PROVIDER=mock) and check the model registry.",
            )
        json_schema = schema.model_json_schema()
        loaded = await self._prompt(prompt[0], prompt[1], json_schema)
        system, user = loaded.render(variables)
        imgs = tuple(ImageInput(i.data, i.mime, detail) for i in images)

        last: IntegrationError | None = None
        for model in models:
            try:
                return await self._run_on_model(
                    task, model, schema, json_schema, loaded, system, user, imgs, detail, ctx,
                    max_output_tokens,
                )
            except IntegrationError as exc:
                if exc.code != IntegrationErrorCode.MODEL_UNAVAILABLE:
                    raise
                last = exc  # fall back to the next model of the route
        assert last is not None
        raise last

    async def _run_on_model[T: BaseModel](
        self,
        task: AITask,
        model: AIModel,
        schema: type[T],
        json_schema: dict[str, Any],
        prompt: LoadedPrompt,
        system: str,
        user: str,
        images: tuple[ImageInput, ...],
        detail: ImageDetail,
        ctx: CallContext,
        max_output_tokens: int | None,
    ) -> AIResult[T]:
        estimate = estimate_call_cost(model, TASKS[task], len(images), detail)
        total: Decimal | None = Decimal(0)
        message = user
        for attempt in (1, 2):
            call_id = await self._reserve(task, model, prompt, ctx, attempt, estimate, len(images))
            request = StructuredRequest(
                model=model.api_model_id, system=system, user=message, schema_name=schema.__name__,
                json_schema=json_schema, images=images, max_output_tokens=max_output_tokens,
            )
            started = time.monotonic()
            try:
                response = await self._provider.generate_structured(request)
            except IntegrationError as exc:
                await self._finish(call_id, ctx, model, None, started, AICallStatus.ERROR,
                                   error_code=exc.code.value, error_message=exc.human_message)
                raise
            except BaseException as exc:
                await self._finish(call_id, ctx, model, None, started, AICallStatus.ERROR,
                                   error_code="internal_error", error_message=type(exc).__name__)
                raise
            cost = token_cost(model, response.usage.input_tokens, response.usage.output_tokens)
            total = None if total is None or cost is None else total + cost

            if response.refusal is not None:
                await self._finish(call_id, ctx, model, response, started, AICallStatus.REFUSED,
                                   error_code="refused", error_message=response.refusal[:2000])
                raise AIRefusedError(f"The model refused the request: {response.refusal[:300]}")
            try:
                output = schema.model_validate_json(response.text or "")
            except ValidationError as exc:
                errors = _validation_errors(exc)
                await self._finish(call_id, ctx, model, response, started, AICallStatus.INVALID_OUTPUT,
                                   validation_errors=errors, error_code="invalid_output",
                                   error_message=response.incomplete_reason)
                if attempt == 1:
                    message = _repair_message(user, response.text, errors)
                    continue
                raise AIInvalidOutputError(
                    f"AI output for '{task.value}' stayed invalid after one repair attempt; "
                    "nothing was saved.",
                    details={"ai_call_id": call_id, "errors": errors},
                ) from exc
            status = AICallStatus.OK if attempt == 1 else AICallStatus.REPAIRED
            await self._finish(call_id, ctx, model, response, started, status,
                               output=output.model_dump(mode="json"))
            return AIResult(
                output=output, call_id=call_id, model_key=model.key, status=status, cost_usd=total
            )
        raise AssertionError("unreachable")

    async def _reserve(
        self,
        task: AITask,
        model: AIModel,
        prompt: LoadedPrompt,
        ctx: CallContext,
        attempt: int,
        estimate: Decimal | None,
        image_count: int,
    ) -> int:
        async with self._sessions() as db:
            await budget.lock_workspace_budget(db, ctx.workspace_id)
            job_budget = None
            if ctx.job_run_id is not None:
                job_budget = await db.scalar(select(JobRun.budget_usd).where(JobRun.id == ctx.job_run_id))
            await budget.assert_within_budgets(
                db, workspace_id=ctx.workspace_id, project_id=ctx.project_id, task=task.value, cost=estimate,
                job_run_id=ctx.job_run_id, job_budget_usd=job_budget,
            )
            call = AICall(
                workspace_id=ctx.workspace_id, project_id=ctx.project_id, job_run_id=ctx.job_run_id,
                task=task.value, provider=model.provider, model_key=model.key,
                api_model_id=model.api_model_id,
                prompt_template_id=prompt.template_id, attempt=attempt, status=AICallStatus.PENDING,
                input_ref=ctx.input_ref or {}, image_inputs=image_count, estimated_cost_usd=estimate,
            )
            db.add(call)
            await db.flush()
            call_id = call.id
            await db.commit()
        return call_id

    async def _finish(
        self,
        call_id: int,
        ctx: CallContext,
        model: AIModel,
        response: StructuredResponse | None,
        started: float,
        status: AICallStatus,
        *,
        output: dict[str, Any] | None = None,
        validation_errors: list[dict[str, Any]] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        values: dict[str, Any] = {
            "status": status, "duration_ms": int((time.monotonic() - started) * 1000), "output": output,
            "validation_errors": validation_errors, "error_code": error_code, "error_message": error_message,
        }
        cost: Decimal | None = None
        if response is not None:
            cost = token_cost(model, response.usage.input_tokens, response.usage.output_tokens)
            values.update(
                input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens,
                actual_cost_usd=cost,
                raw_output=None if status in (AICallStatus.OK, AICallStatus.REPAIRED)
                else (response.text or "")[:MAX_RAW_CHARS] or None,
            )
        elif status == AICallStatus.ERROR:
            values["actual_cost_usd"] = Decimal(0)  # the provider did not bill a failed request
        async with self._sessions() as db:
            await db.execute(update(AICall).where(AICall.id == call_id).values(**values))
            if ctx.job_run_id is not None and cost:
                await db.execute(
                    update(JobRun).where(JobRun.id == ctx.job_run_id)
                    .values(actual_cost_usd=JobRun.actual_cost_usd + cost)
                )
            await db.commit()
        self.calls += 1
        if cost:
            self.spent_usd += cost
