from datetime import timedelta

import httpx
from conftest import TEST_PASSWORD, csrf_headers, login
from sqlalchemy import func, select
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.core.config import get_settings
from app.domains.identity.models import AuditLog, UserSession
from app.domains.identity.service import LOGIN_FAILED_ACTION, utcnow


async def test_login_sets_cookies_and_me_returns_context(client, owner, settings):
    r = await login(client, "owner@example.com")  # email is case-insensitive
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["email"] == "owner@example.com"
    assert body["role"] == "owner"
    assert body["workspace"]["slug"] == "test-ws"

    set_cookie = r.headers.get_list("set-cookie")
    session_cookie = next(c for c in set_cookie if c.startswith(settings.session_cookie_name + "="))
    csrf_cookie = next(c for c in set_cookie if c.startswith(settings.csrf_cookie_name + "="))
    assert "httponly" in session_cookie.lower() and "samesite=lax" in session_cookie.lower()
    assert "httponly" not in csrf_cookie.lower()

    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json() == body


async def test_session_token_is_stored_hashed_and_rotated_per_login(client, owner, db, settings):
    await login(client, "owner@example.com")
    first = client.cookies.get(settings.session_cookie_name)
    await login(client, "owner@example.com")
    second = client.cookies.get(settings.session_cookie_name)
    assert first and second and first != second
    stored = (await db.scalars(select(UserSession.token_hash))).all()
    assert len(stored) == 2
    assert first not in stored and second not in stored


async def test_wrong_password_and_unknown_user_are_indistinguishable(client, owner, db):
    bad_pw = await login(client, "owner@example.com", "wrong-password-xyz")
    unknown = await login(client, "nobody@example.com", "wrong-password-xyz")
    assert bad_pw.status_code == unknown.status_code == 401
    assert bad_pw.json() == unknown.json()
    assert bad_pw.json()["error"]["code"] == "invalid_credentials"
    failed = await db.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.action == LOGIN_FAILED_ACTION)
    )
    assert failed == 2


async def test_me_requires_authentication(client):
    r = await client.get("/api/auth/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthenticated"
    client.cookies.set(get_settings().session_cookie_name, "garbage")
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_logout_requires_csrf_then_revokes_session(client, owner, settings):
    await login(client, "owner@example.com")

    no_csrf = await client.post("/api/auth/logout")
    assert no_csrf.status_code == 403
    assert no_csrf.json()["error"]["code"] == "csrf_failed"

    wrong = await client.post("/api/auth/logout", headers={settings.csrf_header_name: "forged"})
    assert wrong.status_code == 403

    token = client.cookies.get(settings.session_cookie_name)
    ok = await client.post("/api/auth/logout", headers=csrf_headers(client, settings))
    assert ok.status_code == 204
    assert client.cookies.get(settings.session_cookie_name) is None

    # the old token is revoked server-side even if a client keeps it
    client.cookies.set(settings.session_cookie_name, token)
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_idle_and_absolute_session_expiry(client, owner, db, settings):
    await login(client, "owner@example.com")
    session = await db.scalar(select(UserSession))

    session.last_seen_at = utcnow() - timedelta(minutes=settings.session_idle_minutes + 1)
    await db.flush()
    assert (await client.get("/api/auth/me")).status_code == 401

    session.last_seen_at = utcnow()
    session.expires_at = utcnow() - timedelta(seconds=1)
    await db.flush()
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_inactive_user_cannot_login_or_use_session(client, owner, db):
    user, _ = owner
    await login(client, "owner@example.com")
    user.is_active = False
    await db.flush()
    assert (await client.get("/api/auth/me")).status_code == 401
    assert (await login(client, "owner@example.com")).status_code == 401


async def test_login_rate_limited_by_email(app, client, owner, settings):
    limited = settings.model_copy(update={"login_rate_limit_attempts": 3})
    app.dependency_overrides[get_settings] = lambda: limited

    for _ in range(3):
        assert (await login(client, "owner@example.com", "wrong-password-xyz")).status_code == 401
    # even the correct password is refused while the limit is active
    r = await login(client, "owner@example.com", TEST_PASSWORD)
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"


def _behind_proxy(app, trusted: str) -> httpx.AsyncClient:
    """The app as deployed: uvicorn's proxy-headers middleware (FORWARDED_ALLOW_IPS) in front; the peer
    (10.0.0.1) plays the Next proxy."""
    transport = httpx.ASGITransport(
        app=ProxyHeadersMiddleware(app, trusted_hosts=trusted), client=("10.0.0.1", 1)
    )
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def _failed_login(c: httpx.AsyncClient, email: str, client_ip: str) -> int:
    r = await c.post(
        "/api/auth/login", json={"email": email, "password": "wrong-password-xyz"},
        headers={"X-Forwarded-For": client_ip},
    )
    return r.status_code


async def test_client_ip_is_taken_from_forwarded_for_only_behind_trusted_proxy(app, db):
    async with _behind_proxy(app, trusted="10.0.0.1") as c:
        await _failed_login(c, "trusted@example.com", "203.0.113.9")
    async with _behind_proxy(app, trusted="192.0.2.1") as c:  # peer not trusted: header ignored
        await _failed_login(c, "untrusted@example.com", "203.0.113.9")

    ips = dict((await db.execute(
        select(AuditLog.diff["email"].astext, AuditLog.ip).where(AuditLog.action == LOGIN_FAILED_ACTION)
    )).all())
    assert ips == {"trusted@example.com": "203.0.113.9", "untrusted@example.com": "10.0.0.1"}


async def test_login_rate_limit_by_ip_is_per_client_behind_proxy(app, settings):
    """Behind the proxy every request arrives from the same peer; the IP limit must still be per client."""
    limited = settings.model_copy(update={"login_rate_limit_attempts": 3})
    app.dependency_overrides[get_settings] = lambda: limited
    async with _behind_proxy(app, trusted="10.0.0.1") as c:
        for n in range(3):  # different emails: only the IP limit can trigger
            assert await _failed_login(c, f"victim{n}@example.com", "203.0.113.9") == 401
        assert await _failed_login(c, "victim9@example.com", "203.0.113.9") == 429
        assert await _failed_login(c, "victim9@example.com", "198.51.100.7") == 401  # another client


async def test_login_validation_error_shape(client):
    r = await client.post("/api/auth/login", json={"email": "x"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"
