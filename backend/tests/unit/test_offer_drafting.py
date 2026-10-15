"""Phase 7 pure logic: the guard («не придумывать цифры»), the personalization score, prompt variables,
offer and lead status paths."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.errors import InvalidTransitionError
from app.core.state_machine import LeadStatus
from app.domains.offers import drafting
from app.domains.offers.drafting import OfferClaim, OfferFact, OfferInputs, OfferSample
from app.domains.offers.models import OFFER_MACHINE, OfferStatus, OfferStyle
from app.domains.offers.service import lead_path


def _inputs(**over) -> OfferInputs:
    facts = [
        OfferFact(id=1, kind="channel", field="channel.title", value="Кухня Пети"),
        OfferFact(id=2, kind="channel", field="channel.subscribers", value="12 500 подписчиков"),
        OfferFact(id=3, kind="video", field="video.title", value="Борщ за 30 минут"),
        OfferFact(
            id=4,
            kind="thumbnail_problem",
            field="thumbnail_analysis.problem",
            value="превью видео «Борщ за 30 минут»: мелкий текст",
        ),
        OfferFact(
            id=5, kind="sample", field="generated_thumbnail", value="бесплатный образец превью (вариант A)"
        ),
    ]
    data = {
        "channel_title": "Кухня Пети",
        "language": "ru",
        "facts": facts,
        "sample": None,
        "template": None,
        "instructions": "",
    }
    return OfferInputs(**(data | over))


def _codes(issues) -> list[str]:
    return [i.code for i in issues]


# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------
def test_numbers_from_the_facts_and_small_counts_pass():
    body = "У канала 12 500 подписчиков, а «Борщ за 30 минут» заслуживает яркого превью. Сделал 3 варианта."
    assert drafting.guard(_inputs(), "Идея для превью", body, "Посмотрите?", []) == []
    # the same number written without the thin space is the same number
    assert drafting.guard(_inputs(), "", "У вас 12500 подписчиков.", "", None) == []


def test_invented_numbers_percentages_and_metrics_are_flagged():
    body = "Ваш CTR упал на 27%. Мы поднимем конверсию в 2 раза и 15 000 просмотров гарантированы."
    issues = drafting.guard(_inputs(), "Рост на 40 процентов", body, "", None)
    codes = _codes(issues)
    assert codes.count("unsupported_number") == 3  # 40 (subject), 27, 15 000 — «2» is a small count
    assert {i.text for i in issues if i.code == "unsupported_number"} == {"40", "27", "15 000"}
    assert codes.count("metric_claim") == 4  # «процентов», CTR, «%», «конверсию»
    assert "guarantee" in codes


def test_small_numbers_are_not_allowed_as_percentages():
    issues = drafting.guard(_inputs(), "", "Это даст 5 % роста.", "", None)
    assert "unsupported_number" in _codes(issues) and "metric_claim" in _codes(issues)


def test_claims_must_point_to_known_facts_and_empty_text_is_an_issue():
    claims = [
        OfferClaim(text="мелкий текст на превью", fact_ids=[4]),
        OfferClaim(text="вы теряете зрителей", fact_ids=[]),
        OfferClaim(text="ваш стиль устарел", fact_ids=[17]),
    ]
    issues = drafting.guard(_inputs(), "", "Текст", "", claims)
    assert [i.text for i in issues if i.code == "unsupported_claim"] == [
        "вы теряете зрителей",
        "ваш стиль устарел",
    ]
    # edited by hand: the AI's claims no longer describe the text
    assert drafting.guard(_inputs(), "", "Текст", "", None) == []
    assert _codes(drafting.guard(_inputs(), "Тема", "   ", "", None)) == ["empty_text"]


def test_numbers_of_the_template_and_instructions_are_known():
    inputs = _inputs(instructions="предложи пакет из 20 превью")
    assert drafting.guard(inputs, "", "Могу сделать пакет из 20 превью.", "", None) == []


# ---------------------------------------------------------------------------
# Personalization
# ---------------------------------------------------------------------------
def test_personalization_counts_fact_kinds_and_named_literals():
    text = "Кухня Пети: на превью «Борщ за 30 минут» мелкий текст. Прилагаю образец."
    score, sources = drafting.personalization(_inputs(), [1, 3, 4, 5, 99], text, [])
    # channel 10 + video 15 + thumbnail_problem 20 + sample 20 = 65 → 0.7 × 65 + 0.3 × 100 (unique)
    assert score == Decimal("75.50")
    assert [s.fact_id for s in sources] == [1, 3, 4, 5]  # 99 does not exist
    assert all(s.counted for s in sources)

    # a video the text does not name does not count, even if the model listed it
    score2, sources2 = drafting.personalization(_inputs(), [3], "Общий текст без конкретики.", [])
    assert [s.counted for s in sources2] == [False] and score2 == Decimal("30.00")


def test_personalization_penalises_copies_of_other_offers():
    text = "Здравствуйте, я делаю превью для каналов и могу сделать вам обложки получше."
    unique, _ = drafting.personalization(_inputs(), [], text, ["совсем другой текст про другое"])
    copy, _ = drafting.personalization(_inputs(), [], text, [text])
    assert unique == Decimal("30.00") and copy == Decimal("0.00")
    assert drafting.max_overlap("", [text]) == 0.0


# ---------------------------------------------------------------------------
# Prompt variables
# ---------------------------------------------------------------------------
def test_prompt_variables_carry_facts_style_sample_and_language():
    sample = OfferSample(
        variant_id=7, label="B", strategy="minimal", video_title="Борщ", fixes=["small_text"]
    )
    v = drafting.prompt_variables(_inputs(sample=sample, instructions="на «вы»"), "C", OfferStyle.FREE_SAMPLE)
    assert v["language"] == "Russian" and v["label"] == "C" and v["style"] == "free_sample"
    assert "F2 [channel] channel.subscribers: 12 500 подписчиков" in v["facts"]
    assert "Variant B (minimal)" in v["sample"] and "small_text" in v["sample"]
    assert v["instructions"] == "на «вы»" and v["template"] == "none"
    no_sample = drafting.prompt_variables(_inputs(), "A", OfferStyle.PROFESSIONAL)
    assert "do not promise an attachment" in no_sample["sample"]
    assert [drafting.style_for(i) for i in range(3)] == [
        ("A", OfferStyle.PROFESSIONAL),
        ("B", OfferStyle.SHORT),
        ("C", OfferStyle.FREE_SAMPLE),
    ]


# ---------------------------------------------------------------------------
# Status paths
# ---------------------------------------------------------------------------
def test_offer_machine():
    assert OFFER_MACHINE.can(OfferStatus.DRAFT, OfferStatus.PENDING_APPROVAL)
    assert OFFER_MACHINE.can(OfferStatus.APPROVED, OfferStatus.PENDING_APPROVAL)  # edited after approve
    assert OFFER_MACHINE.can(OfferStatus.REJECTED, OfferStatus.DRAFT)  # regenerate
    assert not OFFER_MACHINE.can(OfferStatus.DRAFT, OfferStatus.APPROVED)  # no approve without the queue
    assert not OFFER_MACHINE.can(OfferStatus.PENDING_APPROVAL, OfferStatus.SCHEDULED)  # approve first


def test_lead_path_walks_through_offer_statuses():
    L = LeadStatus
    assert lead_path(L.ANALYZED, L.OFFER_DRAFT) == [L.SELECTED, L.OFFER_DRAFT]
    assert lead_path(L.SCHEDULED, L.PENDING_APPROVAL) == [L.APPROVED, L.PENDING_APPROVAL]
    assert lead_path(L.APPROVED, L.OFFER_DRAFT) == [L.PENDING_APPROVAL, L.OFFER_DRAFT]
    assert lead_path(L.GENERATED, L.OFFER_DRAFT) == [L.OFFER_DRAFT]
    assert lead_path(L.APPROVED, L.APPROVED) == []
    with pytest.raises(InvalidTransitionError):
        lead_path(L.REJECTED, L.APPROVED)
