"""Phase 5 API: estimate built from the analysis, confirmed start (lead status, variants, job cost cap),
decisions on variants with feedback, «Original vs Generated» data, references, roles and isolation."""

from __future__ import annotations

import base64

import factories as f
import pytest
from conftest import add_member, csrf_headers, login
from sqlalchemy import select

from app.core.state_machine import JobStatus, LeadStatus
from app.domains.analysis.audit import SUBSCORES
from app.domains.feedback.models import FeedbackEvent, FeedbackKind
from app.domains.generation.models import GeneratedThumbnail, GenerationRequest, VariantStatus
from app.domains.identity.models import WorkspaceRole
from app.domains.jobs.models import JobRun
from app.domains.leads.models import Lead, LeadStatusHistory
from app.domains.media.models import ImageSource
from app.domains.media.service import ensure_image_metrics, store_image


@pytest.fixture
async def world(db, owner, storage, settings, monkeypatch):
    """Alpha: a weak audited thumbnail (2.0, small_text + cluttered, older) and a strong one (8.0, newer)."""
    monkeypatch.setattr(settings, "ai_provider", "mock")
    ws = owner[1]
    p = await f.project(db, ws.id, "Generation")
    alpha = await f.channel(db, projects=[p], title="Alpha")
    other = await f.channel(db, projects=[p], title="Other")
    t = await f.prompt_template(db)
    videos = []
    for seed, score, codes, days in ((601, 2, ("small_text", "cluttered"), 5), (602, 8, (), 1)):
        a = await store_image(db, storage, f.noise_png(seed), ImageSource.YOUTUBE_THUMBNAIL)
        await ensure_image_metrics(db, storage, a)
        videos.append(await f.video(db, alpha, asset=a, days_ago=days, title=f"Видео {seed}"))
        await f.thumbnail_analysis(db, t, a, codes=codes, **{k: score for k in SUBSCORES})
    foreign_asset = await store_image(db, storage, f.noise_png(603), ImageSource.YOUTUBE_THUMBNAIL)
    foreign_video = await f.video(db, other, asset=foreign_asset)
    return {"ws": ws, "project": p, "alpha": alpha, "videos": videos, "foreign_video": foreign_video}


@pytest.fixture
async def auth(client, owner, settings):
    await login(client, "owner@example.com")
    return csrf_headers(client, settings)


@pytest.fixture
async def lead(client, db, world, auth):
    assert (await client.post("/api/leads/sync", headers=auth, json={})).status_code == 200
    return await db.scalar(select(Lead).where(Lead.channel_id == world["alpha"].id))


async def _estimate(client, lead, auth, **body):
    r = await client.post(f"/api/leads/{lead.id}/generation/estimate", headers=auth, json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def _start(client, lead, auth, **body):
    e = await _estimate(client, lead, auth, **body)
    r = await client.post(
        f"/api/leads/{lead.id}/generations", headers=auth, json=body | {"confirm_token": e["confirm_token"]}
    )
    assert r.status_code == 202, r.text
    return r.json()


async def _ready(db, storage, generation_id) -> list[GeneratedThumbnail]:
    """Pretend the job finished: every variant ready with an image."""
    variants = list(
        await db.scalars(
            select(GeneratedThumbnail).where(GeneratedThumbnail.generation_request_id == generation_id)
            .order_by(GeneratedThumbnail.label)
        )
    )
    for i, v in enumerate(variants):
        asset = await store_image(db, storage, f.noise_png(700 + v.id * 10 + i), ImageSource.GENERATED)
        v.status, v.image_asset_id = VariantStatus.READY, asset.id
    await db.flush()
    return variants


def _png_b64(seed: int = 1) -> str:
    return base64.b64encode(f.noise_png(seed)).decode()


# ---------------------------------------------------------------------------
# Estimate
# ---------------------------------------------------------------------------
async def test_estimate_builds_the_brief_from_the_analysis(client, db, world, lead, auth):
    e = await _estimate(client, lead, auth, instructions="  Без текста  ")
    weak, strong = world["videos"]
    # the weakest audited thumbnail is redesigned by default, not the newest one
    assert e["source"]["video_id"] == weak.id
    assert [c["video_id"] for c in e["source_candidates"]] == [strong.id, weak.id]
    brief = e["brief"]
    assert brief["channel_title"] == "Alpha" and brief["video_title"] == weak.title
    assert brief["base_image"] == "current_thumbnail" and brief["instructions"] == "Без текста"
    assert {x["code"] for x in brief["fixes"]} == {"small_text", "cluttered"}
    assert all(x["source"] == "thumbnail" for x in brief["fixes"])
    assert brief["palette"] and all(c.startswith("#") for c in brief["palette"])
    assert brief["history"] == {"previous_requests": 0, "approved": [], "rejected": []}
    assert "Variant A. Composition direction" in e["prompt_preview"]
    assert "(fixes: small_text)" in e["prompt_preview"] and "Без текста" in e["prompt_preview"]
    assert (e["provider"], e["model_key"], e["variant_count"]) == ("mock", "mock-image", 4)
    assert [m["key"] for m in e["models"]] == ["mock-image"]
    assert float(e["estimated_cost_usd"]) == 0 and e["within_budgets"] and e["lead_status"] == "analyzed"
    # nothing was started
    assert not list(await db.scalars(select(GenerationRequest)))


async def test_estimate_validation(client, world, lead, auth):
    url = f"/api/leads/{lead.id}/generation/estimate"
    other = await client.post(url, headers=auth, json={"source_video_id": world["foreign_video"].id})
    assert other.status_code == 404  # a video of another channel
    chosen = await _estimate(client, lead, auth, source_video_id=world["videos"][1].id)
    assert chosen["source"]["video_id"] == world["videos"][1].id and chosen["brief"]["fixes"] == []
    model = await client.post(url, headers=auth, json={"model_key": "vc-gpt-image-2.5"})
    assert model.status_code == 400 and model.json()["error"]["code"] == "invalid_model"
    invalid = ({"variant_count": 0}, {"variant_count": 7}, {"size": "640x480"},
               {"reference_ids": [1, 2, 3, 4]}, {"unknown": 1})
    for bad in invalid:
        assert (await client.post(url, headers=auth, json=bad)).status_code == 422, bad
    missing = await client.post("/api/leads/999999/generation/estimate", headers=auth, json={})
    assert missing.status_code == 404


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------
async def test_start_requires_the_confirmed_estimate_and_queues_one_job(client, db, world, lead, auth):
    url = f"/api/leads/{lead.id}/generations"
    e = await _estimate(client, lead, auth, variant_count=3)
    wrong = await client.post(url, headers=auth, json={"variant_count": 3, "confirm_token": "nope"})
    assert wrong.status_code == 409 and wrong.json()["error"]["code"] == "confirmation_required"
    assert wrong.json()["error"]["details"]["confirm_token"] == e["confirm_token"]
    changed = await client.post(
        url, headers=auth, json={"variant_count": 4, "confirm_token": e["confirm_token"]}
    )
    assert changed.status_code == 409  # 4 variants were not what the owner confirmed

    ok = await client.post(url, headers=auth, json={"variant_count": 3, "confirm_token": e["confirm_token"]})
    assert ok.status_code == 202, ok.text
    body = ok.json()
    g = body["generation"]
    assert body["lead_status"] == "generation_pending" and g["status"] == "queued" and g["provider_is_mock"]
    assert [v["label"] for v in g["variants"]] == ["A", "B", "C"]
    assert all(v["status"] == "pending" for v in g["variants"])
    assert len({v["strategy"] for v in g["variants"]}) == 3
    assert g["variants"][0]["prompt"] == e["prompt_preview"]  # exactly what the owner saw
    assert g["source_video_id"] == world["videos"][0].id and g["source_image_url"]

    job = await db.get(JobRun, g["job"]["id"])
    assert (job.type, job.queue, job.progress_total) == ("thumbnail_generation", "interactive", 3)
    assert job.params == {
        "generation_request_id": g["id"], "lead_id": lead.id, "project_id": world["project"].id
    }
    assert job.budget_usd == job.estimated_cost_usd == 0  # the confirmed total is the hard cap

    history = list(
        await db.scalars(
            select(LeadStatusHistory)
            .where(LeadStatusHistory.lead_id == lead.id)
            .order_by(LeadStatusHistory.id)
        )
    )
    assert [(h.from_status, h.to_status) for h in history][-2:] == [
        (LeadStatus.ANALYZED, LeadStatus.SELECTED), (LeadStatus.SELECTED, LeadStatus.GENERATION_PENDING)]

    again = await _estimate(client, lead, auth, variant_count=3)
    busy = await client.post(
        url, headers=auth, json={"variant_count": 3, "confirm_token": again["confirm_token"]}
    )
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "generation_running"


async def test_start_is_refused_for_closed_leads_and_without_ai(
    client, db, lead, auth, settings, monkeypatch
):
    e = await _estimate(client, lead, auth)
    url = f"/api/leads/{lead.id}/generations"
    monkeypatch.setattr(settings, "ai_provider", None)
    monkeypatch.setattr(settings, "demo_mode", False)
    e2 = await _estimate(client, lead, auth)
    assert e2["model_key"] is None and e2["models"] == []
    r = await client.post(url, headers=auth, json={"confirm_token": e2["confirm_token"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "ai_not_configured"

    monkeypatch.setattr(settings, "ai_provider", "mock")
    lead.status = LeadStatus.REJECTED
    await db.flush()
    r = await client.post(url, headers=auth, json={"confirm_token": e["confirm_token"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "lead_status"
    assert not list(await db.scalars(select(GenerationRequest)))


# ---------------------------------------------------------------------------
# Results and decisions
# ---------------------------------------------------------------------------
async def test_decisions_feedback_final_sample_and_history(client, db, storage, owner, world, lead, auth):
    g = (await _start(client, lead, auth, variant_count=3))["generation"]
    a, b, c = await _ready(db, storage, g["id"])
    url = "/api/generated-thumbnails/{}/decision"

    r = await client.post(url.format(a.id), headers=auth, json={"decision": "approve", "comment": "хорошо"})
    assert r.status_code == 200, r.text
    assert (await client.post(url.format(b.id), headers=auth, json={"decision": "reject"})).status_code == 200
    final = (await client.post(url.format(c.id), headers=auth, json={"decision": "final"})).json()
    assert final["final_variant_id"] == c.id
    # a new final sample demotes the previous one to approved: one final per lead
    swap = (await client.post(url.format(a.id), headers=auth, json={"decision": "final"})).json()
    statuses = {v["label"]: v["status"] for v in swap["items"][0]["variants"]}
    assert swap["final_variant_id"] == a.id and statuses == {"A": "final", "B": "rejected", "C": "approved"}
    reset = (await client.post(url.format(b.id), headers=auth, json={"decision": "reset"})).json()
    assert {v["label"]: v["status"] for v in reset["items"][0]["variants"]}["B"] == "ready"
    for bad in ({"decision": "love"}, {"decision": "approve", "comment": "x" * 501}):
        assert (await client.post(url.format(a.id), headers=auth, json=bad)).status_code == 422

    events = list(
        await db.scalars(
            select(FeedbackEvent).where(FeedbackEvent.kind == FeedbackKind.GENERATION_DECISION)
            .order_by(FeedbackEvent.id)
        )
    )
    assert [(e.target_id, e.payload["decision"]) for e in events] == [
        (a.id, "approve"), (b.id, "reject"), (c.id, "final"), (a.id, "final"), (b.id, "reset")]
    assert events[0].payload["comment"] == "хорошо" and events[0].payload["strategy"] == a.strategy
    assert events[0].user_id == owner[0].id and events[0].payload["lead_id"] == lead.id

    # the next brief knows what the owner liked and rejected
    await client.post(url.format(b.id), headers=auth, json={"decision": "reject"})
    history = (await _estimate(client, lead, auth))["brief"]["history"]
    assert history == {"previous_requests": 1, "approved": sorted({a.strategy, c.strategy}),
                       "rejected": [b.strategy]}


async def test_pending_variant_cannot_be_decided_and_results_show_the_original(
    client, db, storage, world, lead, auth
):
    g = (await _start(client, lead, auth, variant_count=1))["generation"]
    vid = g["variants"][0]["id"]
    r = await client.post(
        f"/api/generated-thumbnails/{vid}/decision", headers=auth, json={"decision": "final"}
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "variant_not_ready"
    assert (await client.post("/api/generated-thumbnails/999999/decision", headers=auth,
                              json={"decision": "approve"})).status_code == 404

    data = (await client.get(f"/api/leads/{lead.id}/generations")).json()
    assert data["lead_status"] == "generation_pending" and data["final_variant_id"] is None
    assert [i["id"] for i in data["items"]] == [g["id"]]
    [orig] = data["originals"]
    assert orig["video_id"] == world["videos"][0].id and float(orig["overall_score"]) == 2.0
    assert {p["code"] for p in orig["problems"]} == {"small_text", "cluttered"} and orig["image_url"]


async def test_create_more_like_this_uses_the_variant_as_image_1(client, db, storage, lead, auth):
    g = (await _start(client, lead, auth, variant_count=1))["generation"]
    [a] = await _ready(db, storage, g["id"])
    job = await db.get(JobRun, g["job"]["id"])
    job.status = JobStatus.COMPLETED  # the first job finished
    await db.flush()
    e = await _estimate(client, lead, auth, parent_variant_id=a.id, variant_count=2)
    assert e["brief"]["base_image"] == "parent_variant" and "variant the owner liked" in e["prompt_preview"]
    started = await _start(client, lead, auth, parent_variant_id=a.id, variant_count=2)
    assert all(v["parent_id"] == a.id and v["source_image_ids"][0] == a.image_asset_id
               for v in started["generation"]["variants"])
    missing = await client.post(f"/api/leads/{lead.id}/generation/estimate", headers=auth,
                                json={"parent_variant_id": 999999})
    assert missing.status_code == 404


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------
async def test_reference_crud_and_use_in_the_brief(client, db, lead, auth, other_owner):
    body = {"image_base64": _png_b64(11), "title": "Неон", "style": "neon", "tags": ["b", "a", "b"]}
    r = await client.post("/api/references", headers=auth, json=body)
    assert r.status_code == 201, r.text
    ref = r.json()
    assert (ref["width"], ref["height"], ref["tags"], ref["title"]) == (160, 90, ["a", "b"], "Неон")
    data_url = await client.post("/api/references", headers=auth,
                                 json=body | {"image_base64": f"data:image/png;base64,{_png_b64(12)}"})
    assert data_url.status_code == 201
    listed = (await client.get("/api/references")).json()
    assert [x["id"] for x in listed] == [data_url.json()["id"], ref["id"]]

    patched = await client.patch(f"/api/references/{ref['id']}", headers=auth,
                                 json={"description": "яркий неон", "tags": ["z"]})
    assert patched.json()["description"] == "яркий неон" and patched.json()["tags"] == ["z"]

    e = await _estimate(client, lead, auth, reference_ids=[ref["id"]])
    assert e["brief"]["references"] == [{"id": ref["id"], "title": "Неон", "style": "neon",
                                         "description": "яркий неон"}]
    assert "Images 2..2 are style references" in e["prompt_preview"]
    g = (await _start(client, lead, auth, reference_ids=[ref["id"]], variant_count=1))["generation"]
    assert g["reference_ids"] == [ref["id"]] and len(g["variants"][0]["source_image_ids"]) == 2

    assert (await client.delete(f"/api/references/{ref['id']}", headers=auth)).status_code == 204
    assert (await client.patch(f"/api/references/{ref['id']}", headers=auth, json={})).status_code == 404
    gone = await client.post(f"/api/leads/{lead.id}/generation/estimate", headers=auth,
                             json={"reference_ids": [ref["id"]]})
    assert gone.status_code == 404


async def test_reference_validation(client, auth):
    async def post(**over):
        return await client.post("/api/references", headers=auth,
                                 json={"image_base64": _png_b64(), "title": "T"} | over)

    bad = await post(image_base64="%%%% not base64 %%%%")
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_image"
    text = await post(image_base64=base64.b64encode(b"just some text, not a picture").decode())
    assert text.status_code == 400 and text.json()["error"]["code"] == "invalid_image"
    assert (await post(title="")).status_code == 422
    assert (await post(tags=[f"t{i}" for i in range(21)])).status_code == 422


# ---------------------------------------------------------------------------
# Roles and isolation
# ---------------------------------------------------------------------------
async def test_viewer_reads_but_cannot_generate_decide_or_upload(
    client, db, storage, owner, lead, auth, settings
):
    g = (await _start(client, lead, auth, variant_count=1))["generation"]
    [a] = await _ready(db, storage, g["id"])
    e = await _estimate(client, lead, auth)
    await add_member(db, owner[1], "viewer@example.com", WorkspaceRole.VIEWER)
    await client.post("/api/auth/logout", headers=auth)
    await login(client, "viewer@example.com")
    viewer = csrf_headers(client, settings)

    assert (await client.get(f"/api/leads/{lead.id}/generations")).status_code == 200
    assert (await client.post(f"/api/leads/{lead.id}/generation/estimate", headers=viewer,
                              json={})).status_code == 200
    assert (await client.post(f"/api/leads/{lead.id}/generations", headers=viewer,
                              json={"confirm_token": e["confirm_token"]})).status_code == 403
    assert (await client.post(f"/api/generated-thumbnails/{a.id}/decision", headers=viewer,
                              json={"decision": "approve"})).status_code == 403
    assert (await client.post("/api/references", headers=viewer,
                              json={"image_base64": _png_b64(), "title": "T"})).status_code == 403


async def test_other_workspace_sees_nothing(client, db, storage, lead, auth, settings, other_owner):
    g = (await _start(client, lead, auth, variant_count=1))["generation"]
    [a] = await _ready(db, storage, g["id"])
    ref = (await client.post("/api/references", headers=auth,
                             json={"image_base64": _png_b64(), "title": "Mine"})).json()
    await client.post("/api/auth/logout", headers=auth)
    await login(client, "other@example.com")
    other = csrf_headers(client, settings)

    assert (await client.get(f"/api/leads/{lead.id}/generations")).status_code == 404
    assert (await client.post(f"/api/leads/{lead.id}/generation/estimate", headers=other,
                              json={})).status_code == 404
    assert (await client.post(f"/api/generated-thumbnails/{a.id}/decision", headers=other,
                              json={"decision": "reject"})).status_code == 404
    assert (await client.get("/api/references")).json() == []
    assert (await client.delete(f"/api/references/{ref['id']}", headers=other)).status_code == 404
