"""Cost estimates (AI_ARCHITECTURE §10). Prices come from the registry; None = unknown price.

Image-input token counts follow OpenAI's published scheme for earlier vision models (low detail: a fixed
85 tokens; high detail for a 1280×720 thumbnail: 85 + 6 tiles × 170). They are **unverified** for the
GPT-6 family (Q-004) and only feed pre-flight estimates — actual cost uses the usage the API reports.
"""

from __future__ import annotations

from decimal import Decimal

from app.domains.ai.models import AIModel
from app.domains.ai.registry import TaskSpec
from app.providers.ai import ImageDetail

IMAGE_INPUT_TOKENS_EST: dict[ImageDetail, int] = {"low": 85, "high": 1105, "auto": 1105}
_MILLION = Decimal(1_000_000)
_Q = Decimal("0.000001")


def pricing_known(model: AIModel) -> bool:
    return model.price_input_per_1m is not None and model.price_output_per_1m is not None


def token_cost(model: AIModel, input_tokens: int, output_tokens: int) -> Decimal | None:
    if model.price_input_per_1m is None or model.price_output_per_1m is None:
        return None
    cost = (
        Decimal(input_tokens) * model.price_input_per_1m + Decimal(output_tokens) * model.price_output_per_1m
    ) / _MILLION
    return cost.quantize(_Q)


def estimate_tokens(spec: TaskSpec, images: int, detail: ImageDetail) -> tuple[int, int]:
    return spec.est_input_tokens + images * IMAGE_INPUT_TOKENS_EST[detail], spec.est_output_tokens


def estimate_call_cost(model: AIModel, spec: TaskSpec, images: int, detail: ImageDetail) -> Decimal | None:
    tokens_in, tokens_out = estimate_tokens(spec, images, detail)
    return token_cost(model, tokens_in, tokens_out)
