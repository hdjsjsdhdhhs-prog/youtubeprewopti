"""Real thumbnail downloads over HTTPS from YouTube's image CDN."""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx

from app.core.errors import IntegrationError, IntegrationErrorCode, classify_http_status
from app.domains.media.models import ImageSource

PROVIDER = "youtube_thumbnails"
# URLs come from YouTube API payloads; still, only YouTube image hosts are fetched (SSRF guard).
ALLOWED_HOST_SUFFIXES = (".ytimg.com", ".ggpht.com", ".googleusercontent.com")
ALLOWED_HOSTS = frozenset({"img.youtube.com"})


def is_allowed_url(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.username or parts.password:
        return False
    if parts.port not in (None, 443):
        return False
    return host in ALLOWED_HOSTS or host.endswith(ALLOWED_HOST_SUFFIXES)


class HttpThumbnailFetcher:
    name = "http"
    image_source = ImageSource.YOUTUBE_THUMBNAIL

    def __init__(self, client: httpx.AsyncClient, *, max_bytes: int) -> None:
        self._client = client
        self._max_bytes = max_bytes

    async def fetch(self, url: str) -> bytes:
        if not is_allowed_url(url):
            raise IntegrationError(
                IntegrationErrorCode.INVALID_INPUT, f"Thumbnail URL is not a YouTube image URL: {url[:200]}",
                retryable=False, provider=PROVIDER,
            )
        try:
            async with self._client.stream("GET", url, follow_redirects=False) as resp:
                if resp.status_code != 200:
                    raise classify_http_status(PROVIDER, resp.status_code, "downloading a thumbnail")
                declared = resp.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > self._max_bytes:
                    raise self._too_large()
                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    buf += chunk
                    if len(buf) > self._max_bytes:
                        raise self._too_large()
                return bytes(buf)
        except httpx.TimeoutException as exc:
            raise IntegrationError(
                IntegrationErrorCode.TIMEOUT, "Thumbnail download timed out; will retry.",
                retryable=True, provider=PROVIDER,
            ) from exc
        except httpx.TransportError as exc:
            raise IntegrationError(
                IntegrationErrorCode.PROVIDER_UNAVAILABLE,
                f"Thumbnail host is unreachable ({type(exc).__name__}).",
                retryable=True, provider=PROVIDER,
            ) from exc

    def _too_large(self) -> IntegrationError:
        return IntegrationError(
            IntegrationErrorCode.INVALID_INPUT, f"Thumbnail is larger than {self._max_bytes} bytes.",
            retryable=False, provider=PROVIDER,
        )
