"""Phase 3.6 channel audit: output schema, mapping of prompt positions to video ids, stored values, the
inputs digest (cache key) and the video list shown to the model."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.domains.analysis.audit import SUBSCORES
from app.domains.analysis.channel_audit import ChannelVisualAudit, map_indexes
from app.domains.analysis.channel_pipeline import InputVideo, audit_values, inputs_digest, videos_text
from app.domains.analysis.models import AILevel
from app.providers.ai import MockAIProvider, StructuredRequest


def audit(**over) -> ChannelVisualAudit:
    base = {
        "visual_consistency": 3.0, "repeated_template": True, "outdated_style": 8.0,
        "has_design_system": False,
        "better_performing_patterns": [{"pattern": "крупное лицо", "evidence": "видео 2 и 3"}],
        "recurring_problems": [
            {"code": "small_text", "frequency": "high", "description": "мелкий текст"},
            {"code": "cluttered", "frequency": "low", "description": "шум"},
            {"code": "small_text", "frequency": "medium", "description": "повтор"},
        ],
        "weakest_video_indexes": [3, 1, 3], "best_redesign_candidates": [
            {"video_index": 2, "why": "сильная идея"}, {"video_index": 2, "why": "повтор"},
            {"video_index": 9, "why": "нет такого"},
        ],
        "visual_opportunity": "high", "summary": "Единого стиля нет.",
    }
    return ChannelVisualAudit.model_validate(base | over)


def test_map_indexes_keeps_order_and_drops_unknown_and_repeats():
    assert map_indexes([3, 1, 3, 0, 7], [10, 20, 30]) == [30, 10]
    assert map_indexes([], [10]) == []


def test_schema_rejects_out_of_range_values():
    with pytest.raises(ValidationError):
        audit(visual_consistency=11)
    with pytest.raises(ValidationError):
        audit(weakest_video_indexes=[13])  # beyond MAX_THUMBNAILS
    with pytest.raises(ValidationError):
        audit(recurring_problems=[{"code": "made_up", "frequency": "high", "description": "x"}])
    with pytest.raises(ValidationError):
        audit(overall_score=5)  # extra fields are forbidden: numbers are computed by code


def test_audit_values_map_positions_to_video_ids():
    v = audit_values(audit(), [101, 102, 103])
    assert v["weakest_video_ids"] == [103, 101]
    # the repeated position 2 and the unknown position 9 are dropped
    assert v["redesign_candidates"] == [{"video_id": 102, "why": "сильная идея"}]
    assert v["recurring_problem_codes"] == ["cluttered", "small_text"]
    assert v["visual_opportunity"] == AILevel.HIGH
    assert (v["visual_consistency"], v["outdated_style_score"]) == (Decimal("3.00"), Decimal("8.00"))
    assert v["patterns"][0]["pattern"] == "крупное лицо"


def _video(video_id: int, sha: str, analysis_id: int, **scores) -> InputVideo:
    analysis = SimpleNamespace(
        id=analysis_id, problem_codes=["small_text"], improvement_opportunity=AILevel.HIGH,
        visual_summary="Слабо.", **{k: Decimal(str(scores.get(k, 4))) for k in SUBSCORES},
    )
    return InputVideo(
        video_id=video_id, title=f"Видео {video_id}", published_at=datetime(2026, 10, 1, tzinfo=UTC),
        view_count=12345, asset=SimpleNamespace(sha256=sha), analysis=analysis,  # type: ignore[arg-type]
    )


def test_inputs_digest_changes_with_videos_images_and_audits():
    a, b = _video(1, "aa", 10), _video(2, "bb", 20)
    base = inputs_digest([a, b])
    assert base == inputs_digest([_video(1, "aa", 10), _video(2, "bb", 20)]) and len(base) == 64
    assert base != inputs_digest([b, a])  # order = what "Video N" means
    assert base != inputs_digest([a, _video(2, "cc", 20)])  # new thumbnail image
    assert base != inputs_digest([a, _video(2, "bb", 21)])  # new individual audit
    assert base != inputs_digest([a])


def test_videos_text_numbers_videos_and_uses_built_in_weights():
    text = videos_text([_video(1, "aa", 10), _video(2, "bb", 20, composition_score=9)])
    assert text.startswith('Video 1: "Видео 1" | published 2026-10-01 | views 12 345')
    assert "Video 2:" in text and "problems: small_text" in text and "opportunity high" in text
    # default weights: ten quality subscores, redesign_potential excluded => (9 + 9*4) / 10 = 4.5
    assert "quality 4.00/10" in text and "quality 4.50/10" in text


async def test_mock_provider_output_is_a_valid_channel_audit():
    schema = ChannelVisualAudit.model_json_schema()
    response = await MockAIProvider().generate_structured(
        StructuredRequest(
            model="m", system="s", user="u", schema_name="ChannelVisualAudit", json_schema=schema
        )
    )
    ChannelVisualAudit.model_validate_json(response.text or "")
