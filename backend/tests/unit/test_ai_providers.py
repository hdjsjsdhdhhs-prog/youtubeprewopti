"""AI providers: deterministic mock and the OpenAI Responses adapter at the HTTP level.

openai>=3 sends requests through its own ``httpx2`` stack (respx does not see them), so the SDK gets an
``httpx2.MockTransport`` and an unroutable ``.invalid`` base URL: nothing can leave the machine.
The adapter is verified against the SDK's request/response format only — not against the real API (Q-004).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from enum import StrEnum

import httpx2
import pytest
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from app.core.errors import IntegrationError, IntegrationErrorCode
from app.providers.ai import ImageInput, MockAIProvider, StructuredRequest
from app.providers.ai.openai_responses import OpenAIResponsesProvider

BASE = "https://openai.invalid/v1"


class Severity(StrEnum):
    LOW = "low"
    HIGH = "high"


class Problem(BaseModel):
    code: str = Field(max_length=40)
    severity: Severity


class Audit(BaseModel):
    composition: int = Field(ge=1, le=10)
    contrast: float = Field(ge=1, le=10)
    has_face: bool
    summary: str = Field(max_length=60)
    problems: list[Problem] = Field(min_length=1, max_length=4)
    focal_point: str | None = None


def _request(user: str = "analyse", images=()) -> StructuredRequest:
    return StructuredRequest(
        model="m-1",
        system="sys",
        user=user,
        schema_name="Audit",
        json_schema=Audit.model_json_schema(),
        images=images,
    )


async def test_mock_returns_valid_deterministic_output_for_any_schema():
    p = MockAIProvider()
    img = ImageInput(b"\x89PNG fake", "image/png")
    a, b = (
        await p.generate_structured(_request(images=[img])),
        await p.generate_structured(_request(images=[img])),
    )
    assert a.text == b.text and a.refusal is None
    audit = Audit.model_validate_json(a.text)  # nested model, enum, ranges, list bounds, optional
    assert 1 <= audit.composition <= 10 and 1 <= len(audit.problems) <= 4
    assert a.usage.image_inputs == 1 and a.usage.input_tokens > 0 and a.usage.output_tokens > 0
    other = await p.generate_structured(_request(images=[ImageInput(b"other", "image/png")]))
    assert other.text != a.text  # seeded by the inputs
    assert len(p.requests) == 3


def _response(text: str | None = None, *, refusal: str | None = None, status: str = "completed") -> dict:
    content = (
        [{"type": "refusal", "refusal": refusal}]
        if refusal
        else [{"type": "output_text", "text": text, "annotations": []}]
    )
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 1_790_000_000,
        "model": "gpt-6-sol-2026",
        "status": status,
        "output": [
            {"type": "message", "id": "msg_1", "status": "completed", "role": "assistant", "content": content}
        ],
        "incomplete_details": {"reason": "max_output_tokens"} if status == "incomplete" else None,
        "usage": {
            "input_tokens": 1234,
            "output_tokens": 210,
            "total_tokens": 1444,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
    }


Handler = Callable[[httpx2.Request], httpx2.Response]


@pytest.fixture
async def make_provider():
    clients: list[AsyncOpenAI] = []
    seen: list[httpx2.Request] = []

    def make(*responses: httpx2.Response | Exception) -> OpenAIResponsesProvider:
        queue = list(responses)

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen.append(request)
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        client = AsyncOpenAI(
            api_key="sk-test",
            base_url=BASE,
            max_retries=0,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        )
        clients.append(client)
        return OpenAIResponsesProvider(client)

    make.seen = seen  # type: ignore[attr-defined]
    yield make
    for c in clients:
        await c.close()


async def test_openai_request_format_and_parsing(make_provider):
    provider = make_provider(httpx2.Response(200, json=_response('{"ok": 1}')))
    img = ImageInput(b"\xff\xd8jpeg", "image/jpeg", detail="low")
    resp = await provider.generate_structured(
        StructuredRequest(
            model="gpt-6-sol",
            system="SYS",
            user="USER",
            schema_name="Audit",
            json_schema={"type": "object"},
            images=[img],
            max_output_tokens=900,
        )
    )
    assert (resp.text, resp.refusal, resp.model, resp.response_id) == (
        '{"ok": 1}',
        None,
        "gpt-6-sol-2026",
        "resp_1",
    )
    assert (resp.usage.input_tokens, resp.usage.output_tokens, resp.usage.image_inputs) == (1234, 210, 1)

    sent = make_provider.seen[-1]
    assert (sent.method, str(sent.url)) == ("POST", f"{BASE}/responses")
    assert sent.headers["authorization"] == "Bearer sk-test"
    body = json.loads(sent.content)
    assert body["model"] == "gpt-6-sol" and body["instructions"] == "SYS" and body["store"] is False
    assert body["max_output_tokens"] == 900
    assert body["text"]["format"] == {
        "type": "json_schema",
        "name": "Audit",
        "schema": {"type": "object"},
        "strict": False,
    }
    text_part, image_part = body["input"][0]["content"]
    assert text_part == {"type": "input_text", "text": "USER"}
    assert image_part["type"] == "input_image" and image_part["detail"] == "low"
    assert image_part["image_url"].startswith("data:image/jpeg;base64,")


async def test_openai_refusal_and_truncation(make_provider):
    provider = make_provider(
        httpx2.Response(200, json=_response(refusal="I can't help with that")),
        httpx2.Response(200, json=_response('{"ok": ', status="incomplete")),
    )
    refused = await provider.generate_structured(_request())
    assert refused.refusal == "I can't help with that" and refused.text is None
    cut = await provider.generate_structured(_request())
    assert cut.incomplete_reason == "max_output_tokens" and cut.text == '{"ok": '


def _error(status: int, code: str | None, message: str = "boom") -> httpx2.Response:
    return httpx2.Response(
        status, json={"error": {"message": message, "type": "x", "code": code, "param": None}}
    )


@pytest.mark.parametrize(
    ("status", "err_code", "message", "code", "retryable"),
    [
        (401, "invalid_api_key", "bad key", IntegrationErrorCode.AUTH_EXPIRED, False),
        (
            403,
            "unsupported_country_region_territory",
            "Country, region, or territory not supported",
            IntegrationErrorCode.FORBIDDEN,
            False,
        ),
        (
            404,
            "model_not_found",
            "The model `gpt-6-sol` does not exist",
            IntegrationErrorCode.MODEL_UNAVAILABLE,
            False,
        ),
        (
            400,
            "invalid_value",
            "This model does not support image inputs.",
            IntegrationErrorCode.MODEL_UNAVAILABLE,
            False,
        ),
        (
            400,
            "invalid_value",
            "Invalid schema for response_format",
            IntegrationErrorCode.INVALID_INPUT,
            False,
        ),
        (429, "rate_limit_exceeded", "slow down", IntegrationErrorCode.RATE_LIMITED, True),
        (429, "insufficient_quota", "no credit", IntegrationErrorCode.QUOTA_EXCEEDED, False),
        (503, None, "down", IntegrationErrorCode.PROVIDER_UNAVAILABLE, True),
    ],
)
async def test_openai_errors_are_classified(make_provider, status, err_code, message, code, retryable):
    provider = make_provider(_error(status, err_code, message))
    with pytest.raises(IntegrationError) as exc:
        await provider.generate_structured(_request())
    assert (exc.value.code, exc.value.retryable, exc.value.provider) == (code, retryable, "openai")
    assert exc.value.http_status == status


async def test_region_block_is_explained(make_provider):
    provider = make_provider(
        _error(403, "unsupported_country_region_territory", "Country, region, or territory not supported")
    )
    with pytest.raises(IntegrationError) as exc:
        await provider.generate_structured(_request())
    assert "region" in exc.value.human_message.lower()


async def test_openai_network_errors_are_retryable(make_provider):
    provider = make_provider(httpx2.ConnectTimeout("t"), httpx2.ConnectError("c"))
    for expected in (IntegrationErrorCode.TIMEOUT, IntegrationErrorCode.PROVIDER_UNAVAILABLE):
        with pytest.raises(IntegrationError) as exc:
            await provider.generate_structured(_request())
        assert exc.value.code == expected and exc.value.retryable


async def test_openai_list_models(make_provider):
    provider = make_provider(
        httpx2.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"id": "gpt-6-sol", "object": "model", "created": 1, "owned_by": "openai"},
                    {"id": "gpt-6-astra", "object": "model", "created": 1, "owned_by": "openai"},
                ],
            },
        )
    )
    assert await provider.list_models() == ["gpt-6-astra", "gpt-6-sol"]
