"""AI providers: deterministic mock and the OpenAI Responses adapter at the HTTP level.

openai>=3 sends requests through its own ``httpx2`` stack (respx does not see them), so the SDK gets an
``httpx2.MockTransport`` and an unroutable ``.invalid`` base URL: nothing can leave the machine.
The adapter is verified against the SDK's request/response format only — not against the real API (Q-004).
"""

from __future__ import annotations

import base64
import io
import json
from collections.abc import Callable
from enum import StrEnum

import httpx2
import pytest
from openai import AsyncOpenAI
from PIL import Image
from pydantic import BaseModel, Field, SecretStr

from app.core.errors import IntegrationError, IntegrationErrorCode
from app.providers.ai import ImageGenRequest, ImageInput, MockAIProvider, StructuredRequest, open_ai_provider
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

    def make(*responses: httpx2.Response | Exception, **kwargs) -> OpenAIResponsesProvider:
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
        return OpenAIResponsesProvider(client, **kwargs)

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


PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 16


def _images_response(*payloads: bytes) -> httpx2.Response:
    data = [{"b64_json": base64.b64encode(p).decode()} for p in payloads]
    return httpx2.Response(200, json={"created": 1_789_534_663, "data": data})


async def test_vibecode_errors_name_the_gateway_and_its_key(make_provider):
    provider = make_provider(
        _error(401, None, "invalid API key"), _error(402, None, "balance"), name="vibecode"
    )
    with pytest.raises(IntegrationError) as exc:
        await provider.generate_structured(_request())
    assert (exc.value.code, exc.value.provider) == (IntegrationErrorCode.AUTH_EXPIRED, "vibecode")
    assert "YTL_VIBECODE_API_KEY" in exc.value.human_message
    with pytest.raises(IntegrationError) as exc:
        await provider.generate_structured(_request())
    assert (exc.value.code, exc.value.retryable) == (IntegrationErrorCode.QUOTA_EXCEEDED, False)


async def test_image_generation_requests_inline_bytes(make_provider):
    provider = make_provider(_images_response(PNG, JPEG), name="vibecode", inline_image_bytes=True)
    resp = await provider.generate_images(
        ImageGenRequest(model="gpt-image-2", prompt="a red fox", size="1024x1024", quality="high", n=2)
    )
    assert [(i.data, i.mime) for i in resp.images] == [(PNG, "image/png"), (JPEG, "image/jpeg")]
    sent = make_provider.seen[-1]
    assert (sent.method, str(sent.url)) == ("POST", f"{BASE}/images/generations")
    assert json.loads(sent.content) == {
        "model": "gpt-image-2", "prompt": "a red fox", "n": 2, "size": "1024x1024", "quality": "high",
        "response_format": "b64_json",
    }


async def test_openai_image_generation_omits_response_format(make_provider):
    provider = make_provider(_images_response(PNG))  # OpenAI gpt-image rejects response_format
    await provider.generate_images(ImageGenRequest(model="gpt-image-2", prompt="p"))
    assert "response_format" not in json.loads(make_provider.seen[-1].content)


async def test_image_edit_sends_reference_files_as_multipart(make_provider):
    provider = make_provider(_images_response(PNG), name="vibecode", inline_image_bytes=True)
    refs = [ImageInput(PNG, "image/png"), ImageInput(JPEG, "image/jpeg")]
    resp = await provider.generate_images(
        ImageGenRequest(model="gpt-image-2.5", prompt="put a red scarf on the fox", references=refs)
    )
    assert len(resp.images) == 1
    sent = make_provider.seen[-1]
    assert (sent.method, str(sent.url)) == ("POST", f"{BASE}/images/edits")
    assert sent.headers["content-type"].startswith("multipart/form-data")
    body = sent.content
    assert b"gpt-image-2.5" in body and b"put a red scarf on the fox" in body and b"b64_json" in body
    assert PNG in body and JPEG in body
    assert b'filename="ref0.png"' in body and b'filename="ref1.jpg"' in body


async def test_image_url_only_response_and_too_many_references(make_provider):
    provider = make_provider(
        httpx2.Response(200, json={"created": 1, "data": [{"url": "https://vibecode.invalid/f.png"}]}),
        name="vibecode", inline_image_bytes=True,
    )
    with pytest.raises(IntegrationError, match="URL instead of inline bytes"):
        await provider.generate_images(ImageGenRequest(model="gpt-image-2", prompt="p"))
    with pytest.raises(IntegrationError) as exc:
        await provider.generate_images(
            ImageGenRequest(model="gpt-image-2", prompt="p", references=[ImageInput(PNG, "image/png")] * 5)
        )
    assert exc.value.code == IntegrationErrorCode.INVALID_INPUT
    assert len(make_provider.seen) == 1  # the oversized edit never left the process


async def test_mock_image_generation_is_deterministic_png():
    p = MockAIProvider()
    req = ImageGenRequest(model="mock-image-1", prompt="fox", size="1536x1024", n=2)
    a, b = await p.generate_images(req), await p.generate_images(req)
    assert [i.data for i in a.images] == [i.data for i in b.images] and len(a.images) == 2
    img = Image.open(io.BytesIO(a.images[0].data))
    assert (img.format, img.size, a.images[0].mime) == ("PNG", (256, 171), "image/png")
    edit = await p.generate_images(
        ImageGenRequest(model="mock-image-1", prompt="fox", n=3, references=[ImageInput(PNG, "image/png")])
    )
    assert len(edit.images) == 1  # edits return one image


@pytest.mark.parametrize(
    ("explicit", "demo", "vibecode_key", "openai_key", "expected"),
    [
        (None, False, None, None, None),
        (None, False, "vk-x", None, "vibecode"),
        (None, False, None, "sk-x", "openai"),
        (None, False, "vk-x", "sk-x", "vibecode"),
        (None, True, "vk-x", None, "mock"),
        ("openai", False, "vk-x", "sk-x", "openai"),
    ],
)
def test_effective_ai_provider(settings, explicit, demo, vibecode_key, openai_key, expected):
    s = settings.model_copy(
        update={
            "ai_provider": explicit, "demo_mode": demo,
            "vibecode_api_key": SecretStr(vibecode_key) if vibecode_key else None,
            "openai_api_key": SecretStr(openai_key) if openai_key else None,
        }
    )
    assert s.effective_ai_provider == expected


async def test_vibecode_factory_uses_gateway_base_url(settings):
    s = settings.model_copy(
        update={"ai_provider": "vibecode", "vibecode_api_key": SecretStr("vk-test"),
                "vibecode_base_url": "https://vibecode.invalid/v1"}
    )
    async with open_ai_provider(s) as provider:
        assert provider.name == "vibecode" and not provider.is_mock
        client = provider._client  # type: ignore[attr-defined]
        assert str(client.base_url).rstrip("/") == "https://vibecode.invalid/v1"
        assert client.api_key == "vk-test"
    missing = settings.model_copy(update={"ai_provider": "vibecode", "vibecode_api_key": None})
    with pytest.raises(IntegrationError) as exc:
        async with open_ai_provider(missing):
            pass
    assert exc.value.code == IntegrationErrorCode.NOT_CONFIGURED


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
