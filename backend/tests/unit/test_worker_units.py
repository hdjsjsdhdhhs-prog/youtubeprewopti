from datetime import UTC, datetime

import httpx
import pytest
from procrastinate.jobs import Job
from sqlalchemy.exc import OperationalError

from app.core.errors import IntegrationError, IntegrationErrorCode, NotFoundError, RetryableJobError
from app.domains.jobs.service import job_fingerprint
from app.domains.media.imaging import inspect_image
from app.domains.media.models import ImageSource
from app.providers.thumbnails import HttpThumbnailFetcher, MockThumbnailFetcher
from app.providers.thumbnails.http import is_allowed_url
from app.workers.registry import JobRetryStrategy, describe_error, is_retryable


def _job(attempts: int) -> Job:
    return Job(id=1, queue="bulk", lock=None, queueing_lock=None, task_name="t", attempts=attempts)


def _integration(retryable: bool) -> IntegrationError:
    code = IntegrationErrorCode.RATE_LIMITED if retryable else IntegrationErrorCode.NOT_FOUND
    return IntegrationError(code, "human text", retryable=retryable, provider="p")


# --------------------------------------------------------------------------- retry policy
@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (_integration(True), True),
        (_integration(False), False),
        (RetryableJobError("x"), True),
        (TimeoutError(), True),
        (ConnectionError(), True),
        (OperationalError("SELECT 1", {}, Exception("conn lost")), True),
        (ValueError("bug"), False),
        (KeyError("bug"), False),
    ],
)
def test_is_retryable(exc, expected):
    assert is_retryable(exc) is expected


def test_retry_strategy_backoff_and_limit(settings, monkeypatch):
    monkeypatch.setattr(settings, "worker_retry_max_attempts", 4)
    monkeypatch.setattr(settings, "worker_retry_base_seconds", 10)
    monkeypatch.setattr(settings, "worker_retry_max_backoff_seconds", 30)
    s = JobRetryStrategy()
    exc = RetryableJobError("temporary")
    waits = [s.get_retry_decision(exception=exc, job=_job(a)) for a in range(4)]
    assert [w.retry_at is not None for w in waits[:3]] == [True, True, True]
    assert waits[3] is None  # 4th run was the last one allowed
    assert s.get_retry_decision(exception=ValueError(), job=_job(0)) is None  # bugs are not retried
    assert JobRetryStrategy(max_attempts=1).get_retry_decision(exception=exc, job=_job(0)) is None


def test_retry_strategy_wait_is_exponential_and_capped(settings, monkeypatch):
    monkeypatch.setattr(settings, "worker_retry_max_attempts", 10)
    monkeypatch.setattr(settings, "worker_retry_base_seconds", 10)
    monkeypatch.setattr(settings, "worker_retry_max_backoff_seconds", 30)
    waits = []
    for a in range(4):
        before = datetime.now(UTC)
        decision = JobRetryStrategy().get_retry_decision(exception=RetryableJobError(), job=_job(a))
        waits.append(round((decision.retry_at - before).total_seconds()))
    assert waits == [10, 20, 30, 30]  # 10 * 2**attempt, capped at 30


def test_describe_error():
    assert describe_error(_integration(False)) == ("not_found", "human text")
    assert describe_error(NotFoundError("Video not found")) == ("not_found", "Video not found")
    assert describe_error(RetryableJobError("busy"))[0] == "transient_error"
    code, human = describe_error(ZeroDivisionError("secret internals"))
    assert code == "internal_error" and "secret internals" not in human


def test_fingerprint_is_stable_and_scoped():
    a = job_fingerprint("thumbnail_download", 1, {"video_ids": [1, 2], "channel_id": 5})
    assert a == job_fingerprint("thumbnail_download", 1, {"channel_id": 5, "video_ids": [1, 2]})
    assert a != job_fingerprint("thumbnail_download", 2, {"video_ids": [1, 2], "channel_id": 5})
    assert a != job_fingerprint("other", 1, {"video_ids": [1, 2], "channel_id": 5})


# --------------------------------------------------------------------------- fetchers
@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://i.ytimg.com/vi/abc/hqdefault.jpg", True),
        ("https://i9.ytimg.com/vi/abc/maxresdefault.jpg", True),
        ("https://yt3.ggpht.com/avatar.jpg", True),
        ("http://i.ytimg.com/vi/abc/hqdefault.jpg", False),  # plain http
        ("https://i.ytimg.com.evil.example/x.jpg", False),
        ("https://evil.example/?u=i.ytimg.com", False),
        ("https://user:pw@i.ytimg.com/x.jpg", False),
        ("https://i.ytimg.com:8443/x.jpg", False),
        ("https://127.0.0.1/x.jpg", False),
        ("file:///etc/passwd", False),
    ],
)
def test_thumbnail_url_allowlist(url, ok):
    assert is_allowed_url(url) is ok


def _fetcher(handler, max_bytes=1000) -> HttpThumbnailFetcher:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return HttpThumbnailFetcher(client, max_bytes=max_bytes)


URL = "https://i.ytimg.com/vi/abc/hqdefault.jpg"


async def test_http_fetcher_success():
    f = _fetcher(lambda req: httpx.Response(200, content=b"imagebytes"))
    assert await f.fetch(URL) == b"imagebytes"
    assert f.image_source == ImageSource.YOUTUBE_THUMBNAIL


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [(404, "not_found", False), (403, "forbidden", False), (429, "rate_limited", True),
     (503, "provider_unavailable", True), (302, "unknown", False)],
)
async def test_http_fetcher_classifies_status(status, code, retryable):
    f = _fetcher(lambda req: httpx.Response(status, headers={"location": "https://evil.example/"}))
    with pytest.raises(IntegrationError) as err:
        await f.fetch(URL)
    assert err.value.code == code and err.value.retryable is retryable


async def test_http_fetcher_transport_errors_are_retryable():
    def timeout(req):
        raise httpx.ReadTimeout("slow", request=req)

    def refused(req):
        raise httpx.ConnectError("refused", request=req)

    for handler, code in ((timeout, "timeout"), (refused, "provider_unavailable")):
        with pytest.raises(IntegrationError) as err:
            await _fetcher(handler).fetch(URL)
        assert err.value.code == code and err.value.retryable


async def test_http_fetcher_rejects_large_and_foreign_urls():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, content=b"x" * 2000)

    f = _fetcher(handler, max_bytes=1000)
    with pytest.raises(IntegrationError) as too_big:
        await f.fetch(URL)
    assert too_big.value.code == "invalid_input" and not too_big.value.retryable

    calls.clear()
    with pytest.raises(IntegrationError) as foreign:
        await f.fetch("https://169.254.169.254/latest/meta-data")
    assert foreign.value.code == "invalid_input" and calls == []  # never requested


async def test_mock_fetcher_is_deterministic_valid_and_labelled_demo():
    f = MockThumbnailFetcher()
    a, b = await f.fetch(URL), await f.fetch(URL)
    assert a == b and a != await f.fetch(URL + "?2")
    info = inspect_image(a)
    assert info.format == "JPEG" and (info.width, info.height) == (320, 180)
    assert f.image_source == ImageSource.DEMO
