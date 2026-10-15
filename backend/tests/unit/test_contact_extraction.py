"""Deterministic contact extraction (Phase 6, ТЗ §18): no network, no AI."""

from __future__ import annotations

from app.domains.contacts.extract import (
    TextSource,
    classify_url,
    extract_channel,
    extract_text,
    normalize_email,
)
from app.domains.contacts.models import ContactSource, ContactType

DESCRIPTION = """По вопросам рекламы: Info@Example.COM
Telegram: @my_channel_tg, пишите
Мой сайт https://www.mysite.ru/about?x=1. VK: vk.com/club123 и https://vk.com/wall-1_2
insta instagram.com/some.user/ и https://instagram.com/p/abc
https://t.me/+AbCdEf123 https://t.me/s/News_Chan https://youtu.be/x https://bit.ly/a
mail: hello [at] studio [dot] io; logo@2x.png; https://boosty.to/author; почта: почта@пример.рф
"""


def _pairs(text: str) -> set[tuple[str, str]]:
    return {(c.type.value, c.normalized) for c in extract_text(text)}


def test_extracts_every_type_and_ignores_noise() -> None:
    assert _pairs(DESCRIPTION) == {
        ("email", "info@example.com"),
        ("email", "hello@studio.io"),  # obfuscated
        ("telegram", "@my_channel_tg"),  # mention on a «Telegram» line
        ("telegram", "t.me/+AbCdEf123"),  # invite link keeps its case
        ("telegram", "@news_chan"),  # t.me/s/<user>
        ("website", "mysite.ru"),  # one per domain, no www / path / query
        ("vk", "vk.com/club123"),  # the wall post is not a profile
        ("instagram", "@some.user"),  # the post /p/abc is not a profile
        ("other", "boosty.to/author"),
    }
    # the local part «почта» is not ASCII: not an address we can use
    assert all("пример" not in n for _, n in _pairs(DESCRIPTION))


def test_email_rules() -> None:
    assert normalize_email("Sales", "Пример.РФ") == ("sales@пример.рф", "sales@xn--e1afmkfd.xn--p1ai")
    assert normalize_email("logo", "2x.png") is None
    assert normalize_email("a..b", "x.com") is None
    assert _pairs("contact:info@site.com.") == {("email", "info@site.com")}
    assert _pairs("https://user:pw@host.example/x") == {("website", "host.example")}


def test_mentions_need_telegram_context() -> None:
    assert _pairs("follow @someone_here on twitter") == set()
    assert _pairs("тг: @someone_here") == {("telegram", "@someone_here")}
    assert _pairs("TG @abcd") == set()  # 4 characters: too short for a username
    # an e-mail on a telegram line is not a mention
    assert _pairs("telegram и почта me@mail.com") == {("email", "me@mail.com")}


def test_classify_url() -> None:
    assert classify_url("https://t.me/share/url?u=1") is None
    assert classify_url("t.me/c/123/45") is None
    tg = classify_url("https://telegram.me/Some_User)")
    assert tg is not None and (tg.value, tg.normalized) == ("@Some_User", "@some_user")
    assert classify_url("https://vk.com/away.php?to=x") is None
    assert classify_url("https://vk.com/video-1_2") is None
    for profile in ("apple_fan", "marketing_pro", "id42", "public777"):
        vk = classify_url(f"https://vk.com/{profile}")
        assert vk is not None and vk.type == ContactType.VK, profile
    assert classify_url("https://www.youtube.com/@chan") is None
    assert classify_url("https://music.youtube.com/x") is None
    assert classify_url("https://example.com/logo.png") is not None  # a site, not a file domain
    assert classify_url("https://x.com") is None  # no profile
    site = classify_url("http://Shop.Example.org/")
    assert site is not None and (site.type, site.value) == (ContactType.WEBSITE, "shop.example.org")


def test_channel_merge_sources_and_confidence() -> None:
    sources = [
        TextSource("сайт https://brand.io, почта team@brand.io", ContactSource.YOUTUBE_DESCRIPTION, "chan"),
        TextSource(
            "team@brand.io t.me/brand_news https://shop.affiliate.com",
            ContactSource.VIDEO_DESCRIPTION,
            "v1",
            video_id=1,
        ),
        TextSource("t.me/brand_news", ContactSource.VIDEO_DESCRIPTION, "v2", video_id=2),
        TextSource("t.me/brand_news again", ContactSource.VIDEO_DESCRIPTION, "v2", video_id=2),  # same video
    ]
    found = {(f.type, f.normalized): f for f in extract_channel(sources)}
    assert set(found) == {
        (ContactType.EMAIL, "team@brand.io"),
        (ContactType.TELEGRAM, "@brand_news"),
        (ContactType.WEBSITE, "brand.io"),  # affiliate links of videos are not the channel's website
    }
    email = found[(ContactType.EMAIL, "team@brand.io")]
    assert (email.source, email.source_url, email.confidence, email.seen_in_videos) == (
        ContactSource.YOUTUBE_DESCRIPTION,
        "chan",
        0.95,
        1,
    )
    tg = found[(ContactType.TELEGRAM, "@brand_news")]
    assert (tg.source, tg.source_video_id, tg.seen_in_videos) == (ContactSource.VIDEO_DESCRIPTION, 1, 2)
    assert tg.confidence == 0.75  # 0.9 − 0.2 + one repeat 0.05
    # order: by type, then confidence
    assert [f.type for f in extract_channel(sources)] == [
        ContactType.EMAIL,
        ContactType.TELEGRAM,
        ContactType.WEBSITE,
    ]


def test_video_confidence_is_capped_below_the_channel_description() -> None:
    sources = [
        TextSource("tg t.me/brand_news", ContactSource.VIDEO_DESCRIPTION, f"v{i}", video_id=i)
        for i in range(10)
    ]
    [tg] = extract_channel(sources)
    assert tg.confidence == 0.85 and tg.seen_in_videos == 10


def test_spam_is_capped_per_type() -> None:
    text = " ".join(f"user{i}@mail{i}.com" for i in range(30))
    found = extract_channel([TextSource(text, ContactSource.YOUTUBE_DESCRIPTION)])
    assert len(found) == 10
