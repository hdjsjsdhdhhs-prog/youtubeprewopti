"""Phase 5 deterministic parts: problems → design directives, variant strategies, prompt variables, the
16:9 crop of generated images and decoding of uploaded references."""

from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from app.core.errors import AppError
from app.domains.generation import brief as b
from app.domains.generation.service import MAX_REFERENCE_BYTES, decode_image
from app.domains.media.imaging import InvalidImageError, fit_thumbnail


def _brief(**over) -> b.GenerationBrief:
    base = {
        "channel_title": "Кухня Пети",
        "niches": ["Кулинария"],
        "video_title": "Борщ за 20 минут",
        "base_image": "current_thumbnail",
        "fixes": [],
        "recommendations": [],
        "channel_summary": None,
        "palette": [],
        "instructions": "",
        "references": [],
        "history": b.BriefHistory(),
    }
    return b.GenerationBrief(**(base | over))


# ---------------------------------------------------------------------------
# build_fixes
# ---------------------------------------------------------------------------
def test_fixes_thumbnail_first_by_severity_then_channel_deduplicated():
    thumb = [
        {"code": "small_text", "severity": "low", "description": "мелкий текст"},
        {"code": "cluttered", "severity": "high", "description": "много деталей"},
        {"code": "other", "severity": "high", "description": "нет общей директивы"},
    ]
    channel = [
        {"code": "cluttered", "frequency": "high", "description": "дубликат кода превью"},
        {"code": "low_contrast", "frequency": "medium", "description": "низкий контраст"},
    ]
    fixes = b.build_fixes(thumb, channel)
    assert [(x.code, x.source, x.severity) for x in fixes] == [
        ("cluttered", "thumbnail", "high"),
        ("small_text", "thumbnail", "low"),
        ("low_contrast", "channel", "medium"),
    ]
    assert fixes[0].problem == "много деталей"
    assert fixes[0].directive == b.PROBLEM_FIXES[b.ProblemCode.CLUTTERED]


def test_fixes_are_capped_and_every_known_code_has_a_directive():
    every = [{"code": c.value, "severity": "high", "description": ""} for c in b.ProblemCode]
    assert len(b.build_fixes(every, [])) == b.MAX_FIXES
    assert set(b.PROBLEM_FIXES) == set(b.ProblemCode) - {b.ProblemCode.OTHER}
    assert b.build_fixes([], []) == []


def test_long_problem_description_is_truncated():
    [fix] = b.build_fixes([{"code": "cluttered", "severity": "high", "description": "x" * 1000}], [])
    assert len(fix.problem) == 300


# ---------------------------------------------------------------------------
# Strategies and prompt variables
# ---------------------------------------------------------------------------
def test_each_variant_gets_its_own_label_and_strategy():
    got = [b.strategy_for(i) for i in range(len(b.LABELS))]
    assert [label for label, _, _ in got] == list("ABCDEF")
    assert len({key for _, key, _ in got}) == len(b.LABELS)  # A–F explore different directions


def test_prompt_variables_carry_the_brief():
    brief = _brief(
        fixes=b.build_fixes([{"code": "small_text", "severity": "high", "description": "d"}], []),
        recommendations=["Увеличить заголовок"],
        channel_summary="Тёплые тона, лицо автора",
        palette=["#ff0000", "#00ff00"],
        instructions="Без текста",
        history=b.BriefHistory(previous_requests=1, approved=["minimal"], rejected=["cinematic"]),
    )
    v = b.prompt_variables(brief, "B", "strategy text")
    assert (v["label"], v["strategy"], v["channel_title"], v["niches"]) == (
        "B", "strategy text", "Кухня Пети", "Кулинария"
    )
    assert "(fixes: small_text)" in v["fixes"] and v["fixes"].startswith("- ")
    assert v["recommendations"] == "- Увеличить заголовок"
    assert v["palette"] == "#ff0000, #00ff00" and v["instructions"] == "Без текста"
    assert "CURRENT thumbnail" in v["base_image"] and v["references"] == "- none"
    assert "liked these directions before: minimal" in v["history"]
    assert "rejected these directions before: cinematic" in v["history"]


def test_prompt_variables_defaults_without_analysis():
    v = b.prompt_variables(_brief(base_image=None, video_title=None, niches=[]), "A", "s")
    assert v["fixes"] == "- no analysed problems" and v["history"] == "- no earlier decisions"
    assert v["channel_style"] == "not analysed" and v["palette"] == "not measured"
    assert v["niches"] == "not specified" and v["video_title"] == "(no specific video)"
    assert "No current thumbnail" in v["base_image"]


@pytest.mark.parametrize(
    ("base_image", "first"), [("current_thumbnail", 2), ("parent_variant", 2), (None, 1)]
)
def test_reference_images_are_numbered_after_the_base_image(base_image, first):
    refs = [b.BriefReference(id=i, title=f"R{i}", style="", description="") for i in (1, 2)]
    v = b.prompt_variables(_brief(base_image=base_image, references=refs), "A", "s")
    assert v["references"].startswith(f"Images {first}..{first + 1} are style references")
    assert "R1 (style not set): no description" in v["references"]


# ---------------------------------------------------------------------------
# fit_thumbnail: whatever the model returns becomes a 1280×720 JPEG
# ---------------------------------------------------------------------------
def _png(size: tuple[int, int], *, split: bool = False) -> bytes:
    img = Image.new("RGB", size, (0, 0, 255))
    if split:  # left half red, right half blue: shows which side survived the crop
        img.paste((255, 0, 0), (0, 0, size[0] // 2, size[1]))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


@pytest.mark.parametrize("size", [(1536, 1024), (1024, 1024), (1024, 1536), (1280, 720), (7, 3)])
def test_fit_thumbnail_is_always_16_9_jpeg(size):
    with Image.open(io.BytesIO(fit_thumbnail(_png(size)))) as im:
        assert (im.format, im.size, im.mode) == ("JPEG", (1280, 720), "RGB")


def test_fit_thumbnail_crops_the_centre():
    with Image.open(io.BytesIO(fit_thumbnail(_png((2000, 720), split=True)))) as im:
        r_left, _, b_left = im.getpixel((10, 360))
        r_right, _, b_right = im.getpixel((1270, 360))
    assert r_left > 200 and b_left < 60 and b_right > 200 and r_right < 60


def test_fit_thumbnail_rejects_garbage():
    with pytest.raises(InvalidImageError):
        fit_thumbnail(b"not an image")


# ---------------------------------------------------------------------------
# decode_image (references are uploaded as base64)
# ---------------------------------------------------------------------------
def test_decode_image_plain_and_data_url():
    raw = _png((4, 4))
    enc = base64.b64encode(raw).decode()
    assert decode_image(enc) == raw
    assert decode_image(f"data:image/png;base64,{enc}") == raw


def test_decode_image_rejects_invalid_and_too_large():
    with pytest.raises(AppError) as bad:
        decode_image("@@not base64@@")
    assert bad.value.code == "invalid_image"
    with pytest.raises(AppError) as big:
        decode_image(base64.b64encode(b"\0" * (MAX_REFERENCE_BYTES + 1)).decode())
    assert big.value.code == "image_too_large"
