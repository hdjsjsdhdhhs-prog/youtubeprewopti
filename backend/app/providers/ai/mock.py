"""Deterministic offline AI provider (ADR-0007 §7): valid output for any JSON schema, seeded by the inputs.

The same prompt + images always produce the same answer, so repeated runs and tests are reproducible.
Everything it returns is synthetic and labelled ``provider='mock'`` in ``ai_calls`` and the UI.
"""

from __future__ import annotations

import hashlib
import json
import random
from typing import Any

from app.providers.ai.base import StructuredRequest, StructuredResponse, Usage

CHARS_PER_TOKEN = 4  # rough token estimate for the synthetic usage numbers
MOCK_IMAGE_TOKENS = 85
MAX_DEPTH = 8


class MockAIProvider:
    name = "mock"
    is_mock = True

    def __init__(self) -> None:
        self.requests: list[StructuredRequest] = []

    async def list_models(self) -> list[str]:
        return ["mock-vision-1", "mock-text-1"]

    async def generate_structured(self, request: StructuredRequest) -> StructuredResponse:
        self.requests.append(request)
        h = hashlib.sha256()
        for part in (request.model, request.schema_name, request.system, request.user):
            h.update(part.encode("utf-8"))
            h.update(b"\0")
        for img in request.images:
            h.update(hashlib.sha256(img.data).digest())
        rng = random.Random(int.from_bytes(h.digest()[:8], "big"))  # noqa: S311 (not for security)
        value = _fake(request.json_schema, request.json_schema.get("$defs", {}), rng, request.schema_name, 0)
        text = json.dumps(value, ensure_ascii=False)
        usage = Usage(
            input_tokens=(len(request.system) + len(request.user)) // CHARS_PER_TOKEN
            + MOCK_IMAGE_TOKENS * len(request.images),
            output_tokens=len(text) // CHARS_PER_TOKEN,
            image_inputs=len(request.images),
        )
        return StructuredResponse(text=text, refusal=None, usage=usage, model=request.model)


def _resolve(schema: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in schema:
        schema = defs[schema["$ref"].rsplit("/", 1)[-1]]
    return schema


def _fake(schema: dict[str, Any], defs: dict[str, Any], rng: random.Random, path: str, depth: int) -> Any:
    schema = _resolve(schema, defs)
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return rng.choice(schema["enum"])
    for key in ("anyOf", "oneOf"):
        if key in schema:
            options = [s for s in schema[key] if _resolve(s, defs).get("type") != "null"] or schema[key]
            return _fake(options[0], defs, rng, path, depth)
    if "allOf" in schema:
        return _fake(schema["allOf"][0], defs, rng, path, depth)

    kind = schema.get("type", "object")
    if isinstance(kind, list):
        kind = next((t for t in kind if t != "null"), "null")
    if kind == "object":
        if depth >= MAX_DEPTH:
            return {}
        props: dict[str, Any] = schema.get("properties", {})
        return {name: _fake(sub, defs, rng, f"{path}.{name}", depth + 1) for name, sub in props.items()}
    if kind == "array":
        lo = int(schema.get("minItems", 1 if depth < MAX_DEPTH else 0))
        hi = int(schema.get("maxItems", max(lo, 3)))
        n = rng.randint(lo, max(lo, min(hi, lo + 3)))
        return [_fake(schema.get("items", {}), defs, rng, f"{path}[{i}]", depth + 1) for i in range(n)]
    if kind in ("integer", "number"):
        lo = schema.get("minimum", schema.get("exclusiveMinimum", 0))
        hi = schema.get("maximum", schema.get("exclusiveMaximum", lo + 100))
        if kind == "integer":
            lo_i = int(lo) + (1 if "exclusiveMinimum" in schema and "minimum" not in schema else 0)
            hi_i = int(hi) - (1 if "exclusiveMaximum" in schema and "maximum" not in schema else 0)
            return rng.randint(lo_i, max(lo_i, hi_i))
        return round(rng.uniform(float(lo), float(hi)), 2)
    if kind == "boolean":
        return rng.random() < 0.5
    if kind == "null":
        return None
    # string
    text = f"[mock] {schema.get('title') or path.rsplit('.', 1)[-1]} #{rng.randint(1, 999)}"
    min_len, max_len = int(schema.get("minLength", 0)), schema.get("maxLength")
    if len(text) < min_len:
        text = text.ljust(min_len, "·")
    return text[: int(max_len)] if max_len is not None else text
