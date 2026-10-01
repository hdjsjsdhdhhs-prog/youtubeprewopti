"""AIRunner against the real database (inside the per-test transaction): registry routing, ai_calls
records, repair, fallback, refusal, budgets, job cost cap, prompt versioning."""

from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal

import factories as f
import pytest
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from app.core.errors import IntegrationError, IntegrationErrorCode
from app.domains.ai.budget import BudgetExceededError, PricingUnknownError
from app.domains.ai.models import (
    AICall,
    AICallStatus,
    AIModel,
    Budget,
    BudgetPeriod,
    BudgetScope,
    PromptTemplate,
)
from app.domains.ai.prompts import PromptError
from app.domains.ai.registry import AITask, resolve_models
from app.domains.ai.runner import (
    AIInvalidOutputError,
    AINotConfiguredError,
    AIRefusedError,
    AIRunner,
    CallContext,
)
from app.domains.jobs.models import JobRun
from app.providers.ai import ImageInput, MockAIProvider, StructuredResponse, Usage

PROMPT = ("demo_audit", 1)


class Verdict(BaseModel):
    score: int = Field(ge=1, le=10)
    summary: str = Field(max_length=80)


class Scripted:
    """Provider returning scripted texts / raising scripted errors, in order."""

    def __init__(self, *script, name: str = "mock") -> None:
        self.name, self.is_mock = name, name == "mock"
        self.script = list(script)
        self.requests = []

    async def list_models(self) -> list[str]:
        return []

    async def generate_structured(self, request):
        self.requests.append(request)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, dict):  # {"refusal": ...}
            return StructuredResponse(
                text=None, refusal=item["refusal"], usage=Usage(10, 0), model=request.model
            )
        return StructuredResponse(
            text=item, refusal=None, usage=Usage(1000, 200, len(request.images)), model=request.model
        )


@pytest.fixture
def prompts(tmp_path):
    d = tmp_path / "prompts" / PROMPT[0]
    d.mkdir(parents=True)
    (d / "v1.md").write_text("You audit $what.\n=== user ===\nTitle: ${title}\n", encoding="utf-8")
    return tmp_path / "prompts"


@pytest.fixture
def sessions(db):
    @asynccontextmanager
    async def shared():
        yield db  # commits inside the runner become SAVEPOINT releases of the test transaction

    return shared


@pytest.fixture
def mock_mode(settings, monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "mock")
    return settings


def _runner(sessions, provider, settings, prompts) -> AIRunner:
    return AIRunner(sessions, provider, settings, prompts_root=prompts)


async def _run(runner: AIRunner, ctx: CallContext, **kw):
    return await runner.run(
        task=AITask.THUMBNAIL_ANALYSIS,
        schema=Verdict,
        prompt=PROMPT,
        variables={"what": "thumbnails", "title": "T"},
        ctx=ctx,
        **kw,
    )


async def _calls(db) -> list[AICall]:
    return list(await db.scalars(select(AICall).order_by(AICall.id)))


async def test_mock_call_is_validated_recorded_and_prompt_registered(db, owner, sessions, mock_mode, prompts):
    provider = MockAIProvider()
    runner = _runner(sessions, provider, mock_mode, prompts)
    ctx = CallContext(workspace_id=owner[1].id, input_ref={"image_asset_id": 1})
    res = await _run(runner, ctx, images=[ImageInput(b"img", "image/png")], detail="high")

    assert (
        isinstance(res.output, Verdict) and res.status == AICallStatus.OK and res.model_key == "mock-vision"
    )
    sent = provider.requests[0]
    assert (sent.system, sent.user) == ("You audit thumbnails.", "Title: T")
    assert sent.images[0].detail == "high" and sent.model == "mock-vision-1"
    [call] = await _calls(db)
    assert (call.status, call.task, call.provider, call.attempt) == (
        AICallStatus.OK,
        "thumbnail_analysis",
        "mock",
        1,
    )
    assert call.output == res.output.model_dump() and call.raw_output is None
    assert call.input_ref == {"image_asset_id": 1} and call.image_inputs == 1 and call.duration_ms is not None
    assert call.input_tokens and call.output_tokens and call.actual_cost_usd == 0 == call.estimated_cost_usd
    template = await db.get(PromptTemplate, call.prompt_template_id)
    assert (template.name, template.version) == PROMPT and template.output_schema["title"] == "Verdict"


async def test_invalid_output_is_repaired_once(db, owner, sessions, mock_mode, prompts):
    provider = Scripted('{"score": 42, "summary": "x"}', '{"score": 7, "summary": "fixed"}')
    res = await _run(_runner(sessions, provider, mock_mode, prompts), CallContext(workspace_id=owner[1].id))

    assert res.status == AICallStatus.REPAIRED and res.output.score == 7
    first, second = await _calls(db)
    assert (first.status, first.attempt, second.status, second.attempt) == (
        AICallStatus.INVALID_OUTPUT,
        1,
        AICallStatus.REPAIRED,
        2,
    )
    assert first.output is None and first.raw_output == '{"score": 42, "summary": "x"}'
    assert first.validation_errors[0]["loc"] == "score"
    repair = provider.requests[1].user
    assert (
        "did not match the required JSON schema" in repair and "score" in repair and '"score": 42' in repair
    )


async def test_still_invalid_after_repair_is_not_a_result(db, owner, sessions, mock_mode, prompts):
    provider = Scripted("not json", '{"score": 0}')
    with pytest.raises(AIInvalidOutputError) as exc:
        await _run(_runner(sessions, provider, mock_mode, prompts), CallContext(workspace_id=owner[1].id))
    calls = await _calls(db)
    assert [c.status for c in calls] == [AICallStatus.INVALID_OUTPUT] * 2
    assert all(c.output is None for c in calls) and exc.value.details["ai_call_id"] == calls[1].id


async def test_refusal_is_recorded_and_raised(db, owner, sessions, mock_mode, prompts):
    with pytest.raises(AIRefusedError):
        await _run(
            _runner(sessions, Scripted({"refusal": "no"}), mock_mode, prompts),
            CallContext(workspace_id=owner[1].id),
        )
    [call] = await _calls(db)
    assert call.status == AICallStatus.REFUSED and call.error_message == "no"


async def test_unavailable_model_falls_back_to_next_in_route(
    db, owner, sessions, settings, monkeypatch, prompts
):
    monkeypatch.setattr(settings, "ai_provider", "openai")
    unavailable = IntegrationError(
        IntegrationErrorCode.MODEL_UNAVAILABLE, "no such model", retryable=False, provider="openai"
    )
    provider = Scripted(unavailable, '{"score": 5, "summary": "ok"}', name="openai")
    res = await _run(_runner(sessions, provider, settings, prompts), CallContext(workspace_id=owner[1].id))

    assert res.model_key == "vision-premium"
    assert [r.model for r in provider.requests] == ["gpt-6-sol", "gpt-6-astra"]
    failed, ok = await _calls(db)
    assert (failed.model_key, failed.status, failed.error_code) == (
        "vision-standard",
        AICallStatus.ERROR,
        "model_unavailable",
    )
    assert failed.actual_cost_usd == 0 and ok.status == AICallStatus.OK


async def test_other_provider_errors_propagate(db, owner, sessions, mock_mode, prompts):
    boom = IntegrationError(IntegrationErrorCode.RATE_LIMITED, "slow", retryable=True, provider="mock")
    with pytest.raises(IntegrationError):
        await _run(
            _runner(sessions, Scripted(boom), mock_mode, prompts), CallContext(workspace_id=owner[1].id)
        )
    [call] = await _calls(db)
    assert (call.status, call.error_code) == (AICallStatus.ERROR, "rate_limited")


async def test_not_configured(db, owner, sessions, settings, monkeypatch, prompts):
    monkeypatch.setattr(settings, "ai_provider", None)
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "openai_api_key", None)
    assert await resolve_models(db, settings, AITask.THUMBNAIL_ANALYSIS) == []
    with pytest.raises(AINotConfiguredError):
        await _run(
            _runner(sessions, MockAIProvider(), settings, prompts), CallContext(workspace_id=owner[1].id)
        )
    assert await _calls(db) == []


async def test_disabled_model_is_skipped(db, owner, sessions, mock_mode, prompts):
    await resolve_models(db, mock_mode, AITask.THUMBNAIL_ANALYSIS)  # seeds the registry
    await db.execute(update(AIModel).where(AIModel.key == "mock-vision").values(enabled=False))
    with pytest.raises(AINotConfiguredError):
        await _run(
            _runner(sessions, MockAIProvider(), mock_mode, prompts), CallContext(workspace_id=owner[1].id)
        )


async def _price_mock(db, settings, price_in: str, price_out: str) -> None:
    await resolve_models(db, settings, AITask.THUMBNAIL_ANALYSIS)
    await db.execute(
        update(AIModel)
        .where(AIModel.key == "mock-vision")
        .values(price_input_per_1m=Decimal(price_in), price_output_per_1m=Decimal(price_out))
    )


async def test_operations_budget_stops_before_the_call(db, owner, sessions, mock_mode, prompts):
    ws = owner[1].id
    db.add(Budget(workspace_id=ws, scope=BudgetScope.GLOBAL, period=BudgetPeriod.DAY, max_ai_operations=1))
    await db.flush()
    provider = MockAIProvider()
    runner = _runner(sessions, provider, mock_mode, prompts)
    await _run(runner, CallContext(workspace_id=ws))
    with pytest.raises(BudgetExceededError, match="operations limit 1"):
        await _run(runner, CallContext(workspace_id=ws))
    assert len(provider.requests) == 1 and len(await _calls(db)) == 1  # nothing reserved for the blocked call


async def test_usd_budget_counts_reserved_and_actual_cost(db, owner, sessions, mock_mode, prompts):
    ws = owner[1].id
    await _price_mock(
        db, mock_mode, "1.00", "4.00"
    )  # est. per call: (1200+85)·1 + 900·4 = 4885 µ$ ≈ $0.004885
    p = await f.project(db, ws)
    db.add(
        Budget(
            workspace_id=ws,
            scope=BudgetScope.PROJECT,
            scope_ref=str(p.id),
            period=BudgetPeriod.TOTAL,
            limit_usd=Decimal("0.0080"),
        )
    )
    await db.flush()
    ok = '{"score": 5, "summary": "ok"}'
    runner = _runner(sessions, Scripted(ok, ok), mock_mode, prompts)  # usage 1000/200 => actual $0.0018
    ctx = CallContext(workspace_id=ws, project_id=p.id)
    img = [ImageInput(b"a", "image/png")]
    await _run(runner, ctx, images=img)
    [call] = await _calls(db)
    assert (call.estimated_cost_usd, call.actual_cost_usd) == (Decimal("0.004885"), Decimal("0.001800"))
    await _run(runner, ctx, images=img)  # 0.0018 actual + 0.004885 estimate ≤ 0.008

    # an in-flight call (pending, not finished yet) counts with its estimate
    db.add(
        AICall(
            workspace_id=ws,
            project_id=p.id,
            task="thumbnail_analysis",
            provider="mock",
            model_key="mock-vision",
            api_model_id="x",
            estimated_cost_usd=Decimal("0.004"),
        )
    )
    await db.flush()
    with pytest.raises(BudgetExceededError, match="USD limit"):  # 0.0036 + 0.004 + 0.004885 > 0.008
        await _run(runner, ctx, images=img)
    # another project is not limited by this budget
    other = await f.project(db, ws)
    await _run(
        _runner(sessions, Scripted(ok), mock_mode, prompts),
        CallContext(workspace_id=ws, project_id=other.id),
        images=img,
    )


async def test_usd_limit_with_unknown_price_refuses(db, owner, sessions, settings, monkeypatch, prompts):
    monkeypatch.setattr(settings, "ai_provider", "openai")  # registry OpenAI rows have no prices
    ws = owner[1].id
    db.add(
        Budget(
            workspace_id=ws,
            scope=BudgetScope.TASK,
            scope_ref="thumbnail_analysis",
            period=BudgetPeriod.MONTH,
            limit_usd=Decimal(100),
        )
    )
    await db.flush()
    provider = Scripted('{"score": 5, "summary": "ok"}', name="openai")
    with pytest.raises(PricingUnknownError):
        await _run(_runner(sessions, provider, settings, prompts), CallContext(workspace_id=ws))
    assert provider.requests == []


async def test_without_budgets_unknown_price_is_allowed(db, owner, sessions, settings, monkeypatch, prompts):
    monkeypatch.setattr(settings, "ai_provider", "openai")
    provider = Scripted('{"score": 5, "summary": "ok"}', name="openai")
    res = await _run(_runner(sessions, provider, settings, prompts), CallContext(workspace_id=owner[1].id))
    [call] = await _calls(db)
    assert res.cost_usd is None and call.estimated_cost_usd is None and call.actual_cost_usd is None


async def test_job_cost_cap_and_job_actual_cost(db, owner, sessions, mock_mode, prompts):
    ws = owner[1].id
    await _price_mock(db, mock_mode, "1.00", "4.00")
    job = await f.job_run(db, ws, budget_usd=Decimal("0.006"))
    runner = _runner(
        sessions, Scripted('{"score": 5, "summary": "a"}', '{"score": 6, "summary": "b"}'), mock_mode, prompts
    )
    ctx = CallContext(workspace_id=ws, job_run_id=job.id)
    await _run(runner, ctx)
    await db.refresh(job)
    assert job.actual_cost_usd == Decimal("0.001800")  # 1000·1 + 200·4 µ$ (actual usage, not the estimate)
    with pytest.raises(BudgetExceededError, match="Job cost cap"):
        await _run(runner, ctx)


async def test_changed_prompt_requires_new_version(db, owner, sessions, mock_mode, prompts):
    runner = _runner(sessions, MockAIProvider(), mock_mode, prompts)
    await _run(runner, CallContext(workspace_id=owner[1].id))
    (prompts / PROMPT[0] / "v1.md").write_text("Edited $what\n=== user ===\n${title}\n", encoding="utf-8")
    with pytest.raises(PromptError) as exc:
        await _run(
            _runner(sessions, MockAIProvider(), mock_mode, prompts), CallContext(workspace_id=owner[1].id)
        )
    assert exc.value.code == "prompt_changed" and "v2.md" in exc.value.message


async def test_missing_prompt_variable_is_an_error(db, owner, sessions, mock_mode, prompts):
    runner = _runner(sessions, MockAIProvider(), mock_mode, prompts)
    with pytest.raises(PromptError, match="missing or bad variable"):
        await runner.run(
            task=AITask.THUMBNAIL_ANALYSIS,
            schema=Verdict,
            prompt=PROMPT,
            variables={"what": "x"},
            ctx=CallContext(workspace_id=owner[1].id),
        )


async def test_job_run_link(db, owner, sessions, mock_mode, prompts):
    job = await f.job_run(db, owner[1].id)
    await _run(
        _runner(sessions, MockAIProvider(), mock_mode, prompts),
        CallContext(workspace_id=owner[1].id, job_run_id=job.id),
    )
    [call] = await _calls(db)
    assert call.job_run_id == job.id
    assert (await db.get(JobRun, job.id)).actual_cost_usd == 0
