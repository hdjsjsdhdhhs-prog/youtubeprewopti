"""Phase 7 API: estimate built from the facts, confirmed start, the drafting job (scripted text model),
guard and personalization, edits (MANUALLY_EDITED, approval invalidated), Approve / Reject / Skip /
Schedule, the Approval Queue, templates, roles and workspace isolation."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import factories as f
import pytest
from conftest import add_member, csrf_headers, login
from sqlalchemy import func, select

from app.core.state_machine import LeadStatus
from app.domains.ai.models import AICall
from app.domains.analysis.audit import SUBSCORES
from app.domains.approvals.models import Approval, ApprovalDecision
from app.domains.feedback.models import FeedbackEvent, FeedbackKind
from app.domains.generation.models import GeneratedThumbnail, GenerationRequest, VariantStatus
from app.domains.identity.models import WorkspaceRole
from app.domains.jobs.models import JobRun
from app.domains.leads.models import Lead, LeadStatusHistory
from app.domains.media.models import ImageSource
from app.domains.media.service import store_image
from app.domains.offers.models import Offer, OfferVariant
from app.domains.offers.pipeline import run_offer_generation
from app.providers.ai import StructuredResponse
from app.providers.ai.base import Usage

CLEAN = (
    "Здравствуйте! Посмотрел канал Alpha: на превью «Видео 601» мелкий текст. Сделал образец — посмотрите?"
)


def draft(body: str = CLEAN, *, subject: str = "Идея для превью Alpha", facts=(1, 2), claims=None) -> dict:
    return {
        "subject": subject,
        "body": body,
        "cta": "Посмотрите?",
        "structure": {k: "…" for k in ("observation", "problem", "insight", "value", "free_sample", "cta")},
        "facts_used": list(facts),
        "claims": claims if claims is not None else [{"text": "мелкий текст", "fact_ids": [1]}],
    }


class ScriptedText:
    """Text provider (``mock`` registry rows) answering each structured call from a script."""

    name = "mock"
    is_mock = True

    def __init__(self, *script) -> None:
        self.script = list(script)
        self.requests = []

    async def list_models(self):
        return []

    async def generate_images(self, request):  # pragma: no cover - not used by offers
        raise AssertionError("offers must not generate images")

    async def generate_structured(self, request):
        self.requests.append(request)
        step = self.script.pop(0) if self.script else draft()
        if isinstance(step, BaseException):
            raise step
        text = step if isinstance(step, str) else json.dumps(step, ensure_ascii=False)
        return StructuredResponse(text=text, refusal=None, usage=Usage(100, 50), model=request.model)


@pytest.fixture
async def world(db, owner, storage, settings, monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "mock")
    ws = owner[1]
    p = await f.project(db, ws.id, "Offers")
    alpha = await f.channel(db, projects=[p], title="Alpha", handle="alpha_cook", subscriber_count=12500)
    beta = await f.channel(db, projects=[p], title="Beta")
    t = await f.prompt_template(db)
    videos = []
    for seed, score, codes, days in ((601, 2, ("small_text", "cluttered"), 5), (602, 8, (), 1)):
        a = await store_image(db, storage, f.noise_png(seed), ImageSource.YOUTUBE_THUMBNAIL)
        videos.append(await f.video(db, alpha, asset=a, days_ago=days, title=f"Видео {seed}"))
        await f.thumbnail_analysis(db, t, a, codes=codes, **{k: score for k in SUBSCORES})
    return {"ws": ws, "project": p, "alpha": alpha, "beta": beta, "videos": videos}


@pytest.fixture
async def auth(client, owner, settings):
    await login(client, "owner@example.com")
    return csrf_headers(client, settings)


@pytest.fixture
async def leads(client, db, world, auth):
    assert (await client.post("/api/leads/sync", headers=auth, json={})).status_code == 200
    rows = {lead.channel_id: lead for lead in await db.scalars(select(Lead))}
    return rows[world["alpha"].id], rows[world["beta"].id]


@pytest.fixture
def lead(leads):
    return leads[0]


@pytest.fixture
def sessions(db):
    @asynccontextmanager
    async def shared():
        yield db

    return shared


async def _estimate(client, lead, auth, **body):
    r = await client.post(f"/api/leads/{lead.id}/offer/estimate", headers=auth, json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def _start(client, lead, auth, **body):
    e = await _estimate(client, lead, auth, **body)
    r = await client.post(
        f"/api/leads/{lead.id}/offers", headers=auth, json=body | {"confirm_token": e["confirm_token"]}
    )
    assert r.status_code == 202, r.text
    return r.json()["offer"]


async def _run(db, sessions, settings, offer_id, provider):
    offer = await db.get(Offer, offer_id)
    job = await db.get(JobRun, offer.job_run_id)

    async def progress(done, total):
        return None

    return await run_offer_generation(
        sessions,
        provider,
        settings,
        job_run_id=job.id,
        workspace_id=job.workspace_id,
        params=dict(job.params),
        progress=progress,
        last_attempt=True,
    )


async def _drafted(client, db, sessions, settings, lead, auth, *script, **body):
    """Start an offer and run its job with the scripted drafts; returns the offer as the API shows it."""
    o = await _start(client, lead, auth, **body)
    await _run(db, sessions, settings, o["id"], ScriptedText(*script))
    r = await client.get(f"/api/offers/{o['id']}")
    assert r.status_code == 200, r.text
    return r.json()


async def _email(client, channel, auth, value="author@example.com"):
    r = await client.post(
        f"/api/channels/{channel.id}/contacts",
        headers=auth,
        json={"type": "email", "value": value, "status": "verified"},
    )
    assert r.status_code == 201, r.text
    return next(c for c in r.json()["contacts"]["items"] if c["value_normalized"] == value)


async def _approve(client, offer, auth, **extra):
    return await client.post(
        f"/api/offers/{offer['id']}/decision",
        headers=auth,
        json={"decision": "approve", "content_hash": offer["content_hash"]} | extra,
    )


# ---------------------------------------------------------------------------
# Estimate and start
# ---------------------------------------------------------------------------
async def test_estimate_lists_the_facts_and_the_exact_prompt(client, db, world, lead, auth):
    e = await _estimate(client, lead, auth, instructions="  на «вы»  ")
    facts = {(x["kind"], x["value"]) for x in e["inputs"]["facts"]}
    assert ("channel", "Alpha") in facts and ("channel", "@alpha_cook") in facts
    assert ("channel", "12 500 подписчиков") in facts
    assert ("video", "Видео 601") in facts and ("video", "Видео 602") in facts
    assert any(k == "thumbnail_problem" and "Видео 601" in v for k, v in facts)
    assert [x["id"] for x in e["inputs"]["facts"]] == list(range(1, len(e["inputs"]["facts"]) + 1))
    assert e["inputs"]["instructions"] == "на «вы»" and e["inputs"]["language"] == "ru"
    assert e["inputs"]["sample"] is None and e["samples"] == []
    assert (e["offer_id"], e["round"], e["variant_count"]) == (None, 1, 3)
    assert e["styles"] == ["professional", "short", "free_sample"]
    assert (e["provider"], e["model_key"]) == ("mock", "mock-text") and float(e["estimated_cost_usd"]) == 0
    assert 'Variant A, style "professional"' in e["prompt_preview"] and "F1 [channel]" in e["prompt_preview"]
    assert "Never invent numbers" in e["prompt_preview"]
    assert not list(await db.scalars(select(Offer)))  # nothing was started

    url = f"/api/leads/{lead.id}/offer/estimate"
    for bad in ({"variant_count": 0}, {"variant_count": 4}, {"language": "Russian"}, {"x": 1}):
        assert (await client.post(url, headers=auth, json=bad)).status_code == 422, bad
    assert (await client.post("/api/leads/999999/offer/estimate", headers=auth, json={})).status_code == 404
    assert (await client.post(url, headers=auth, json={"template_id": 999999})).status_code == 404


async def test_start_requires_the_confirmed_estimate(client, db, world, lead, auth):
    url = f"/api/leads/{lead.id}/offers"
    e = await _estimate(client, lead, auth, variant_count=2)
    wrong = await client.post(url, headers=auth, json={"variant_count": 2, "confirm_token": "nope"})
    assert wrong.status_code == 409 and wrong.json()["error"]["code"] == "confirmation_required"
    assert wrong.json()["error"]["details"]["confirm_token"] == e["confirm_token"]
    other = await client.post(
        url, headers=auth, json={"variant_count": 3, "confirm_token": e["confirm_token"]}
    )
    assert other.status_code == 409

    ok = await client.post(url, headers=auth, json={"variant_count": 2, "confirm_token": e["confirm_token"]})
    assert ok.status_code == 202, ok.text
    body = ok.json()
    o = body["offer"]
    assert body["lead_status"] == "offer_draft" and o["status"] == "draft" and o["provider_is_mock"]
    assert [(v["label"], v["style"], v["status"]) for v in o["variants"]] == [
        ("A", "professional", "pending"),
        ("B", "short", "pending"),
    ]
    assert o["blockers"] and not o["can_approve"]
    job = await db.get(JobRun, o["job"]["id"])
    assert (job.type, job.queue, job.progress_total) == ("offer_generation", "interactive", 2)
    assert job.params == {
        "offer_id": o["id"],
        "round": 1,
        "lead_id": lead.id,
        "project_id": world["project"].id,
    }
    assert job.budget_usd == job.estimated_cost_usd == 0
    history = list(
        await db.scalars(
            select(LeadStatusHistory)
            .where(LeadStatusHistory.lead_id == lead.id)
            .order_by(LeadStatusHistory.id)
        )
    )
    assert [(h.from_status, h.to_status) for h in history][-2:] == [
        (LeadStatus.ANALYZED, LeadStatus.SELECTED),
        (LeadStatus.SELECTED, LeadStatus.OFFER_DRAFT),
    ]

    again = await _estimate(client, lead, auth, variant_count=2)
    busy = await client.post(
        url, headers=auth, json={"variant_count": 2, "confirm_token": again["confirm_token"]}
    )
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "offer_generating"


async def test_start_is_refused_for_closed_leads_running_generation_and_without_ai(
    client, db, lead, auth, settings, monkeypatch
):
    e = await _estimate(client, lead, auth)
    url = f"/api/leads/{lead.id}/offers"
    for status_, code in (
        (LeadStatus.REJECTED, "lead_status"),
        (LeadStatus.GENERATION_PENDING, "generation_running"),
    ):
        lead.status = status_
        await db.flush()
        r = await client.post(url, headers=auth, json={"confirm_token": e["confirm_token"]})
        assert r.status_code == 409 and r.json()["error"]["code"] == code
    lead.status = LeadStatus.ANALYZED
    await db.flush()
    monkeypatch.setattr(settings, "ai_provider", None)
    monkeypatch.setattr(settings, "demo_mode", False)
    e2 = await _estimate(client, lead, auth)
    assert e2["model_key"] is None
    r = await client.post(url, headers=auth, json={"confirm_token": e2["confirm_token"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "ai_not_configured"
    assert not list(await db.scalars(select(Offer)))


# ---------------------------------------------------------------------------
# The job → the queue
# ---------------------------------------------------------------------------
async def test_job_writes_drafts_checks_them_and_queues_the_offer(
    client, db, sessions, settings, world, lead, auth
):
    invented = draft("Канал Alpha: ваш CTR упал на 27%, «Видео 601» теряет 4000 просмотров.")
    o = await _drafted(
        client, db, sessions, settings, lead, auth, invented, draft(), draft(CLEAN + " Коротко.")
    )
    assert o["status"] == "pending_approval"
    a, b, c = sorted(o["variants"], key=lambda v: v["label"])
    assert [v["status"] for v in (a, b, c)] == ["ready"] * 3
    # A invents numbers and metrics: not selected; B is the first clean draft
    assert {i["code"] for i in a["guard_issues"]} == {"unsupported_number", "metric_claim"}
    assert {i["text"] for i in a["guard_issues"] if i["code"] == "unsupported_number"} == {"27", "4000"}
    assert b["guard_issues"] == [] and b["is_selected"] and o["selected_variant_id"] == b["id"]
    assert float(b["personalization_score"]) > 0 and o["personalization_score"] == b["personalization_score"]
    assert {s["fact_id"] for s in b["personalization_sources"]} == {1, 2}
    assert b["structure"]["observation"] == "…" and b["claims"][0]["fact_ids"] == [1]
    assert (
        await db.scalar(select(func.count()).select_from(AICall).where(AICall.task == "offer_generation"))
    ) == 3
    assert (await db.get(Lead, lead.id)).status == LeadStatus.PENDING_APPROVAL
    # no contact yet: cannot be approved
    assert set(o["blockers"]) == {"contact_required", "channel_required"} and not o["can_approve"]

    q = (await client.get("/api/approval-queue")).json()
    assert q["total"] == 1 and q["counts"]["pending_approval"] == 1
    card = q["items"][0]
    assert card["offer"]["id"] == o["id"] and card["channel"]["title"] == "Alpha"
    assert card["lead"]["id"] == lead.id and card["lead"]["lead_score"] is not None
    assert card["thumbnail"]["video_id"] == world["videos"][0].id  # the weakest thumbnail
    assert set(card["thumbnail"]["problem_codes"]) == {"small_text", "cluttered"}
    assert card["analysis"]["problem_codes"] and card["contacts"] == []
    assert (await client.get("/api/approval-queue", params={"status": "approved"})).json()["total"] == 0
    assert (await client.get("/api/approval-queue", params={"status": "bogus"})).status_code == 422


async def test_failed_drafts_mark_the_offer_failed(client, db, sessions, settings, lead, auth):
    o = await _drafted(client, db, sessions, settings, lead, auth, "not json", "{}", variant_count=1)
    assert o["status"] == "failed" and o["error"]
    assert o["variants"][0]["status"] == "failed"
    assert (await db.get(Lead, lead.id)).status == LeadStatus.OFFER_DRAFT
    # «Regenerate» reopens a failed offer with a new round
    e = await _estimate(client, lead, auth, variant_count=1)
    assert (e["offer_id"], e["round"]) == (o["id"], 2)


# ---------------------------------------------------------------------------
# Approve / edit / schedule
# ---------------------------------------------------------------------------
async def test_approve_needs_a_usable_contact_the_current_hash_and_a_clean_text(
    client, db, sessions, settings, owner, world, lead, auth
):
    o = await _drafted(client, db, sessions, settings, lead, auth, draft("Ваш CTR — 2%."), draft())
    contact = await _email(client, world["alpha"], auth)
    r = await client.patch(f"/api/offers/{o['id']}", headers=auth, json={"contact_id": contact["id"]})
    assert r.status_code == 200, r.text
    o = r.json()
    assert o["contact"]["id"] == contact["id"] and o["channel"] == "email" and o["can_approve"]
    assert not o["manually_edited"]  # choosing a contact is not an edit of the text

    stale = await client.post(
        f"/api/offers/{o['id']}/decision",
        headers=auth,
        json={"decision": "approve", "content_hash": "0" * 64},
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "content_changed"
    missing = await client.post(f"/api/offers/{o['id']}/decision", headers=auth, json={"decision": "approve"})
    assert missing.status_code == 422

    ok = await _approve(client, o, auth, comment="ок")
    assert ok.status_code == 200, ok.text
    approved = ok.json()
    assert approved["status"] == "approved" and approved["approval"]["decision"] == "approved"
    assert approved["approval"]["is_current"] and approved["approval"]["content_hash"] == o["content_hash"]
    assert (await db.get(Lead, lead.id)).status == LeadStatus.APPROVED
    [row] = list(await db.scalars(select(Approval)))
    assert (row.decision, row.target_id, row.user_id) == (ApprovalDecision.APPROVED, o["id"], owner[0].id)
    ev = await db.scalar(select(FeedbackEvent).where(FeedbackEvent.kind == FeedbackKind.OFFER_DECISION))
    assert ev.payload["decision"] == "approve" and ev.payload["label"] == "B" and ev.ai_value is not None

    # selecting the draft with a guard issue changes the content: back to the queue, cannot be approved
    a = next(v for v in approved["variants"] if v["label"] == "A")
    r = await client.patch(f"/api/offers/{o['id']}", headers=auth, json={"variant_id": a["id"]})
    back = r.json()
    assert back["status"] == "pending_approval" and not back["approval"]["is_current"]
    assert back["blockers"] == ["guard_issues"]
    refused = await _approve(client, back, auth)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "offer_not_approvable"
    assert refused.json()["error"]["details"]["blockers"] == ["guard_issues"]
    assert (await db.get(Lead, lead.id)).status == LeadStatus.PENDING_APPROVAL


async def test_manual_edit_marks_the_offer_and_rechecks_the_text(client, db, sessions, settings, lead, auth):
    o = await _drafted(client, db, sessions, settings, lead, auth, draft(), variant_count=1)
    url = f"/api/offers/{o['id']}"
    bad = (
        await client.patch(url, headers=auth, json={"body": "Alpha, сделаю 50 превью со скидкой 30%."})
    ).json()
    v = bad["variants"][0]
    assert bad["manually_edited"] and v["edited"] and v["body"].startswith("Alpha, сделаю 50")
    assert {i["code"] for i in v["guard_issues"]} == {"unsupported_number", "metric_claim"}
    assert "guard_issues" in bad["blockers"]
    fixed = (
        await client.patch(url, headers=auth, json={"body": CLEAN, "subject": "Превью для Alpha"})
    ).json()
    assert (
        fixed["variants"][0]["guard_issues"] == [] and fixed["variants"][0]["subject"] == "Превью для Alpha"
    )
    assert fixed["content_hash"] != bad["content_hash"]
    edits = list(
        await db.scalars(
            select(FeedbackEvent)
            .where(FeedbackEvent.kind == FeedbackKind.OFFER_EDIT)
            .order_by(FeedbackEvent.id)
        )
    )
    assert [sorted(e.payload["changes"]) for e in edits] == [["body"], ["body", "subject"]]
    assert edits[0].payload["changes"]["body"]["from"] == CLEAN

    for body in ({"body": ""}, {"subject": "x" * 151}, {"unknown": 1}):
        assert (await client.patch(url, headers=auth, json=body)).status_code == 422, body
    assert (await client.patch(url, headers=auth, json={"variant_id": 999999})).status_code == 404
    assert (await client.patch(url, headers=auth, json={"contact_id": 999999})).status_code == 404


async def test_schedule_needs_the_current_approval(client, db, sessions, settings, world, lead, auth):
    o = await _drafted(client, db, sessions, settings, lead, auth, draft(), variant_count=1)
    contact = await _email(client, world["alpha"], auth)
    o = (
        await client.patch(f"/api/offers/{o['id']}", headers=auth, json={"contact_id": contact["id"]})
    ).json()
    url = f"/api/offers/{o['id']}/schedule"
    soon = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    early = await client.post(url, headers=auth, json={"scheduled_at": soon})
    assert early.status_code == 409 and early.json()["error"]["code"] == "offer_status"

    o = (await _approve(client, o, auth)).json()
    past = await client.post(url, headers=auth, json={"scheduled_at": "2020-01-01T00:00:00Z"})
    assert past.status_code == 400 and past.json()["error"]["code"] == "invalid_schedule"
    naive = await client.post(url, headers=auth, json={"scheduled_at": "2030-01-01T00:00:00"})
    assert naive.status_code == 422
    # Phase 8: scheduling queues the real email, so a sender account is required
    no_account = await client.post(url, headers=auth, json={"scheduled_at": soon})
    assert no_account.status_code == 409 and no_account.json()["error"]["code"] == "email_account_required"
    account = await client.post(
        "/api/email-accounts", headers=auth,
        json={"name": "Main", "from_email": "me@example.com", "smtp_host": "smtp.example.com",
              "password": "pw"},
    )
    assert account.status_code == 201, account.text
    s = (await client.post(url, headers=auth, json={"scheduled_at": soon})).json()
    assert s["status"] == "scheduled" and s["scheduled_at"]
    assert s["message"]["status"] == "queued" and s["message"]["email_account_id"] == account.json()["id"]
    assert (await db.get(Lead, lead.id)).status == LeadStatus.SCHEDULED

    # editing a scheduled offer: back to the queue, the time is cleared, the queued email is cancelled
    e = (await client.patch(f"/api/offers/{o['id']}", headers=auth, json={"cta": "Интересно?"})).json()
    assert e["status"] == "pending_approval" and e["scheduled_at"] is None and e["manually_edited"]
    assert e["message"]["status"] == "cancelled"
    assert (await db.get(Lead, lead.id)).status == LeadStatus.PENDING_APPROVAL
    e = (await _approve(client, e, auth)).json()
    again = await client.post(url, headers=auth, json={"scheduled_at": soon})
    assert again.status_code == 200 and again.json()["message"]["status"] == "queued"
    un = await client.delete(url, headers=auth)
    assert un.status_code == 200 and un.json()["status"] == "approved" and un.json()["scheduled_at"] is None
    assert un.json()["message"]["status"] == "cancelled"
    assert (await client.delete(url, headers=auth)).status_code == 409


async def test_opted_out_contact_blocks_approval(client, db, sessions, settings, world, lead, auth):
    o = await _drafted(client, db, sessions, settings, lead, auth, draft(), variant_count=1)
    contact = await _email(client, world["alpha"], auth)
    o = (
        await client.patch(f"/api/offers/{o['id']}", headers=auth, json={"contact_id": contact["id"]})
    ).json()
    assert o["can_approve"]
    r = await client.patch(f"/api/contacts/{contact['id']}", headers=auth, json={"status": "opted_out"})
    assert r.status_code == 200, r.text
    o = (await client.get(f"/api/offers/{o['id']}")).json()
    assert o["blockers"] == ["contact_unusable"]
    assert (await _approve(client, o, auth)).status_code == 409
    # a Telegram channel does not fit an email contact
    other = await _email(client, world["alpha"], auth, "second@example.com")
    o = (
        await client.patch(
            f"/api/offers/{o['id']}", headers=auth, json={"contact_id": other["id"], "channel": "telegram"}
        )
    ).json()
    assert o["blockers"] == ["channel_mismatch"]
    o = (await client.patch(f"/api/offers/{o['id']}", headers=auth, json={"channel": "manual"})).json()
    assert o["can_approve"]


# ---------------------------------------------------------------------------
# Reject / Skip / Regenerate / queue order
# ---------------------------------------------------------------------------
async def test_reject_skip_regenerate_and_queue_order(client, db, sessions, settings, leads, auth):
    alpha, beta = leads
    oa = await _drafted(client, db, sessions, settings, alpha, auth, draft(), variant_count=1)
    ob = await _drafted(client, db, sessions, settings, beta, auth, draft(), variant_count=1)
    ids = [c["offer"]["id"] for c in (await client.get("/api/approval-queue")).json()["items"]]
    assert set(ids) == {oa["id"], ob["id"]}
    first = ids[0]
    skip = await client.post(f"/api/offers/{first}/decision", headers=auth, json={"decision": "skip"})
    assert (
        skip.status_code == 200 and skip.json()["skipped_at"] and skip.json()["status"] == "pending_approval"
    )
    assert [c["offer"]["id"] for c in (await client.get("/api/approval-queue")).json()["items"]][-1] == first

    rej = await client.post(
        f"/api/offers/{oa['id']}/decision",
        headers=auth,
        json={"decision": "reject", "comment": "слишком длинно"},
    )
    assert rej.status_code == 200 and rej.json()["status"] == "rejected"
    assert (await db.get(Lead, alpha.id)).status == LeadStatus.OFFER_DRAFT
    q = (await client.get("/api/approval-queue")).json()
    assert [c["offer"]["id"] for c in q["items"]] == [ob["id"]] and q["counts"]["rejected"] == 1
    again = await client.post(f"/api/offers/{oa['id']}/decision", headers=auth, json={"decision": "skip"})
    assert again.status_code == 409
    decisions = [a.decision for a in await db.scalars(select(Approval).order_by(Approval.id))]
    assert decisions == [ApprovalDecision.SKIPPED, ApprovalDecision.REJECTED]

    # Regenerate: the same offer gets round 2 (new variants, old ones kept)
    e = await _estimate(client, alpha, auth, variant_count=2)
    assert (e["offer_id"], e["round"]) == (oa["id"], 2)
    o2 = await _start(client, alpha, auth, variant_count=2)
    assert o2["id"] == oa["id"] and o2["round"] == 2 and o2["status"] == "draft"
    assert [(v["round"], v["label"]) for v in o2["variants"]] == [(2, "A"), (2, "B"), (1, "A")]
    await _run(db, sessions, settings, o2["id"], ScriptedText(draft(), draft()))
    done = (await client.get(f"/api/offers/{o2['id']}")).json()
    sel = next(v for v in done["variants"] if v["is_selected"])
    assert done["status"] == "pending_approval" and (sel["round"], sel["label"]) == (2, "A")
    # a job of an old round does nothing
    assert (await _run(db, sessions, settings, o2["id"], ScriptedText()))["skipped"]


async def test_attachment_defaults_to_the_final_sample(
    client, db, sessions, settings, storage, world, lead, auth
):
    req = GenerationRequest(
        workspace_id=world["ws"].id,
        lead_id=lead.id,
        source_video_id=world["videos"][0].id,
        variant_count=2,
        model_key="mock-image",
        brief={"fixes": [{"code": "small_text"}, {"code": "cluttered"}]},
    )
    db.add(req)
    await db.flush()
    variants = []
    for label, status_ in (("A", VariantStatus.FINAL), ("B", VariantStatus.APPROVED)):
        asset = await store_image(
            db, storage, f.noise_png(800 + len(label) + ord(label)), ImageSource.GENERATED
        )
        v = GeneratedThumbnail(
            generation_request_id=req.id,
            label=label,
            strategy="minimal",
            prompt="p",
            model_key="mock-image",
            image_asset_id=asset.id,
            status=status_,
        )
        db.add(v)
        variants.append(v)
    await db.flush()
    final, approved = variants

    e = await _estimate(client, lead, auth)
    assert [s["variant_id"] for s in e["samples"]] == [final.id, approved.id]
    assert e["inputs"]["sample"]["variant_id"] == final.id
    assert e["inputs"]["sample"]["fixes"] == ["small_text", "cluttered"]
    assert any(x["kind"] == "sample" and "Видео 601" in x["value"] for x in e["inputs"]["facts"])
    none = await _estimate(client, lead, auth, attach_sample=False)
    assert none["inputs"]["sample"] is None and "do not promise an attachment" in none["prompt_preview"]
    bad = await client.post(
        f"/api/leads/{lead.id}/offer/estimate", headers=auth, json={"attachment_variant_id": 999999}
    )
    assert bad.status_code == 404

    o = await _drafted(client, db, sessions, settings, lead, auth, draft(), variant_count=1)
    assert o["attachment"]["variant_id"] == final.id and o["attachment"]["image_url"]
    swapped = (
        await client.patch(
            f"/api/offers/{o['id']}", headers=auth, json={"attachment_variant_id": approved.id}
        )
    ).json()
    assert swapped["attachment"]["variant_id"] == approved.id and swapped["content_hash"] != o["content_hash"]
    cleared = (
        await client.patch(f"/api/offers/{o['id']}", headers=auth, json={"attachment_variant_id": None})
    ).json()
    assert cleared["attachment"] is None
    listed = (await client.get(f"/api/leads/{lead.id}/offers")).json()
    assert [i["id"] for i in listed["items"]] == [o["id"]] and len(listed["samples"]) == 2
    [card] = (await client.get("/api/approval-queue")).json()["items"]
    assert [s["variant_id"] for s in card["samples"]] == [final.id, approved.id]


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
async def test_templates_are_versioned_and_used_in_the_estimate(client, lead, auth):
    r = await client.post(
        "/api/offer-templates", headers=auth, json={"name": "Коротко", "body": "Коротко, на «вы», без воды"}
    )
    assert r.status_code == 201, r.text
    t1 = r.json()
    assert t1["version"] == 1
    t2 = (
        await client.put(
            f"/api/offer-templates/{t1['id']}",
            headers=auth,
            json={"name": "Коротко", "body": "Коротко, на «ты»"},
        )
    ).json()
    assert (t2["key"], t2["version"]) == (t1["key"], 2)
    listed = (await client.get("/api/offer-templates")).json()
    assert [(t["id"], t["version"]) for t in listed] == [(t2["id"], 2)]

    e = await _estimate(client, lead, auth, template_id=t2["id"])
    assert e["inputs"]["template"] == {
        "id": t2["id"],
        "name": "Коротко",
        "version": 2,
        "body": "Коротко, на «ты»",
    }
    assert "Коротко, на «ты»" in e["prompt_preview"]
    # an old version can still be used explicitly
    assert (await _estimate(client, lead, auth, template_id=t1["id"]))["inputs"]["template"]["version"] == 1

    assert (await client.delete(f"/api/offer-templates/{t1['id']}", headers=auth)).status_code == 204
    assert (await client.get("/api/offer-templates")).json() == []
    gone = await client.post(
        f"/api/leads/{lead.id}/offer/estimate", headers=auth, json={"template_id": t2["id"]}
    )
    assert gone.status_code == 404
    for bad in ({"name": "", "body": "x"}, {"name": "x", "body": ""}, {"name": "x", "body": "y", "z": 1}):
        assert (await client.post("/api/offer-templates", headers=auth, json=bad)).status_code == 422


# ---------------------------------------------------------------------------
# Roles and isolation
# ---------------------------------------------------------------------------
async def test_viewer_reads_but_cannot_write_or_approve(client, db, sessions, settings, owner, lead, auth):
    o = await _drafted(client, db, sessions, settings, lead, auth, draft(), variant_count=1)
    e = await _estimate(client, lead, auth)
    await add_member(db, owner[1], "viewer@example.com", WorkspaceRole.VIEWER)
    await client.post("/api/auth/logout", headers=auth)
    await login(client, "viewer@example.com")
    viewer = csrf_headers(client, settings)

    assert (await client.get("/api/approval-queue")).status_code == 200
    assert (await client.get(f"/api/offers/{o['id']}")).status_code == 200
    assert (
        await client.post(f"/api/leads/{lead.id}/offer/estimate", headers=viewer, json={})
    ).status_code == 200
    forbidden = [
        client.post(
            f"/api/leads/{lead.id}/offers", headers=viewer, json={"confirm_token": e["confirm_token"]}
        ),
        client.patch(f"/api/offers/{o['id']}", headers=viewer, json={"cta": "?"}),
        client.post(f"/api/offers/{o['id']}/decision", headers=viewer, json={"decision": "reject"}),
        client.post(
            f"/api/offers/{o['id']}/schedule", headers=viewer, json={"scheduled_at": "2030-01-01T00:00:00Z"}
        ),
        client.post("/api/offer-templates", headers=viewer, json={"name": "x", "body": "y"}),
    ]
    for call in forbidden:
        assert (await call).status_code == 403


async def test_other_workspace_sees_nothing(client, db, sessions, settings, lead, auth, other_owner):
    o = await _drafted(client, db, sessions, settings, lead, auth, draft(), variant_count=1)
    t = (await client.post("/api/offer-templates", headers=auth, json={"name": "Mine", "body": "b"})).json()
    await client.post("/api/auth/logout", headers=auth)
    await login(client, "other@example.com")
    other = csrf_headers(client, settings)

    assert (await client.get("/api/approval-queue")).json()["total"] == 0
    assert (await client.get(f"/api/offers/{o['id']}")).status_code == 404
    assert (await client.get(f"/api/leads/{lead.id}/offers")).status_code == 404
    assert (
        await client.post(f"/api/leads/{lead.id}/offer/estimate", headers=other, json={})
    ).status_code == 404
    assert (await client.patch(f"/api/offers/{o['id']}", headers=other, json={"cta": "?"})).status_code == 404
    assert (
        await client.post(f"/api/offers/{o['id']}/decision", headers=other, json={"decision": "reject"})
    ).status_code == 404
    assert (await client.get("/api/offer-templates")).json() == []
    assert (await client.delete(f"/api/offer-templates/{t['id']}", headers=other)).status_code == 404
    assert await db.scalar(select(func.count()).select_from(OfferVariant)) == 1
