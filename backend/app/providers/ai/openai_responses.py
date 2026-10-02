"""OpenAI-compatible adapter (ADR-0007 §8). The ``openai`` SDK is imported only here.

Serves both the OpenAI API and OpenAI-compatible gateways (vibecode.moe): only ``base_url``, the key and
the provider name differ.

* Analysis: Responses API with a JSON-schema text format. ``strict`` is off on purpose: Pydantic schemas
  (defaults, optional fields) do not satisfy strict-mode restrictions, and the runner validates the output
  with Pydantic anyway (one repair attempt on failure).
* Images: ``/images/generations`` (prompt only) and ``/images/edits`` (multipart, 1–4 reference files).
  Bytes are requested inline (``response_format=b64_json``) where the provider supports it — vibecode's
  URLs expire after a few hours.

OpenAI itself: **unverified against the real API** (Q-004, Q-016). vibecode.moe: verified 2026-10-01.
"""

from __future__ import annotations

import base64
from typing import Any

import openai
from openai import AsyncOpenAI

from app.core.errors import IntegrationError, IntegrationErrorCode
from app.providers.ai.base import (
    MAX_REFERENCE_IMAGES,
    GeneratedImage,
    ImageGenRequest,
    ImageGenResponse,
    StructuredRequest,
    StructuredResponse,
    Usage,
)

PROVIDER = "openai"
# provider name -> (human label, env var with the key)
_LABELS = {"openai": ("OpenAI", "YTL_OPENAI_API_KEY"), "vibecode": ("vibecode.moe", "YTL_VIBECODE_API_KEY")}
# Error codes/messages meaning "this model can't serve the request" => fall back to the next model.
_MODEL_UNAVAILABLE_CODES = frozenset({"model_not_found", "unsupported_model", "unsupported_value"})
_MODEL_UNAVAILABLE_HINTS = ("does not support", "does not exist", "not supported", "unsupported")
_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


def _sniff_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def classify_openai_error(exc: openai.OpenAIError, model: str, provider: str = PROVIDER) -> IntegrationError:
    label, key_env = _LABELS.get(provider, (provider, "the API key"))

    def _err(
        code: IntegrationErrorCode, message: str, *, retryable: bool, status: int | None = None
    ) -> IntegrationError:
        return IntegrationError(code, message, retryable=retryable, provider=provider, http_status=status)

    if isinstance(exc, openai.APITimeoutError):
        return _err(IntegrationErrorCode.TIMEOUT, f"{label} request timed out; will retry.", retryable=True)
    if isinstance(exc, openai.APIConnectionError):
        return _err(IntegrationErrorCode.PROVIDER_UNAVAILABLE, f"Cannot reach the {label} API; will retry.",
                    retryable=True)
    if not isinstance(exc, openai.APIStatusError):
        return _err(IntegrationErrorCode.UNKNOWN, f"{label} client error: {type(exc).__name__}",
                    retryable=False)
    status, code = exc.status_code, (getattr(exc, "code", None) or "")
    message = str(getattr(exc, "message", "") or exc)
    if isinstance(exc, openai.AuthenticationError):
        return _err(IntegrationErrorCode.AUTH_EXPIRED,
                    f"{label} API key is invalid or revoked: check {key_env}.",
                    retryable=False, status=status)
    if status == 402:
        return _err(IntegrationErrorCode.QUOTA_EXCEEDED,
                    f"{label} account balance is insufficient. Top up and restart the job.",
                    retryable=False, status=status)
    if isinstance(exc, openai.PermissionDeniedError) and code == "unsupported_country_region_territory":
        return _err(IntegrationErrorCode.FORBIDDEN,
                    "OpenAI API is not available from the region this server connects from (HTTP 403 "
                    "unsupported_country_region_territory). Run the worker on a host in a supported region "
                    "or use another AI provider; the API key does not change this.",
                    retryable=False, status=status)
    if isinstance(exc, openai.PermissionDeniedError):
        return _err(IntegrationErrorCode.FORBIDDEN,
                    f"{label} denied access (model {model!r} may not be available to this account).",
                    retryable=False, status=status)
    if isinstance(exc, openai.RateLimitError):
        if code == "insufficient_quota":
            return _err(IntegrationErrorCode.QUOTA_EXCEEDED,
                        f"{label} account has no remaining credit/quota (billing). "
                        "Top up and restart the job.",
                        retryable=False, status=status)
        return _err(IntegrationErrorCode.RATE_LIMITED, f"{label} rate limit hit; will retry later.",
                    retryable=True, status=status)
    lowered = message.lower()
    if isinstance(exc, openai.NotFoundError) or code in _MODEL_UNAVAILABLE_CODES or (
        status == 400 and "model" in lowered and any(h in lowered for h in _MODEL_UNAVAILABLE_HINTS)
    ):
        return _err(IntegrationErrorCode.MODEL_UNAVAILABLE,
                    f"{label} model {model!r} is not available for this request ({message[:200]}). Check the "
                    "model registry (python -m app.cli ai-models).",
                    retryable=False, status=status)
    if status >= 500:
        return _err(IntegrationErrorCode.PROVIDER_UNAVAILABLE,
                    f"{label} is temporarily unavailable (HTTP {status}); will retry.",
                    retryable=True, status=status)
    return _err(IntegrationErrorCode.INVALID_INPUT, f"{label} rejected the request: {message[:300]}",
                retryable=False, status=status)


def _data_url(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


class OpenAIResponsesProvider:
    is_mock = False

    def __init__(
        self,
        client: AsyncOpenAI,
        *,
        name: str = PROVIDER,
        image_timeout_seconds: float | None = None,
        inline_image_bytes: bool = False,
    ) -> None:
        self._client = client
        self.name = name
        self._image_timeout = image_timeout_seconds
        # gpt-image on OpenAI always returns base64 and rejects ``response_format``; gateways default to URLs.
        self._inline_image_bytes = inline_image_bytes

    def _classify(self, exc: openai.OpenAIError, model: str) -> IntegrationError:
        return classify_openai_error(exc, model, self.name)

    async def list_models(self) -> list[str]:
        try:
            page = await self._client.models.list()
            return sorted([m.id async for m in page])
        except openai.OpenAIError as exc:
            raise self._classify(exc, "-") from exc

    async def generate_images(self, request: ImageGenRequest) -> ImageGenResponse:
        if len(request.references) > MAX_REFERENCE_IMAGES:
            raise IntegrationError(
                IntegrationErrorCode.INVALID_INPUT,
                f"At most {MAX_REFERENCE_IMAGES} reference images are supported.",
                retryable=False, provider=self.name,
            )
        params: dict[str, Any] = {"model": request.model, "prompt": request.prompt}
        if request.size is not None:
            params["size"] = request.size
        if request.quality is not None:
            params["quality"] = request.quality
        if self._inline_image_bytes:
            params["response_format"] = "b64_json"
        if self._image_timeout is not None:
            params["timeout"] = self._image_timeout
        try:
            if request.references:
                files = [
                    (f"ref{i}.{_EXT.get(r.mime, 'png')}", r.data, r.mime)
                    for i, r in enumerate(request.references)
                ]
                resp = await self._client.images.edit(image=files, **params)
            else:
                resp = await self._client.images.generate(n=request.n, **params)
        except openai.OpenAIError as exc:
            raise self._classify(exc, request.model) from exc

        images: list[GeneratedImage] = []
        for item in resp.data or []:
            if not item.b64_json:  # a temporary URL instead of bytes: not usable by a background worker
                raise IntegrationError(
                    IntegrationErrorCode.UNKNOWN,
                    f"{self.name} returned an image URL instead of inline bytes (b64_json).",
                    retryable=False, provider=self.name,
                )
            data = base64.b64decode(item.b64_json)
            images.append(GeneratedImage(data=data, mime=_sniff_mime(data)))
        if not images:
            raise IntegrationError(
                IntegrationErrorCode.UNKNOWN, f"{self.name} returned no images.", retryable=True,
                provider=self.name,
            )
        return ImageGenResponse(images=images, model=request.model)

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
            "store": False,  # nothing is kept on the provider's side
        }
        if request.max_output_tokens is not None:
            params["max_output_tokens"] = request.max_output_tokens
        try:
            resp = await self._client.responses.create(**params)
        except openai.OpenAIError as exc:
            raise self._classify(exc, request.model) from exc

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
