"""ThumbnailAudit schema, deterministic overall score, weights validation, vision image preparation."""

from __future__ import annotations

import io
from decimal import Decimal

import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError

from app.domains.analysis.audit import DEFAULT_WEIGHTS, SUBSCORES, ThumbnailAudit, overall_score
from app.domains.analysis.pipeline import audit_values, metrics_text
from app.domains.analysis.schemas import ThumbnailWeights
from app.domains.media.imaging import prepare_for_vision
from app.providers.ai import MockAIProvider, StructuredRequest


def _scores(**over: float) -> dict[str, float]:
    return {k: 5.0 for k in SUBSCORES} | over


def test_overall_is_weighted_mean_and_ignores_redesign_potential_by_default():
    assert DEFAULT_WEIGHTS["redesign_potential"] == 0
    assert overall_score(_scores(), DEFAULT_WEIGHTS) == Decimal("5.00")
    # redesign_potential is an opportunity signal, not quality: it does not move the default score
    assert overall_score(_scores(redesign_potential=10), DEFAULT_WEIGHTS) == Decimal("5.00")
    # one of ten counted subscores at 10 => (9*5 + 10) / 10
    assert overall_score(_scores(contrast_score=10), DEFAULT_WEIGHTS) == Decimal("5.50")
    weights = {k: 0.0 for k in SUBSCORES} | {"text_score": 3.0, "subject_score": 1.0}
    assert overall_score(_scores(text_score=2, subject_score=10), weights) == Decimal("4.00")
    with pytest.raises(ValueError):
        overall_score(_scores(), {k: 0.0 for k in SUBSCORES})


def test_weights_validation():
    assert ThumbnailWeights().model_dump() == DEFAULT_WEIGHTS
    assert ThumbnailWeights(text_score=2).text_score == 2
    for bad in ({"text_score": -1}, {"text_score": 11}, {"unknown": 1}, {k: 0 for k in SUBSCORES}):
        with pytest.raises(ValidationError):
            ThumbnailWeights(**bad)


def _audit(**over) -> dict:
    return {k: 6.5 for k in SUBSCORES} | {
        "has_face": True, "face_emotion": "удивление", "text_present": True, "text_word_count_est": 4,
        "focal_point": "лицо слева", "background_separation": "medium", "clutter_level": "low",
        "problems": [
            {"code": "small_text", "severity": "high", "description": "мелкий текст"},
            {"code": "low_contrast", "severity": "medium", "description": "текст сливается"},
            {"code": "small_text", "severity": "low", "description": "подпись"},
        ],
        "recommendations": [{"code": "small_text", "action": "крупнее", "rationale": "читаемость"}],
        "visual_summary": "Хорошая идея, слабый текст.", "improvement_opportunity": "medium",
    } | over


def test_audit_schema_bounds_and_vocabulary():
    ThumbnailAudit.model_validate(_audit())
    for bad in (
        {"text_score": 0.5}, {"text_score": 10.5}, {"improvement_opportunity": "huge"},
        {"problems": [{"code": "made_up", "severity": "high", "description": "x"}]},
        {"overall_score": 7},  # the model must not return the overall score
        {"problems": [{"code": "other", "severity": "low", "description": "x"}] * 9},
    ):
        with pytest.raises(ValidationError):
            ThumbnailAudit.model_validate(_audit(**bad))


def test_audit_values_for_storage():
    values = audit_values(ThumbnailAudit.model_validate(_audit(composition_score=7.456)))
    assert values["composition_score"] == Decimal("7.46")
    assert values["problem_codes"] == ["low_contrast", "small_text"]  # distinct, sorted
    assert values["problems"][0] == {"code": "small_text", "severity": "high", "description": "мелкий текст"}
    assert values["improvement_opportunity"] == "medium"


async def test_mock_provider_returns_a_valid_audit():
    schema = ThumbnailAudit.model_json_schema()
    for user in ("a", "b", "c"):
        res = await MockAIProvider().generate_structured(
            StructuredRequest(model="mock-vision-1", system="s", user=user, schema_name="ThumbnailAudit",
                              json_schema=schema)
        )
        ThumbnailAudit.model_validate_json(res.text or "")


def _png(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr.astype(np.uint8), "RGB").save(buf, "PNG")
    return buf.getvalue()


def test_prepare_for_vision_removes_letterbox_and_limits_width():
    # 480x360 hqdefault: 45 px black bars around a 480x270 picture
    arr = np.zeros((360, 480, 3))
    arr[45:315] = (200, 120, 40)
    data, mime, cropped = prepare_for_vision(_png(arr))
    with Image.open(io.BytesIO(data)) as im:
        assert (im.format, im.size, mime, cropped) == ("JPEG", (480, 270), "image/jpeg", True)

    big = np.full((1080, 1920, 3), 128)
    data, _, cropped = prepare_for_vision(_png(big))
    with Image.open(io.BytesIO(data)) as im:
        assert im.size == (1280, 720) and not cropped


def test_metrics_text_handles_missing_metrics():
    assert metrics_text(None) == "- not computed"
