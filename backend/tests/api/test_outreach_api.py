"""Phase 8.1: SMTP accounts (password only in the secret store), «Send» of approved offers, the dispatcher
(one message per account, cooldown, daily limit), the send job with every guardrail re-checked (content /
approval / opt-out since queueing), errors (bounce → suppression, transient → retry, login → account error,
auto-pause), crash safety (never re-sent), manual channels, cancel, reply / opt-out events, roles."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import factories as f
import pytest
from conftest import add_member, csrf_headers, login
from sqlalchemy import func, select

from app.api.routes.outreach import get_email_transport
from app.core.crypto import SecretCipher
from app.core.errors import IntegrationError, IntegrationErrorCode
from app.core.secrets import SecretStore
from app.core.state_machine import LeadStatus
from app.domains.analysis.audit import SUBSCORES
from app.domains.contacts.models import SuppressionEntry, SuppressionReason
from app.domains.identity.models import Secret, WorkspaceRole
from app.domains.jobs.models import JobRun
from app.domains.leads.models import Lead
from app.domains.media.models import ImageSource
from app.domains.media.service import store_image
from app.domains.offers.models import Offer
from app.domains.offers.pipeline import run_offer_generation
from app.domains.outreach.dispatch import dispatch_due, send_message
from app.domains.outreach.models import AccountStatus, EmailAccount, Message, MessageStatus
from app.providers.ai import StructuredResponse
from app.providers.ai.base import Usage
from app.providers.outreach import MockEmailTransport, RecipientRejectedError

BODY = "Здравствуйте! Посмотрел канал Alpha: на превью «Видео 601» мелкий текст. Сделал образец — посмотрите?"
PASSWORD = "smtp-pass-123"


def _draft() -> dict:
    return {
        "subject": "Идея для превью Alpha",
        "body": BODY,
        "cta": "Посмотрите?",
        "structure": {k: "…" for k in ("observation", "problem", "insight", "value", "free_sample", "cta")},
        "facts_used": [1],
        "claims": [],
    }


class ScriptedText:
    name = "mock"
    is_mock = True

    async def list_models(self):
        return []

    async def generate_images(self, request):  # pragma: no cover
        raise AssertionError

    async def generate_structured(self, request):
        return StructuredResponse(
            text=json.dumps(_draft(), ensure_ascii=False),
            refusal=None,
            usage=Usage(10, 10),
            model=request.model,
        )


@pytest.fixture
def sessions(db):
    @asynccontextmanager
    async def shared():
        yield db

    return shared


@pytest.fixture
def secrets(settings):
    return SecretStore(SecretCipher(settings.master_key.get_secret_value()))


@pytest.fixture
def transport(app):
    t = MockEmailTransport()
    app.dependency_overrides[get_email_transport] = lambda: t
    return t


@pytest.fixture
async def auth(client, owner, settings):
    await login(client, "owner@example.com")
    return csrf_headers(client, settings)


@pytest.fixture
async def world(db, owner, storage, settings, monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "mock")
    ws = owner[1]
    p = await f.project(db, ws.id, "Outreach")
    alpha = await f.channel(db, projects=[p], title="Alpha")
    t = await f.prompt_template(db)
    a = await store_image(db, storage, f.noise_png(601), ImageSource.YOUTUBE_THUMBNAIL)
    await f.video(db, alpha, asset=a, title="Видео 601")
    await f.thumbnail_analysis(db, t, a, codes=("small_text",), **{k: 3 for k in SUBSCORES})
    return {"ws": ws, "alpha": alpha}


async def _account(client, auth, **over) -> dict:
    body = {
        "name": "Main",
        "from_email": "me@example.com",
        "from_name": "Петя",
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_username": "me@example.com",
        "password": PASSWORD,
    } | over
    r = await client.post("/api/email-accounts", headers=auth, json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def _approved(
    client, db, sessions, settings, world, auth, *, contact_type="email", value="author@example.org"
) -> dict:
    """An approved offer of Alpha's lead with a usable contact."""
    assert (await client.post("/api/leads/sync", headers=auth, json={})).status_code == 200
    lead = await db.scalar(select(Lead).where(Lead.channel_id == world["alpha"].id))
    e = (
        await client.post(f"/api/leads/{lead.id}/offer/estimate", headers=auth, json={"variant_count": 1})
    ).json()
    o = (
        await client.post(
            f"/api/leads/{lead.id}/offers",
            headers=auth,
            json={"variant_count": 1, "confirm_token": e["confirm_token"]},
        )
    ).json()["offer"]
    job = await db.get(JobRun, (await db.get(Offer, o["id"])).job_run_id)

    async def progress(done, total):
        return None

    await run_offer_generation(
        sessions,
        ScriptedText(),
        settings,
        job_run_id=job.id,
        workspace_id=job.workspace_id,
        params=dict(job.params),
        progress=progress,
        last_attempt=True,
    )
    r = await client.post(
        f"/api/channels/{world['alpha'].id}/contacts",
        headers=auth,
        json={"type": contact_type, "value": value, "status": "verified"},
    )
    assert r.status_code == 201, r.text
    contact = next(c for c in r.json()["contacts"]["items"] if c["is_manual"])
    o = (
        await client.patch(f"/api/offers/{o['id']}", headers=auth, json={"contact_id": contact["id"]})
    ).json()
    r = await client.post(
        f"/api/offers/{o['id']}/decision",
        headers=auth,
        json={"decision": "approve", "content_hash": o["content_hash"]},
    )
    assert r.status_code == 200, r.text
    return r.json() | {"lead_id": lead.id, "contact": contact}


async def _send(client, offer, auth, **extra):
    return await client.post(
        f"/api/offers/{offer['id']}/send", headers=auth, json={"content_hash": offer["content_hash"]} | extra
    )


async def _deliver(db, sessions, storage, secrets, settings, transport, message_id):
    return await send_message(sessions, transport, storage, secrets, settings, message_id)


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------
async def test_account_password_is_encrypted_and_never_returned(client, db, auth, transport, secrets):
    acc = await _account(client, auth)
    assert acc["password_configured"] and acc["password_hint"] == "••••••••"
    assert PASSWORD not in json.dumps(acc) and "password" not in acc
    assert (acc["daily_limit"], acc["cooldown_min_seconds"], acc["cooldown_max_seconds"]) == (20, 60, 180)
    assert "ответьте «нет»" in acc["opt_out_text"] and "reply" in acc["opt_out_text"]
    row = await db.get(EmailAccount, acc["id"])
    secret = await db.get(Secret, row.secret_id)
    assert PASSWORD.encode() not in secret.ciphertext
    assert await secrets.get(db, row.secret_id) == PASSWORD
    listed = (await client.get("/api/email-accounts")).json()
    assert PASSWORD not in json.dumps(listed)

    patched = (
        await client.patch(
            f"/api/email-accounts/{acc['id']}", headers=auth, json={"daily_limit": 5, "password": "new-pass"}
        )
    ).json()
    assert patched["daily_limit"] == 5 and await secrets.get(db, row.secret_id) == "new-pass"
    bad = await client.patch(
        f"/api/email-accounts/{acc['id']}",
        headers=auth,
        json={"cooldown_min_seconds": 500, "cooldown_max_seconds": 10},
    )
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_cooldown"
    for body in ({"from_email": "not-an-email"}, {"daily_limit": 501}, {"smtp_port": 0}, {"x": 1}):
        assert (
            await client.post(
                "/api/email-accounts",
                headers=auth,
                json={"name": "B", "from_email": "b@example.com", "smtp_host": "h"} | body,
            )
        ).status_code == 422, body


async def test_verify_pause_and_resume(client, db, auth, transport):
    acc = await _account(client, auth)
    ok = (await client.post(f"/api/email-accounts/{acc['id']}/verify", headers=auth)).json()
    assert ok["ok"] and ok["transport"] == "mock" and transport.verified == 1 and not transport.sent
    transport.script.append(
        IntegrationError(IntegrationErrorCode.AUTH_EXPIRED, "bad login", retryable=False, provider="smtp")
    )
    bad = (await client.post(f"/api/email-accounts/{acc['id']}/verify", headers=auth)).json()
    assert not bad["ok"] and bad["error_code"] == "auth_expired"
    paused = (await client.post(f"/api/email-accounts/{acc['id']}/pause", headers=auth)).json()
    assert paused["status"] == "paused"
    resumed = (await client.post(f"/api/email-accounts/{acc['id']}/resume", headers=auth)).json()
    assert resumed["status"] == "active" and resumed["consecutive_failures"] == 0


# ---------------------------------------------------------------------------
# Send → dispatch → deliver
# ---------------------------------------------------------------------------
async def test_send_dispatch_and_deliver(
    client, db, sessions, storage, secrets, settings, world, auth, transport
):
    acc = await _account(client, auth)
    o = await _approved(client, db, sessions, settings, world, auth)
    stale = await client.post(f"/api/offers/{o['id']}/send", headers=auth, json={"content_hash": "0" * 64})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "content_changed"

    r = await _send(client, o, auth)
    assert r.status_code == 202, r.text
    m = r.json()
    assert (m["status"], m["channel"], m["to_address"], m["email_account_id"]) == (
        "queued",
        "email",
        "author@example.org",
        acc["id"],
    )
    assert m["body"].startswith(BODY) and "ответьте «нет»" in m["body"]  # opt-out footer
    assert m["content_hash"] == o["content_hash"]
    offer = (await client.get(f"/api/offers/{o['id']}")).json()
    assert offer["status"] == "scheduled" and offer["message"]["id"] == m["id"]
    assert (await db.get(Lead, o["lead_id"])).status == LeadStatus.SCHEDULED
    again = await _send(client, offer, auth)
    assert again.status_code == 409  # one live message per offer

    counts = await dispatch_due(db)
    assert counts["enqueued"] == 1
    job = await db.scalar(select(JobRun).where(JobRun.type == "outreach_send"))
    assert job.params == {"message_id": m["id"]} and job.queue == "outreach"
    assert (await dispatch_due(db))["enqueued"] == 0  # the account's cooldown slot is reserved
    account = await db.get(EmailAccount, acc["id"])
    gap = (account.next_send_at - datetime.now(UTC)).total_seconds()
    assert 50 <= gap <= 181

    result = await _deliver(db, sessions, storage, secrets, settings, transport, m["id"])
    assert result["sent"] == m["id"]
    [sent] = transport.sent
    assert (sent.to, sent.from_email, sent.subject) == (
        "author@example.org",
        "me@example.com",
        "Идея для превью Alpha",
    )
    assert sent.text == m["body"] and sent.list_unsubscribe == "mailto:me@example.com?subject=unsubscribe"
    done = (await client.get(f"/api/messages/{m['id']}")).json()
    assert done["status"] == "sent" and done["sent_at"] and [e["type"] for e in done["events"]] == ["sent"]
    assert (await client.get(f"/api/offers/{o['id']}")).json()["status"] == "sent"
    assert (await db.get(Lead, o["lead_id"])).status == LeadStatus.SENT
    # a repeated job never sends twice
    assert (await _deliver(db, sessions, storage, secrets, settings, transport, m["id"]))["skipped"]
    assert len(transport.sent) == 1
    acc_out = (await client.get("/api/email-accounts")).json()[0]
    assert acc_out["sent_today"] == 1 and acc_out["remaining_today"] == 19


async def test_send_needs_an_account_and_an_approval(client, db, sessions, settings, world, auth, transport):
    o = await _approved(client, db, sessions, settings, world, auth)
    no_acc = await _send(client, o, auth)
    assert no_acc.status_code == 409 and no_acc.json()["error"]["code"] == "email_account_required"
    acc = await _account(client, auth)
    await client.post(f"/api/email-accounts/{acc['id']}/pause", headers=auth)
    paused = await _send(client, o, auth, email_account_id=acc["id"])
    assert paused.status_code == 409 and paused.json()["error"]["code"] == "email_account_inactive"
    await client.post(f"/api/email-accounts/{acc['id']}/resume", headers=auth)
    # back in the queue (not approved): nothing can be sent
    e = (await client.patch(f"/api/offers/{o['id']}", headers=auth, json={"cta": "Интересно?"})).json()
    r = await _send(client, e, auth)
    assert r.status_code == 409 and r.json()["error"]["code"] == "offer_status"
    assert await db.scalar(select(func.count()).select_from(Message)) == 0


async def test_opt_out_after_queueing_stops_the_send(
    client, db, sessions, storage, secrets, settings, world, auth, transport
):
    await _account(client, auth)
    o = await _approved(client, db, sessions, settings, world, auth)
    m = (await _send(client, o, auth)).json()
    r = await client.patch(f"/api/contacts/{o['contact']['id']}", headers=auth, json={"status": "opted_out"})
    assert r.status_code == 200
    result = await _deliver(db, sessions, storage, secrets, settings, transport, m["id"])
    assert result["cancelled"] == "offer_not_sendable" and "contact_unusable" in result["blockers"]
    assert transport.sent == []
    msg = (await client.get(f"/api/messages/{m['id']}")).json()
    assert msg["status"] == "cancelled" and msg["events"][-1]["type"] == "cancelled"
    assert (await client.get(f"/api/offers/{o['id']}")).json()["status"] == "approved"


async def test_bounce_suppresses_the_address(
    client, db, sessions, storage, secrets, settings, world, auth, transport
):
    await _account(client, auth)
    o = await _approved(client, db, sessions, settings, world, auth)
    m = (await _send(client, o, auth)).json()
    transport.script.append(RecipientRejectedError("no such user", provider="smtp", smtp_code=550))
    result = await _deliver(db, sessions, storage, secrets, settings, transport, m["id"])
    assert result == {"failed": m["id"], "error": "invalid_input", "bounce": True}
    msg = (await client.get(f"/api/messages/{m['id']}")).json()
    assert msg["status"] == "failed" and msg["events"][-1]["type"] == "bounced"
    entry = await db.scalar(
        select(SuppressionEntry).where(SuppressionEntry.value_normalized == "author@example.org")
    )
    assert entry.reason == SuppressionReason.BOUNCE and entry.source_message_id == m["id"]
    offer = (await client.get(f"/api/offers/{o['id']}")).json()
    assert offer["status"] == "approved" and "contact_unusable" in offer["blockers"]
    assert (await db.get(Lead, o["lead_id"])).status == LeadStatus.FAILED
    # a bounce is not the account's fault
    assert (await db.scalar(select(EmailAccount))).consecutive_failures == 0


async def test_transient_errors_retry_then_fail_and_autopause(
    client, db, sessions, storage, secrets, settings, world, auth, transport, monkeypatch
):
    monkeypatch.setattr(settings, "outreach_max_attempts", 2)
    monkeypatch.setattr(settings, "outreach_autopause_failures", 2)
    acc = await _account(client, auth)
    o = await _approved(client, db, sessions, settings, world, auth)
    m = (await _send(client, o, auth)).json()
    flaky = IntegrationError(
        IntegrationErrorCode.PROVIDER_UNAVAILABLE, "down", retryable=True, provider="smtp"
    )
    transport.script.extend([flaky, flaky])
    first = await _deliver(db, sessions, storage, secrets, settings, transport, m["id"])
    assert first == {"retry": m["id"], "attempt": 1, "error": "provider_unavailable"}
    row = await db.get(Message, m["id"])
    assert row.status == MessageStatus.QUEUED and row.next_attempt_at > datetime.now(UTC)
    assert (await dispatch_due(db, datetime.now(UTC)))["enqueued"] == 0  # waits for next_attempt_at
    second = await _deliver(db, sessions, storage, secrets, settings, transport, m["id"])
    assert second["failed"] == m["id"] and not second["bounce"]
    account = await db.get(EmailAccount, acc["id"])
    await db.refresh(account)
    assert account.status == AccountStatus.PAUSED and "автопауза" in account.paused_reason
    assert transport.sent == []


async def test_login_error_puts_the_account_in_error(
    client, db, sessions, storage, secrets, settings, world, auth, transport
):
    acc = await _account(client, auth)
    o = await _approved(client, db, sessions, settings, world, auth)
    m = (await _send(client, o, auth)).json()
    transport.script.append(
        IntegrationError(IntegrationErrorCode.AUTH_EXPIRED, "535", retryable=False, provider="smtp")
    )
    assert (await _deliver(db, sessions, storage, secrets, settings, transport, m["id"]))["failed"] == m["id"]
    account = await db.get(EmailAccount, acc["id"])
    await db.refresh(account)
    assert account.status == AccountStatus.ERROR
    # after fixing the account the offer can be sent again (a new message; the failed one stays history)
    await client.post(f"/api/email-accounts/{acc['id']}/resume", headers=auth)
    offer = (await client.get(f"/api/offers/{o['id']}")).json()
    assert (await _send(client, offer, auth)).status_code == 202


async def test_a_crash_while_sending_is_never_resent(
    client, db, sessions, storage, secrets, settings, world, auth, transport
):
    await _account(client, auth)
    o = await _approved(client, db, sessions, settings, world, auth)
    m = (await _send(client, o, auth)).json()
    row = await db.get(Message, m["id"])
    row.status = MessageStatus.SENDING  # the worker died after handing it to the server
    await db.flush()
    result = await _deliver(db, sessions, storage, secrets, settings, transport, m["id"])
    assert result == {"failed": "send_interrupted"} and transport.sent == []
    assert (await client.get(f"/api/messages/{m['id']}")).json()["error_code"] == "send_interrupted"


async def test_daily_limit_holds_the_queue(
    client, db, sessions, storage, secrets, settings, world, auth, transport
):
    acc = await _account(client, auth, daily_limit=1)
    o = await _approved(client, db, sessions, settings, world, auth)
    m = (await _send(client, o, auth)).json()
    other = Message(
        **{
            k: getattr(await db.get(Message, m["id"]), k)
            for k in (
                "workspace_id",
                "lead_id",
                "offer_id",
                "channel",
                "email_account_id",
                "contact_type",
                "to_address",
                "subject",
                "body",
                "content_hash",
            )
        },
        idempotency_key="f" * 64,
        status=MessageStatus.SENT,
        scheduled_at=datetime.now(UTC),
        sent_at=datetime.now(UTC),
    )
    db.add(other)
    await db.flush()
    counts = await dispatch_due(db)
    assert counts == {"accounts": 1, "enqueued": 0, "limit_reached": 1}
    account = await db.get(EmailAccount, acc["id"])
    assert account.next_send_at is None


async def test_scheduled_message_waits_for_its_time(client, db, sessions, settings, world, auth, transport):
    await _account(client, auth)
    o = await _approved(client, db, sessions, settings, world, auth)
    later = datetime.now(UTC) + timedelta(hours=2)
    s = (
        await client.post(
            f"/api/offers/{o['id']}/schedule", headers=auth, json={"scheduled_at": later.isoformat()}
        )
    ).json()
    assert s["message"]["status"] == "queued"
    assert (await dispatch_due(db))["enqueued"] == 0
    assert (await dispatch_due(db, later + timedelta(minutes=1)))["enqueued"] == 1


# ---------------------------------------------------------------------------
# Manual channels, cancel, events
# ---------------------------------------------------------------------------
async def test_telegram_is_sent_by_the_operator(client, db, sessions, settings, world, auth, transport):
    o = await _approved(
        client, db, sessions, settings, world, auth, contact_type="telegram", value="@alpha_tg"
    )
    assert o["channel"] == "telegram"
    m = (await _send(client, o, auth)).json()
    assert (m["status"], m["to_address"], m["email_account_id"]) == ("manual", "@alpha_tg", None)
    assert m["body"] == BODY  # no email footer
    assert (await dispatch_due(db))["enqueued"] == 0
    sent = (
        await client.post(f"/api/messages/{m['id']}/mark-sent", headers=auth, json={"note": "отправил"})
    ).json()
    assert sent["status"] == "sent" and sent["events"][0]["payload"] == {"manual": True}
    assert (await client.get(f"/api/offers/{o['id']}")).json()["status"] == "sent"
    again = await client.post(f"/api/messages/{m['id']}/mark-sent", headers=auth, json={})
    assert again.status_code == 409


async def test_cancel_keeps_the_offer_approved(client, db, sessions, settings, world, auth, transport):
    await _account(client, auth)
    o = await _approved(client, db, sessions, settings, world, auth)
    m = (await _send(client, o, auth)).json()
    c = (await client.post(f"/api/messages/{m['id']}/cancel", headers=auth)).json()
    assert c["status"] == "cancelled"
    offer = (await client.get(f"/api/offers/{o['id']}")).json()
    assert offer["status"] == "approved" and offer["scheduled_at"] is None
    assert (await db.get(Lead, o["lead_id"])).status == LeadStatus.APPROVED
    # cancelled is not «live»: the same text can be queued again
    assert (await _send(client, offer, auth)).status_code == 202


async def test_reply_and_opt_out_events(client, db, sessions, settings, world, auth, transport):
    o = await _approved(
        client, db, sessions, settings, world, auth, contact_type="telegram", value="@alpha_tg"
    )
    m = (await _send(client, o, auth)).json()
    early = await client.post(f"/api/messages/{m['id']}/events", headers=auth, json={"type": "replied"})
    assert early.status_code == 409  # not sent yet
    await client.post(f"/api/messages/{m['id']}/mark-sent", headers=auth, json={})
    r = await client.post(
        f"/api/messages/{m['id']}/events", headers=auth, json={"type": "replied", "note": "ответил"}
    )
    assert r.status_code == 200 and (await db.get(Lead, o["lead_id"])).status == LeadStatus.REPLIED
    opt = (
        await client.post(f"/api/messages/{m['id']}/events", headers=auth, json={"type": "opt_out"})
    ).json()
    assert [e["type"] for e in opt["events"]] == ["sent", "replied", "opt_out"]
    lead = await db.get(Lead, o["lead_id"])
    await db.refresh(lead)
    assert lead.status == LeadStatus.OPTED_OUT
    entry = await db.scalar(select(SuppressionEntry).where(SuppressionEntry.value_normalized == "@alpha_tg"))
    assert entry.reason == SuppressionReason.OPT_OUT and entry.source_message_id == m["id"]
    listed = (await client.get("/api/messages", params={"status": "sent"})).json()
    assert listed["total"] == 1 and listed["counts"]["sent"] == 1


# ---------------------------------------------------------------------------
# Roles and isolation
# ---------------------------------------------------------------------------
async def test_roles_and_isolation(
    client, db, sessions, settings, owner, world, auth, transport, other_owner
):
    acc = await _account(client, auth)
    o = await _approved(client, db, sessions, settings, world, auth)
    m = (await _send(client, o, auth)).json()
    await add_member(db, owner[1], "op@example.com", WorkspaceRole.OPERATOR)
    await client.post("/api/auth/logout", headers=auth)
    await login(client, "op@example.com")
    op = csrf_headers(client, settings)
    assert (await client.get("/api/email-accounts")).status_code == 200
    assert (
        await client.post(
            "/api/email-accounts",
            headers=op,
            json={"name": "x", "from_email": "x@example.com", "smtp_host": "h"},
        )
    ).status_code == 403
    assert (await client.post(f"/api/email-accounts/{acc['id']}/pause", headers=op)).status_code == 403
    assert (
        await client.post(f"/api/messages/{m['id']}/cancel", headers=op)
    ).status_code == 200  # operator sends

    await client.post("/api/auth/logout", headers=op)
    await login(client, "other@example.com")
    other = csrf_headers(client, settings)
    assert (await client.get("/api/email-accounts")).json() == []
    assert (await client.get(f"/api/messages/{m['id']}")).status_code == 404
    assert (await client.get("/api/messages")).json()["total"] == 0
    assert (
        await client.post(
            f"/api/offers/{o['id']}/send", headers=other, json={"content_hash": o["content_hash"]}
        )
    ).status_code == 404
