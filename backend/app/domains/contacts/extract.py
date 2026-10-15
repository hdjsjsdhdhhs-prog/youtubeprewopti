"""Deterministic contact extraction (AI_ARCHITECTURE §5): regex + URL parsing, no network, no AI.

Input — texts the channel published itself: its description and the descriptions of its stored videos
(YouTube Data API). Nothing is fetched, no CAPTCHA / login / anti-bot protection is touched (ТЗ §18).

Rules:

* **email** — ``local@domain.tld`` and the common obfuscation ``name [at] domain [dot] com``; file-like
  domains (``logo@2x.png``) are dropped. Normalised: lower case, IDNA domain.
* **telegram** — ``t.me/<user>`` (also ``telegram.me``, ``t.me/s/<user>``) → ``@user``; invite links
  ``t.me/+…`` / ``t.me/joinchat/…`` are kept as links (a group, lower confidence); a bare ``@user`` counts
  only on a line that mentions Telegram («telegram», «телеграм», «tg», «тг»).
* **vk** — ``vk.com/<id>`` (``vk.ru``, ``m.vk.com``), not posts / videos / redirects.
* **instagram** — ``instagram.com/<user>`` → ``@user``, not posts / reels.
* **other** — profiles on other public platforms (Facebook, X, TikTok, Twitch, Discord, Boosty, Patreon,
  Linktree, Taplink, OK, Dzen, Threads).
* **website** — any other ``http(s)://`` / ``www.`` link, **only from the channel description** (video
  descriptions are full of affiliate and product links). One contact per domain. YouTube / Google links and
  URL shorteners are ignored.

Confidence: by type for the channel description; video descriptions start lower and gain with every further
video that repeats the contact. The same contact found in both keeps the channel description as its source.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.domains.contacts.models import ContactSource, ContactType

EXTRACTOR_VERSION = 1
MAX_PER_TYPE = 10  # spam-like descriptions: keep the first ones of each type

TYPE_ORDER = {t: i for i, t in enumerate(ContactType)}

# confidence of a contact found in the channel description
BASE_CONFIDENCE: dict[str, float] = {
    "email": 0.95,
    "email_obfuscated": 0.85,
    "telegram": 0.9,
    "telegram_mention": 0.75,
    "telegram_invite": 0.6,
    "vk": 0.85,
    "instagram": 0.85,
    "other": 0.8,
    "website": 0.8,
}
VIDEO_PENALTY = 0.2  # found in one video description only
VIDEO_REPEAT_BONUS = 0.05  # per further video (up to VIDEO_REPEAT_MAX)
VIDEO_REPEAT_MAX = 3

_TRAILING = ".,;:!?)]}>»”’'\"…"
_URL = re.compile(r"(?i)(?:https?://|www\.)[^\s<>\"'«»(){}\[\]|]+")
# platform links are often written without a scheme
_BARE = re.compile(
    r"(?i)(?<![\w./@-])(?:(?:m\.)?vk\.(?:com|ru)|t\.me|telegram\.(?:me|dog)|(?:www\.)?instagram\.com)"
    r"/[^\s<>\"'«»(){}\[\]|]+"
)
_EMAIL = re.compile(
    r"(?<![\w.+%-])([A-Za-z0-9][A-Za-z0-9._%+-]{0,63})@((?:[\w-]+\.)+[^\W\d_]{2,24})(?![\w-])",
    re.UNICODE,
)
_OBFUSCATED_EMAIL = re.compile(
    r"(?i)(?<![\w.+-])([a-z0-9][a-z0-9._%+-]{0,63})\s*[\[(]\s*(?:at|собака)\s*[\])]\s*"
    r"([a-z0-9-]+(?:\s*(?:[\[(]\s*(?:dot|точка)\s*[\])]|\.)\s*[a-z0-9-]+)+)"
)
_DOT_TOKEN = re.compile(r"(?i)\s*(?:[\[(]\s*(?:dot|точка)\s*[\])]|\.)\s*")
_MENTION = re.compile(r"(?<![\w@.])@([A-Za-z][A-Za-z0-9_]{4,31})(?![\w@])")
_TELEGRAM_CONTEXT = re.compile(r"(?i)(?:telegram|телеграм|\btg\b|\bтг\b)")
_TG_USER = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
_VK_ID = re.compile(r"^[A-Za-z0-9_.]{2,64}$")
_IG_USER = re.compile(r"^[A-Za-z0-9_.]{1,30}$")
_FILE_TLDS = frozenset({"png", "jpg", "jpeg", "gif", "webp", "svg", "mp4", "mov", "pdf", "zip"})

_TG_HOSTS = frozenset({"t.me", "telegram.me", "telegram.dog"})
_TG_RESERVED = frozenset(
    {"share", "addstickers", "addemoji", "addlist", "proxy", "socks", "iv", "c", "login"}
)
_VK_HOSTS = frozenset({"vk.com", "vk.ru", "m.vk.com", "m.vk.ru"})
_VK_RESERVED = frozenset(
    {
        "feed",
        "search",
        "audio",
        "music",
        "app",
        "apps",
        "doc",
        "docs",
        "note",
        "notes",
        "market",
        "im",
        "write",
        "share",
        "away",
        "video",
        "videos",
        "clips",
        "photos",
        "friends",
        "groups",
        "login",
        "settings",
    }
)
# posts, media and apps: wall-1_2, video-1_2, photo1_2, album-1_2, topic-1_2, clip-1_2, app123, page-1_2
_VK_CONTENT = re.compile(r"(?i)^(?:wall|video|photo|album|topic|clip|app|page|doc|audio|market)-?\d")
_IG_HOSTS = frozenset({"instagram.com", "instagr.am"})
_IG_RESERVED = frozenset({"p", "reel", "reels", "tv", "explore", "stories", "accounts", "direct"})
_OTHER_HOSTS = frozenset(
    {
        "facebook.com",
        "fb.com",
        "twitter.com",
        "x.com",
        "tiktok.com",
        "twitch.tv",
        "discord.gg",
        "discord.com",
        "boosty.to",
        "patreon.com",
        "linktr.ee",
        "taplink.cc",
        "ok.ru",
        "dzen.ru",
        "zen.yandex.ru",
        "threads.net",
    }
)
_IGNORED_HOSTS = frozenset(
    {
        "youtube.com",
        "youtu.be",
        "m.youtube.com",
        "music.youtube.com",
        "studio.youtube.com",
        "google.com",
        "goo.gl",
        "g.co",
        "forms.gle",
        "bit.ly",
        "tinyurl.com",
        "clck.ru",
        "cutt.ly",
        "t.co",
        "vk.cc",
        "amzn.to",
        "ali.ski",
        "is.gd",
        "ow.ly",
        "rebrand.ly",
        "shorturl.at",
        "s.click.aliexpress.com",
    }
)


@dataclass(frozen=True)
class Candidate:
    """One contact in one text."""

    type: ContactType
    value: str  # display form
    normalized: str  # dedup key (UNIQUE per channel)
    kind: str  # key of BASE_CONFIDENCE


@dataclass(frozen=True)
class TextSource:
    text: str
    source: ContactSource  # YOUTUBE_DESCRIPTION or VIDEO_DESCRIPTION
    url: str | None = None
    video_id: int | None = None


@dataclass(frozen=True)
class FoundContact:
    type: ContactType
    value: str
    normalized: str
    confidence: float
    source: ContactSource
    source_url: str | None
    source_video_id: int | None
    seen_in_videos: int  # distinct video descriptions that contain it


def _host(netloc: str) -> str:
    host = netloc.rsplit("@", 1)[-1].split(":", 1)[0].lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def _strip_trailing(url: str) -> str:
    while url and url[-1] in _TRAILING:
        url = url[:-1]
    return url


def _segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s]


def _matches(host: str, hosts: frozenset[str]) -> bool:
    return host in hosts or any(host.endswith("." + h) for h in hosts)


def classify_url(raw: str) -> Candidate | None:
    """A link → the contact it points to (or None: ignored / not a profile)."""
    url = _strip_trailing(raw)
    if not url:
        return None
    parts = urlsplit(url if re.match(r"(?i)https?://", url) else "https://" + url)
    host = _host(parts.netloc)
    if not host or "." not in host:
        return None
    segs = _segments(parts.path)

    if host in _TG_HOSTS:
        if not segs:
            return None
        first = segs[0]
        if first == "s" and len(segs) > 1:
            first = segs[1]
        if first.startswith("+") or first.lower() == "joinchat":
            code = first[1:] if first.startswith("+") else (segs[1] if len(segs) > 1 else "")
            if not re.fullmatch(r"[A-Za-z0-9_-]{6,64}", code):
                return None
            return Candidate(ContactType.TELEGRAM, f"t.me/+{code}", f"t.me/+{code}", "telegram_invite")
        if first.lower() in _TG_RESERVED or not _TG_USER.match(first):
            return None
        return Candidate(ContactType.TELEGRAM, f"@{first}", f"@{first.lower()}", "telegram")

    if host in _VK_HOSTS:
        if not segs:
            return None
        first = segs[0]
        low = first.lower()
        if low.endswith(".php") or low in _VK_RESERVED or _VK_CONTENT.match(first) or not _VK_ID.match(first):
            return None
        return Candidate(ContactType.VK, f"vk.com/{first}", f"vk.com/{low}", "vk")

    if _matches(host, _IG_HOSTS):
        if not segs or segs[0].lower() in _IG_RESERVED or not _IG_USER.match(segs[0]):
            return None
        user = segs[0].strip(".")
        if not user:
            return None
        return Candidate(ContactType.INSTAGRAM, f"@{user}", f"@{user.lower()}", "instagram")

    if _matches(host, _IGNORED_HOSTS) or host.split(".")[-1] in _FILE_TLDS:
        return None

    if _matches(host, _OTHER_HOSTS):
        if not segs:
            return None
        key = f"{host}/{segs[0]}"
        return Candidate(ContactType.OTHER, key, key.lower(), "other")

    return Candidate(ContactType.WEBSITE, host, host, "website")


def normalize_email(local: str, domain: str) -> tuple[str, str] | None:
    """(display, normalized) or None for file-like / invalid addresses."""
    domain = domain.lower().strip(".")
    tld = domain.rsplit(".", 1)[-1]
    if tld in _FILE_TLDS or ".." in local or local.endswith(".") or len(local) > 64:
        return None
    try:
        ascii_domain = domain.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    display = f"{local.lower()}@{domain}"
    return display, f"{local.lower()}@{ascii_domain}"


def extract_text(text: str) -> list[Candidate]:
    """All contacts in one text, without duplicates (e-mails first, then links, then mentions)."""
    found: dict[tuple[ContactType, str], Candidate] = {}

    def add(c: Candidate | None) -> None:
        if c is not None:
            found.setdefault((c.type, c.normalized), c)

    urls = list(_URL.finditer(text))
    spans = [m.span() for m in urls]

    def in_url(pos: int) -> bool:
        return any(a <= pos < b for a, b in spans)

    for m in _EMAIL.finditer(text):
        if in_url(m.start()):  # https://user:password@host is a link, not an address
            continue
        norm = normalize_email(m.group(1), m.group(2))
        if norm:
            add(Candidate(ContactType.EMAIL, norm[0], norm[1], "email"))
    for m in _OBFUSCATED_EMAIL.finditer(text):
        domain = _DOT_TOKEN.sub(".", m.group(2))
        norm = normalize_email(m.group(1), domain)
        if norm and "." in domain:
            add(Candidate(ContactType.EMAIL, norm[0], norm[1], "email_obfuscated"))
    for m in urls:
        add(classify_url(m.group(0)))
    for m in _BARE.finditer(text):
        if in_url(m.start()):
            continue
        add(classify_url(m.group(0)))
    for line in text.splitlines():
        if not _TELEGRAM_CONTEXT.search(line):
            continue
        for m in _MENTION.finditer(line):
            user = m.group(1)
            add(Candidate(ContactType.TELEGRAM, f"@{user}", f"@{user.lower()}", "telegram_mention"))
    return list(found.values())


def _video_confidence(base: float, videos: int) -> float:
    bonus = VIDEO_REPEAT_BONUS * min(max(videos - 1, 0), VIDEO_REPEAT_MAX)
    return round(min(base - VIDEO_PENALTY + bonus, base - 0.05), 2)


def extract_channel(sources: Sequence[TextSource]) -> list[FoundContact]:
    """Contacts of a channel from all its texts. Websites only from the channel description."""
    channel: dict[tuple[ContactType, str], tuple[Candidate, TextSource]] = {}
    in_videos: dict[tuple[ContactType, str], list[tuple[Candidate, TextSource]]] = {}
    for src in sources:
        for c in extract_text(src.text):
            key = (c.type, c.normalized)
            if src.source == ContactSource.VIDEO_DESCRIPTION:
                if c.type == ContactType.WEBSITE:
                    continue
                seen = in_videos.setdefault(key, [])
                if all(s.video_id != src.video_id for _, s in seen):
                    seen.append((c, src))
            else:
                channel.setdefault(key, (c, src))

    out: list[FoundContact] = []
    for key in dict.fromkeys([*channel, *in_videos]):
        videos = in_videos.get(key, [])
        if key in channel:
            c, src = channel[key]
            confidence = BASE_CONFIDENCE[c.kind]
        else:
            c, src = videos[0]
            confidence = _video_confidence(BASE_CONFIDENCE[c.kind], len(videos))
        out.append(
            FoundContact(
                type=c.type,
                value=c.value,
                normalized=c.normalized,
                confidence=confidence,
                source=src.source,
                source_url=src.url,
                source_video_id=src.video_id,
                seen_in_videos=len(videos),
            )
        )
    out.sort(key=lambda f: (TYPE_ORDER[f.type], -f.confidence, f.normalized))
    per_type: dict[ContactType, int] = {}
    kept: list[FoundContact] = []
    for f in out:
        per_type[f.type] = per_type.get(f.type, 0) + 1
        if per_type[f.type] <= MAX_PER_TYPE:
            kept.append(f)
    return kept


_EMAIL_FULL = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._%+-]{0,63})@((?:[\w-]+\.)+[^\W\d_]{2,24})$", re.UNICODE)


def normalize_manual(type_: ContactType, raw: str) -> tuple[str, str] | None:
    """(display, normalized) of a value an operator typed for ``type_``; None = not a valid value of it."""
    value = raw.strip()
    if not value:
        return None
    if type_ == ContactType.EMAIL:
        m = _EMAIL_FULL.match(value.removeprefix("mailto:"))
        return normalize_email(m.group(1), m.group(2)) if m else None
    if type_ in (ContactType.TELEGRAM, ContactType.INSTAGRAM) and "/" not in value:
        user = value.removeprefix("@")
        pattern = _TG_USER if type_ == ContactType.TELEGRAM else _IG_USER
        return (f"@{user}", f"@{user.lower()}") if pattern.match(user) else None
    if type_ == ContactType.VK and "/" not in value and "." not in value:
        return (f"vk.com/{value}", f"vk.com/{value.lower()}") if _VK_ID.match(value) else None
    c = classify_url(value)
    if c is not None and c.type == type_:
        return c.value, c.normalized
    if type_ == ContactType.OTHER and c is not None and c.type == ContactType.WEBSITE:
        return c.value, c.normalized  # a profile on a platform we do not know
    return None


def types_of(contacts: Iterable[FoundContact]) -> set[ContactType]:
    return {c.type for c in contacts}
