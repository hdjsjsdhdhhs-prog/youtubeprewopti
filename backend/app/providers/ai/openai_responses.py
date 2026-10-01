"""OpenAI adapter (ADR-0007 §8). The ``openai`` SDK is imported only here.

Uses the Responses API with a JSON-schema text format. ``strict`` is off on purpose: Pydantic schemas
(defaults, optional fields) do not satisfy strict-mode restrictions, and the runner validates the output
with Pydantic anyway (one repair attempt on failure).

**Unverified against the real API** until a key is configured (Q-004); covered by HTTP-level tests.
"""

from __future__ import annotations

import base64
from typing import Any

import openai
from openai import AsyncOpenAI

from app.core.errors import IntegrationError, IntegrationErrorCode
from app.providers.ai.base import StructuredRequest, StructuredResponse, Usage

PROVIDER = "openai"
# Error codes/messages meaning "this model can't serve the request" => fall back to the next model.
_MODEL_UNAVAILABLE_CODES = frozenset({"model_not_found", "unsupported_model", "unsupported_value"})
_MODEL_UNAVAILABLE_HINTS = ("does not support", "does not exist", "not supported", "unsupported")


def _err(
    code: IntegrationErrorCode, message: str, *, retryable: bool, status: int | None = None
) -> IntegrationError:
    return IntegrationError(code, message, retryable=retryable, provider=PROVIDER, http_status=status)


def classify_openai_error(exc: openai.OpenAIError, model: str) -> IntegrationError:
    if isinstance(exc, openai.APITimeoutError):
        return _err(IntegrationErrorCode.TIMEOUT, "OpenAI request timed out; will retry.", retryable=True)
    if isinstance(exc, openai.APIConnectionError):
        return _err(IntegrationErrorCode.PROVIDER_UNAVAILABLE, "Cannot reach the OpenAI API; will retry.",
                    retryable=True)
    if not isinstance(exc, openai.APIStatusError):
        return _err(IntegrationErrorCode.UNKNOWN, f"OpenAI client error: {type(exc).__name__}",
                    retryable=False)
    status, code = exc.status_code, (getattr(exc, "code", None) or "")
    message = str(getattr(exc, "message", "") or exc)
    if isinstance(exc, openai.AuthenticationError):
        return _err(IntegrationErrorCode.AUTH_EXPIRED,
                    "OpenAI API key is invalid or revoked: check YTL_OPENAI_API_KEY.",
                    retryable=False, status=status)
    if isinstance(exc, openai.PermissionDeniedError) and code == "unsupported_country_region_territory":
        return _err(IntegrationErrorCode.FORBIDDEN,
                    "OpenAI API is not available from the region this server connects from (HTTP 403 "
                    "unsupported_country_region_territory). Run the worker on a host in a supported region "
                    "or use another AI provider; the API key does not change this.",
                    retryable=False, status=status)
    if isinstance(exc, openai.PermissionDeniedError):
        return _err(IntegrationErrorCode.FORBIDDEN,
                    f"OpenAI denied access (model {model!r} may not be available to this project).",
                    retryable=False, status=status)
    if isinstance(exc, openai.RateLimitError):
        if code == "insufficient_quota":
            return _err(IntegrationErrorCode.QUOTA_EXCEEDED,
                        "OpenAI account has no remaining credit/quota (billing). Top up and restart the job.",
                        retryable=False, status=status)
        return _err(IntegrationErrorCode.RATE_LIMITED, "OpenAI rate limit hit; will retry later.",
                    retryable=True, status=status)
    lowered = message.lower()
    if isinstance(exc, openai.NotFoundError) or code in _MODEL_UNAVAILABLE_CODES or (
        status == 400 and "model" in lowered and any(h in lowered for h in _MODEL_UNAVAILABLE_HINTS)
    ):
        return _err(IntegrationErrorCode.MODEL_UNAVAILABLE,
                    f"OpenAI model {model!r} is not available for this request ({message[:200]}). Check the "
                    "model registry / YTL_OPENAI_VISION_MODEL (python -m app.cli ai-models).",
                    retryable=False, status=status)
    if status >= 500:
        return _err(IntegrationErrorCode.PROVIDER_UNAVAILABLE,
                    f"OpenAI is temporarily unavailable (HTTP {status}); will retry.",
                    retryable=True, status=status)
    return _err(IntegrationErrorCode.INVALID_INPUT, f"OpenAI rejected the request: {message[:300]}",
                retryable=False, status=status)


def _data_url(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


class OpenAIResponsesProvider:
    name = PROVIDER
    is_mock = False

    def __init__(self, client: AsyncOpenAI) -> None:
        self._client = client

    async def list_models(self) -> list[str]:
        try:
            page = await self._client.models.list()
            return sorted([m.id async for m in page])
        except openai.OpenAIError as exc:
            raise classify_openai_error(exc, "-") from exc

    async def generate_structured(self, request: StructuredRequest) -> StructuredResponse:
        content: list[dict[str, Any]] = [{"type": "input_text", "text": request.user}]
        content += [
            {"type": "input_image", "image_url": _data_url(i.data, i.mime), "detail": i.detail}
            for i in request.images
        ]
        params: dict[str, Any] = {
            "model": request.model,
            "instructions": request.system,
            "input": [{"role": "user", "content": content}],
            "text": {"format": {"type": "json_schema", "name": request.schema_name[:64],
                                "schema": request.json_schema, "strict": False}},
            "store": False,  # nothing is kept on OpenAI's side
        }
        if request.max_output_tokens is not None:
            params["max_output_tokens"] = request.max_output_tokens
        try:
            resp = await self._client.responses.create(**params)
        except openai.OpenAIError as exc:
            raise classify_openai_error(exc, request.model) from exc

        refusal = None
        for item in resp.output or []:
            for part in getattr(item, "content", None) or []:
                if getattr(part, "type", None) == "refusal":
                    refusal = getattr(part, "refusal", None) or "refused"
        usage = resp.usage
        return StructuredResponse(
            text=resp.output_text or None,
            refusal=refusal,
            usage=Usage(
                input_tokens=usage.input_tokens if usage else 0,
                output_tokens=usage.output_tokens if usage else 0,
                image_inputs=len(request.images),
            ),
            model=resp.model,
            response_id=resp.id,
            incomplete_reason=(resp.incomplete_details.reason if resp.incomplete_details else None),
        )
