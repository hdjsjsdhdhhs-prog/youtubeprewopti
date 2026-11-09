"""Phase 8.2: campaigns — setup and readiness, adding leads (one active campaign per lead, balanced A/B
styles), offer generation (estimate → confirm → one draft per lead in its style, bulk queue), the run
(approved offers queued once, sent within the window and limits, outcomes synced, auto-complete), pause /
resume, auto-pause after failures, complete / archive (queued messages cancelled), A/B counts, roles and
isolation. Every offer is still approved one by one in the Approval Queue."""

from __future__ import annotations

import itertools
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import factories as f
import pytest
from conftest import add_member, csrf_headers, login
from sqlalchemy import func, select

from app.api.routes.outreach import get_email_transport
from app.core.crypto import SecretCipher
from app.core.secrets import SecretStore
from app.domains.campaigns.models import Campaign, CampaignLead, CampaignLeadStatus, CampaignStatus
from app.domains.campaigns.service import tick
from app.domains.identity.models import WorkspaceRole
from app.domains.jobs.models import JobRun
from app.domains.leads.models import Lead
from app.domains.offers.models import Offer, OfferVariant
from app.domains.offers.pipeline import run_offer_generation
from app.domains.outreach.dispatch import dispatch_due, send_message
from app.domains.outreach.models import Message, MessageStatus
from app.providers.ai import StructuredResponse
from app.providers.ai.base import Usage
from app.providers.outreach import MockEmailTransport, RecipientRejectedError

# distinct texts (no digits: the guard flags numbers that are not in the facts; no shared phrases: the
# uniqueness check compares the drafts of different offers)
BODIES = [
    "Здравствуйте! Посмотрел ваш канал: на обложках мелкий текст, его трудно прочитать с телефона. "
    "Сделал бесплатный образец — взглянете?",
    "Привет! Миниатюры роликов теряются в ленте из-за тёмного фона. Набросал вариант поярче, могу прислать.",
    "Добрый день. Заметил, что лица на заставках почти не видно; подготовил пример с крупным планом.",
    "Приветствую, у ваших картинок к видео нет единого стиля — собрал небольшую концепцию оформления.",
]
ALWAYS = {
    "send_days": [1, 2, 3, 4, 5, 6, 7],
    "send_from": "00:00:00",
    "send_to": "23:59:59",
    "timezone": "UTC",
}


class ScriptedText:
    name = "mock"
    is_mock = True

    def __init__(self) -> None:
        self._n = itertools.count()

    async def list_models(self):
        return []

    async def generate_images(self, request):  # pragma: no cover
        raise AssertionError

    async def generate_structured(self, request):
        body = BODIES[next(self._n) % len(BODIES)]
        draft = {
            "subject": "Идея для превью",
            "body": body,
            "cta": "Посмотрите?",
            "structure": {
                k: "…" for k in ("observation", "problem", "insight", "value", "free_sample", "cta")
            },
            "facts_used": [1],
            "claims": [],
        }
        return StructuredResponse(
            text=json.dumps(draft, ensure_ascii=False), refusal=None, usage=Usage(10, 10), model=request.model
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
async def world(db, owner, settings, monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "mock")
    ws = owner[1]
    p = await f.project(db, ws.id, "Campaigns")
    channels = [await f.channel(db, projects=[p], title=t) for t in ("Alpha", "Beta", "Gamma")]
    return {"ws": ws, "project": p, "channels": channels}


async def _account(client, auth, **over) -> dict:
    body = {
        "name": "Main",
        "from_email": "me@example.com",
        "smtp_host": "smtp.example.com",
        "password": "pw",
        "cooldown_min_seconds": 0,
        "cooldown_max_seconds": 0,
    } | over
    r = await client.post("/api/email-accounts", headers=auth, json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def _campaign(client, auth, **over) -> dict:
    r = await client.post("/api/campaigns", headers=auth, json={"name": "Осень", **ALWAYS} | over)
    assert r.status_code == 201, r.text
    return r.json()


async def _leads(client, db, auth, world, n=2) -> list[int]:
    """Leads of the first ``n`` channels, each with a verified email."""
    assert (await client.post("/api/leads/sync", headers=auth, json={})).status_code == 200
    ids = []
    for i, ch in enumerate(world["channels"][:n]):
        r = await client.post(
            f"/api/channels/{ch.id}/contacts",
            headers=auth,
            json={"type": "email", "value": f"author{i}@example.org", "status": "verified"},
        )
        assert r.status_code == 201, r.text
        ids.append(await db.scalar(select(Lead.id).where(Lead.channel_id == ch.id)))
    return ids


async def _add(client, auth, campaign_id, ids) -> dict:
    r = await client.post(
        f"/api/campaigns/{campaign_id}/leads", headers=auth, json={"selection": {"lead_ids": ids}}
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _generate(client, db, sessions, settings, auth, campaign_id) -> dict:
    est = (await client.post(f"/api/campaigns/{campaign_id}/offers/estimate", headers=auth)).json()
    r = await client.post(
        f"/api/campaigns/{campaign_id}/offers", headers=auth, json={"confirm_token": est["confirm_token"]}
    )
    assert r.status_code == 202, r.text
    started = r.json()
    provider = ScriptedText()

    async def progress(done, total):
        return None

    for job in await db.scalars(select(JobRun).where(JobRun.id.in_(started["job_ids"])).order_by(JobRun.id)):
        await run_offer_generation(
            sessions,
            provider,
            settings,
            job_run_id=job.id,
            workspace_id=job.workspace_id,
            params=dict(job.params),
            progress=progress,
            last_attempt=True,
        )
    return started | {"estimate": est}


async def _offer_of(client, db, lead_id) -> dict:
    offer_id = await db.scalar(select(Offer.id).where(Offer.lead_id == lead_id).order_by(Offer.id.desc()))
    return (await client.get(f"/api/offers/{offer_id}")).json()


async def _approve(client, db, auth, lead_id) -> dict:
    o = await _offer_of(client, db, lead_id)
    r = await client.post(
        f"/api/offers/{o['id']}/decision",
        headers=auth,
        json={"decision": "approve", "content_hash": o["content_hash"]},
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _deliver(sessions, storage, secrets, settings, transport, message_id):
    return await send_message(sessions, transport, storage, secrets, settings, message_id)


async def _ready(
    client, db, sessions, settings, world, auth, *, n=2, approve=True, **campaign
) -> tuple[dict, list]:
    """A campaign with ``n`` leads whose offers are written (and approved)."""
    if "email_account_ids" not in campaign:
        await _account(client, auth)
    c = await _campaign(client, auth, **campaign)
    ids = await _leads(client, db, auth, world, n)
    await _add(client, auth, c["id"], ids)
    await _generate(client, db, sessions, settings, auth, c["id"])
    if approve:
        for lid in ids:
            await _approve(client, db, auth, lid)
    return (await client.get(f"/api/campaigns/{c['id']}")).json(), ids


async def _message(db, lead_id) -> Message | None:
    return await db.scalar(select(Message).where(Message.lead_id == lead_id).order_by(Message.id.desc()))


# ---------------------------------------------------------------------------
# The whole run
# ---------------------------------------------------------------------------
async def test_campaign_run_end_to_end(
    client, db, sessions, storage, secrets, settings, world, auth, transport
):
    acc = await _account(client, auth)
    c = await _campaign(client, auth, styles=["professional", "short"], email_account_ids=[acc["id"]])
    assert (c["status"], c["issues"], c["styles"], c["timezone"]) == (
        "draft",
        ["no_leads"],
        ["professional", "short"],
        "UTC",
    )
    assert [a["id"] for a in c["email_accounts"]] == [acc["id"]] and c["in_window_now"]
    ids = await _leads(client, db, auth, world)
    added = await _add(client, auth, c["id"], ids)
    assert (added["matched"], added["added"]) == (2, 2)
    assert added["styles"] == {"professional": 1, "short": 1}  # balanced A/B
    c = (await client.get(f"/api/campaigns/{c['id']}")).json()
    assert (c["status"], c["issues"], c["counts"]["active"], c["counts"]["stages"]) == (
        "ready",
        [],
        2,
        {"no_offer": 2},
    )

    est = (await client.post(f"/api/campaigns/{c['id']}/offers/estimate", headers=auth)).json()
    assert sorted(est["lead_ids"]) == sorted(ids) and est["styles"] == {"professional": 1, "short": 1}
    assert est["skipped"] == {
        "no_contact": 0,
        "lead_status": 0,
        "generation_running": 0,
        "offer_generating": 0,
    }
    bad = await client.post(f"/api/campaigns/{c['id']}/offers", headers=auth, json={"confirm_token": "x"})
    assert bad.status_code == 409 and bad.json()["error"]["code"] == "confirmation_required"
    started = await _generate(client, db, sessions, settings, auth, c["id"])
    assert started["started"] == 2 and started["campaign"]["status"] == "pending_approval"
    jobs = list(await db.scalars(select(JobRun).where(JobRun.id.in_(started["job_ids"]))))
    assert {(j.type, j.queue) for j in jobs} == {("offer_generation", "bulk")}
    styles = {
        cl.lead_id: cl.style.value
        for cl in await db.scalars(select(CampaignLead).where(CampaignLead.campaign_id == c["id"]))
    }
    for lid in ids:
        o = await _offer_of(client, db, lid)
        assert o["status"] == "pending_approval" and len(o["variants"]) == 1
        assert o["variants"][0]["style"] == styles[lid]
        assert o["variants"][0]["label"] == {"professional": "A", "short": "B"}[styles[lid]]
        assert o["campaign"] == {
            "id": c["id"],
            "name": "Осень",
            "status": "pending_approval",
            "style": styles[lid],
            "label": o["variants"][0]["label"],
            "active": True,
        }
        assert o["channel"] == "email" and o["contact"]["type"] == "email"
    queue = (await client.get("/api/approval-queue", params={"campaign_id": c["id"]})).json()
    assert queue["total"] == 2
    # nothing goes out before the launch, even approved
    await _approve(client, db, auth, ids[0])
    assert (await tick(db))["queued"] == 0
    assert await _message(db, ids[0]) is None

    run = (await client.post(f"/api/campaigns/{c['id']}/start", headers=auth)).json()
    assert run["status"] == "running" and run["started_at"]
    m1 = await _message(db, ids[0])
    assert (m1.status, m1.campaign_id, m1.email_account_id) == (MessageStatus.QUEUED, c["id"], acc["id"])
    listed = (await client.get("/api/messages", params={"campaign_id": c["id"]})).json()
    assert listed["total"] == 1 and listed["items"][0]["campaign_name"] == "Осень"
    assert (await dispatch_due(db))["enqueued"] == 1
    assert (await _deliver(sessions, storage, secrets, settings, transport, m1.id))["sent"] == m1.id
    assert (await tick(db))["sent"] == 1
    assert (await db.get(Campaign, c["id"])).status == CampaignStatus.RUNNING  # one lead still in review

    await _approve(client, db, auth, ids[1])
    assert (await tick(db))["queued"] == 1
    m2 = await _message(db, ids[1])
    assert (await dispatch_due(db))["enqueued"] == 1
    assert (await _deliver(sessions, storage, secrets, settings, transport, m2.id))["sent"] == m2.id
    assert len(transport.sent) == 2
    done = await tick(db)
    assert (done["sent"], done["completed"]) == (1, 1)
    c = (await client.get(f"/api/campaigns/{c['id']}")).json()
    assert c["status"] == "completed" and c["completed_at"]
    assert (c["counts"]["sent"], c["counts"]["active"], c["sent_today"]) == (2, 0, 2)

    r = await client.post(f"/api/messages/{m1.id}/events", headers=auth, json={"type": "positive_reply"})
    assert r.status_code == 200
    stats = (await client.get(f"/api/campaigns/{c['id']}/stats")).json()
    by = {v["style"]: v for v in stats["variants"]}
    replied_style = styles[ids[0]]
    other_style = styles[ids[1]]
    assert by[replied_style]["sent"] == 1
    assert (
        by[replied_style]["replied"],
        by[replied_style]["positive_reply"],
        by[replied_style]["reply_rate"],
    ) == (
        1,
        1,
        "1.0000",
    )
    assert (by[other_style]["sent"], by[other_style]["replied"], by[other_style]["no_reply"]) == (1, 0, 1)
    assert all(v["small_sample"] for v in stats["variants"]) and stats["min_sample"] == 30
    assert "не выводы" in stats["note"]
    leads = (await client.get(f"/api/campaigns/{c['id']}/leads")).json()
    assert {i["lead_id"]: i["stage"] for i in leads["items"]} == {ids[0]: "replied", ids[1]: "sent"}
    assert (await client.get(f"/api/campaigns/{c['id']}")).json()["counts"]["replied"] == 1


# ---------------------------------------------------------------------------
# Pause, window, daily limit, auto-pause
# ---------------------------------------------------------------------------
async def test_pause_window_and_daily_limit_hold_the_queue(
    client, db, sessions, storage, secrets, settings, world, auth, transport
):
    c, ids = await _ready(client, db, sessions, settings, world, auth, daily_limit=1)
    await client.post(f"/api/campaigns/{c['id']}/start", headers=auth)
    m1, m2 = await _message(db, ids[0]), await _message(db, ids[1])
    assert m1.status == m2.status == MessageStatus.QUEUED

    paused = (await client.post(f"/api/campaigns/{c['id']}/pause", headers=auth)).json()
    assert paused["status"] == "paused" and paused["paused_reason"]
    counts = await dispatch_due(db)
    assert counts["enqueued"] == 0 and counts["campaign_waiting"] >= 1
    assert (await tick(db))["queued"] == 0  # paused: nothing new is queued either
    # a send job that was already enqueued also waits (re-checked right before the server)
    waiting = await _deliver(sessions, storage, secrets, settings, transport, m1.id)
    assert waiting == {"waiting": "campaign_paused"} and transport.sent == []
    await db.refresh(m1)
    assert m1.status == MessageStatus.QUEUED and m1.error_code == "campaign_paused"

    resumed = (await client.post(f"/api/campaigns/{c['id']}/resume", headers=auth)).json()
    assert resumed["status"] == "running" and resumed["paused_reason"] is None
    assert (await dispatch_due(db))["enqueued"] == 1
    assert (await _deliver(sessions, storage, secrets, settings, transport, m1.id))["sent"] == m1.id
    # the campaign's own daily limit (1) holds the second one although the account has room
    counts = await dispatch_due(db)
    assert counts["enqueued"] == 0 and counts["campaign_waiting"] == 1
    assert (await client.get(f"/api/campaigns/{c['id']}")).json()["sent_today"] == 1

    # outside the window: tomorrow's weekday only
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).isoweekday()
    patched = (
        await client.patch(
            f"/api/campaigns/{c['id']}", headers=auth, json={"daily_limit": 10, "send_days": [tomorrow]}
        )
    ).json()
    assert not patched["in_window_now"]
    assert datetime.fromisoformat(patched["next_window_at"]) > datetime.now(UTC)
    counts = await dispatch_due(db)
    assert counts["enqueued"] == 0 and counts["campaign_waiting"] == 1
    later = datetime.fromisoformat(patched["next_window_at"]) + timedelta(minutes=1)
    assert (await dispatch_due(db, later))["enqueued"] == 1


async def test_failures_in_a_row_pause_the_campaign(
    client, db, sessions, storage, secrets, settings, world, auth, transport
):
    c, ids = await _ready(client, db, sessions, settings, world, auth, n=1, max_consecutive_failures=1)
    await client.post(f"/api/campaigns/{c['id']}/start", headers=auth)
    m = await _message(db, ids[0])
    transport.script.append(RecipientRejectedError("no such user", provider="smtp", smtp_code=550))
    result = await _deliver(sessions, storage, secrets, settings, transport, m.id)
    assert result["failed"] == m.id and result["bounce"] and result["campaign_autopaused"] == c["id"]
    out = (await client.get(f"/api/campaigns/{c['id']}")).json()
    assert out["status"] == "paused" and "автопауза" in out["paused_reason"]
    assert out["consecutive_failures"] == 1
    assert (await tick(db))["failed"] == 1
    [row] = (await client.get(f"/api/campaigns/{c['id']}/leads")).json()["items"]
    assert (row["status"], row["stage"], row["reason"]) == ("failed", "failed", "invalid_input")


# ---------------------------------------------------------------------------
# Leads: one active campaign per lead, skips, removal
# ---------------------------------------------------------------------------
async def test_a_lead_is_active_in_one_campaign_only(client, db, sessions, settings, world, auth, transport):
    await _account(client, auth)
    a = await _campaign(client, auth, name="A", styles=["professional", "short", "free_sample"])
    b = await _campaign(client, auth, name="B")
    ids = await _leads(client, db, auth, world, n=3)
    res = await _add(client, auth, a["id"], ids)
    assert res["added"] == 3 and res["styles"] == {"professional": 1, "short": 1, "free_sample": 1}
    again = await _add(client, auth, a["id"], ids[:1])
    assert (again["added"], again["skipped"]["already_in_campaign"]) == (0, 1)
    other = await _add(client, auth, b["id"], ids)
    assert (other["added"], other["skipped"]["other_campaign"]) == (0, 3)

    removed = await client.delete(f"/api/campaigns/{a['id']}/leads/{ids[0]}", headers=auth)
    assert removed.status_code == 200 and removed.json()["counts"]["removed"] == 1
    assert (await client.delete(f"/api/campaigns/{a['id']}/leads/{ids[0]}", headers=auth)).status_code == 409
    moved = await _add(client, auth, b["id"], ids[:1])
    assert moved["added"] == 1
    # a closed lead is not added
    r = await client.post(f"/api/leads/{ids[1]}/status", headers=auth, json={"status": "rejected"})
    assert r.status_code == 200, r.text
    await client.delete(f"/api/campaigns/{a['id']}/leads/{ids[1]}", headers=auth)
    closed = await _add(client, auth, b["id"], ids[1:2])
    assert closed["skipped"]["lead_status"] == 1
    # by filter («all filtered»)
    c = await _campaign(client, auth, name="C")
    r = await client.post(
        f"/api/campaigns/{c['id']}/leads",
        headers=auth,
        json={"selection": {"filters": {"project_id": world["project"].id}}},
    )
    body = r.json()
    # B holds the first lead, A the third, the second is closed: nothing is free
    assert r.status_code == 200 and body["matched"] >= 2 and body["added"] == 0
    assert sum(body["skipped"].values()) == body["matched"]
    stored = (await client.get(f"/api/campaigns/{c['id']}")).json()["target_filters"]
    assert stored == {"project_id": world["project"].id}
    # the database guarantees it too
    assert (
        await db.scalar(
            select(func.count())
            .select_from(CampaignLead)
            .where(CampaignLead.lead_id == ids[0], CampaignLead.status == CampaignLeadStatus.ACTIVE)
        )
        == 1
    )


async def test_generation_skips_leads_without_a_fitting_contact(
    client, db, sessions, settings, world, auth, transport
):
    await _account(client, auth)
    c = await _campaign(client, auth)
    ids = await _leads(client, db, auth, world, n=2)
    gamma = await db.scalar(select(Lead.id).where(Lead.channel_id == world["channels"][2].id))
    await _add(client, auth, c["id"], [*ids, gamma])  # Gamma has no contact
    est = (await client.post(f"/api/campaigns/{c['id']}/offers/estimate", headers=auth)).json()
    assert sorted(est["lead_ids"]) == sorted(ids) and est["skipped"]["no_contact"] == 1
    # telegram-only campaign: no lead has a Telegram contact
    patched = await client.patch(f"/api/campaigns/{c['id']}", headers=auth, json={"channels": ["telegram"]})
    assert patched.status_code == 200
    est = (await client.post(f"/api/campaigns/{c['id']}/offers/estimate", headers=auth)).json()
    assert est["lead_ids"] == [] and est["skipped"]["no_contact"] == 3
    r = await client.post(
        f"/api/campaigns/{c['id']}/offers", headers=auth, json={"confirm_token": est["confirm_token"]}
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "nothing_to_generate"


async def test_an_existing_open_offer_is_kept(client, db, sessions, settings, world, auth, transport):
    """A lead whose offer was written before keeps it: no second offer, no new cost."""
    await _account(client, auth)
    ids = await _leads(client, db, auth, world, n=1)
    e = (
        await client.post(f"/api/leads/{ids[0]}/offer/estimate", headers=auth, json={"variant_count": 1})
    ).json()
    o = (
        await client.post(
            f"/api/leads/{ids[0]}/offers",
            headers=auth,
            json={"variant_count": 1, "confirm_token": e["confirm_token"]},
        )
    ).json()["offer"]
    c = await _campaign(client, auth)
    await _add(client, auth, c["id"], ids)
    [row] = (await client.get(f"/api/campaigns/{c['id']}/leads")).json()["items"]
    assert (row["offer_id"], row["stage"]) == (o["id"], "drafting")
    est = (await client.post(f"/api/campaigns/{c['id']}/offers/estimate", headers=auth)).json()
    assert est["lead_ids"] == [] and est["skipped"]["offer_generating"] == 1


# ---------------------------------------------------------------------------
# Reject, cancel, complete, archive
# ---------------------------------------------------------------------------
async def test_rejected_and_cancelled_are_not_resent(
    client, db, sessions, storage, secrets, settings, world, auth, transport
):
    c, ids = await _ready(client, db, sessions, settings, world, auth, approve=False)
    await _approve(client, db, auth, ids[0])
    o2 = await _offer_of(client, db, ids[1])
    r = await client.post(f"/api/offers/{o2['id']}/decision", headers=auth, json={"decision": "reject"})
    assert r.status_code == 200
    started = (await client.post(f"/api/campaigns/{c['id']}/start", headers=auth)).json()
    assert started["counts"]["rejected"] == 1  # the launch syncs the outcomes first
    m = await _message(db, ids[0])
    assert (await client.post(f"/api/messages/{m.id}/cancel", headers=auth)).status_code == 200
    assert (await tick(db))["queued"] == 0  # cancelled by a human: not queued again for this approval
    assert await db.scalar(select(func.count()).select_from(Message)) == 1
    rows = {i["lead_id"]: i for i in (await client.get(f"/api/campaigns/{c['id']}/leads")).json()["items"]}
    assert rows[ids[0]]["stage"] == "send_cancelled" and rows[ids[1]]["status"] == "rejected"
    # the operator can still send it by hand; it stays a campaign message
    o1 = await _offer_of(client, db, ids[0])
    sent = await client.post(
        f"/api/offers/{o1['id']}/send", headers=auth, json={"content_hash": o1["content_hash"]}
    )
    assert sent.status_code == 202 and sent.json()["campaign_id"] == c["id"]


async def test_complete_cancels_queued_messages_and_archive_freezes(
    client, db, sessions, settings, world, auth, transport
):
    c, ids = await _ready(client, db, sessions, settings, world, auth)
    later = {"starts_at": (datetime.now(UTC) + timedelta(days=2)).isoformat()}
    assert (await client.patch(f"/api/campaigns/{c['id']}", headers=auth, json=later)).status_code == 200
    await client.post(f"/api/campaigns/{c['id']}/start", headers=auth)
    m = await _message(db, ids[0])
    assert m.scheduled_at > datetime.now(UTC) + timedelta(days=1)  # queued for the first window
    assert (await dispatch_due(db))["enqueued"] == 0
    done = (await client.post(f"/api/campaigns/{c['id']}/complete", headers=auth)).json()
    assert done["status"] == "completed" and done["counts"]["skipped"] == 2
    await db.refresh(m)
    assert m.status == MessageStatus.CANCELLED
    o = await _offer_of(client, db, ids[0])
    assert o["status"] == "approved" and o["campaign"]["active"] is False
    assert (await client.post(f"/api/campaigns/{c['id']}/start", headers=auth)).status_code == 409
    archived = (await client.post(f"/api/campaigns/{c['id']}/archive", headers=auth)).json()
    assert archived["status"] == "archived"
    r = await client.patch(f"/api/campaigns/{c['id']}", headers=auth, json={"name": "x"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "campaign_status"
    listed = (await client.get("/api/campaigns")).json()
    assert listed["items"] == [] and listed["counts"]["archived"] == 1
    assert len((await client.get("/api/campaigns", params={"include_archived": True})).json()["items"]) == 1
    # released: the leads can join another campaign
    other = await _campaign(client, auth, name="Next")
    assert (await _add(client, auth, other["id"], ids))["added"] == 2


async def test_start_needs_leads_and_a_sender(client, db, sessions, settings, world, auth, transport):
    c = await _campaign(client, auth)
    ids = await _leads(client, db, auth, world, n=1)
    await _add(client, auth, c["id"], ids)
    c = (await client.get(f"/api/campaigns/{c['id']}")).json()
    assert c["status"] == "draft" and c["issues"] == ["email_account_required"]
    r = await client.post(f"/api/campaigns/{c['id']}/start", headers=auth)
    assert r.status_code == 409 and r.json()["error"]["code"] == "campaign_not_ready"
    assert r.json()["error"]["details"]["issues"] == ["email_account_required"]
    await _account(client, auth)
    assert (await client.get(f"/api/campaigns/{c['id']}")).json()["issues"] == []
    await tick(db)  # the minute tick re-evaluates draft ⇄ ready
    assert (await db.get(Campaign, c["id"])).status == CampaignStatus.READY
    assert (await client.post(f"/api/campaigns/{c['id']}/start", headers=auth)).json()["status"] == "running"
    manual = await _campaign(client, auth, name="Вручную", channels=["manual"])
    assert manual["issues"] == ["no_leads"]  # no email account needed


# ---------------------------------------------------------------------------
# Validation, roles, isolation
# ---------------------------------------------------------------------------
async def test_validation(client, auth):
    for body in (
        {"timezone": "Mars/Olympus"},
        {"styles": ["short", "short"]},
        {"styles": []},
        {"channels": []},
        {"send_days": [0]},
        {"daily_limit": 501},
        {"x": 1},
    ):
        r = await client.post("/api/campaigns", headers=auth, json={"name": "x"} | body)
        assert r.status_code == 422, body
    r = await client.post(
        "/api/campaigns", headers=auth, json={"name": "x", "send_from": "18:00", "send_to": "10:00"}
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_window"
    r = await client.post("/api/campaigns", headers=auth, json={"name": "x", "email_account_ids": [999999]})
    assert r.status_code == 404
    c = (await client.post("/api/campaigns", headers=auth, json={"name": "Defaults"})).json()
    assert (c["timezone"], c["send_days"], c["send_from"], c["send_to"], c["daily_limit"]) == (
        "Europe/Moscow",
        [1, 2, 3, 4, 5],
        "10:00:00",
        "18:00:00",
        20,
    )


async def test_roles_and_isolation(
    client, db, sessions, settings, owner, world, auth, transport, other_owner
):
    c, ids = await _ready(client, db, sessions, settings, world, auth, n=1, approve=False)
    await add_member(db, owner[1], "viewer@example.com", WorkspaceRole.VIEWER)
    await add_member(db, owner[1], "op@example.com", WorkspaceRole.OPERATOR)
    await client.post("/api/auth/logout", headers=auth)

    await login(client, "viewer@example.com")
    viewer = csrf_headers(client, settings)
    assert (await client.get(f"/api/campaigns/{c['id']}")).status_code == 200
    assert (await client.post("/api/campaigns", headers=viewer, json={"name": "v"})).status_code == 403
    assert (await client.post(f"/api/campaigns/{c['id']}/start", headers=viewer)).status_code == 403
    await client.post("/api/auth/logout", headers=viewer)

    await login(client, "op@example.com")
    op = csrf_headers(client, settings)
    assert (await client.post(f"/api/campaigns/{c['id']}/start", headers=op)).json()["status"] == "running"
    assert (await client.post(f"/api/campaigns/{c['id']}/pause", headers=op)).status_code == 200
    await client.post("/api/auth/logout", headers=op)

    await login(client, "other@example.com")
    other = csrf_headers(client, settings)
    assert (await client.get("/api/campaigns")).json()["items"] == []
    assert (await client.get(f"/api/campaigns/{c['id']}")).status_code == 404
    assert (await client.get(f"/api/campaigns/{c['id']}/leads")).status_code == 404
    mine = (await client.post("/api/campaigns", headers=other, json={"name": "Mine"})).json()
    r = await client.post(
        f"/api/campaigns/{mine['id']}/leads", headers=other, json={"selection": {"lead_ids": ids}}
    )
    assert r.status_code == 404  # not their lead
    variants = await db.scalar(select(func.count()).select_from(OfferVariant))
    assert variants == 1
