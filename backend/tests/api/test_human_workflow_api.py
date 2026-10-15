"""Phase 4 API: manual lead statuses, bulk actions on a selection, tags (user + system), favorites, human
score corrections with rescoring and the «AI vs human» summary, approvals gate, roles and isolation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import factories as f
import pytest
from conftest import add_member, csrf_headers, login
from sqlalchemy import func, select

from app.core.errors import ConflictError
from app.core.state_machine import LeadStatus
from app.domains.analysis.audit import SUBSCORES
from app.domains.approvals.models import ApprovalDecision, ApprovalTarget
from app.domains.approvals.service import content_hash, record_decision, require_approval
from app.domains.feedback.models import FeedbackEvent, FeedbackKind
from app.domains.identity.models import AuditLog, WorkspaceRole
from app.domains.leads.models import Lead, LeadStatusHistory
from app.domains.media.models import ImageSource
from app.domains.media.service import store_image
from app.domains.youtube.models import ChannelMetrics

NOW = datetime.now(UTC)


@pytest.fixture
async def world(db, owner, storage):
    """Alpha: 2 weak audited thumbnails (2.0), very active; Beta: 2 strong ones (8.0); Gamma: bare channel."""
    ws = owner[1]
    p = await f.project(db, ws.id, "Workflow")
    t = await f.prompt_template(db)
    seq = iter(range(900, 1000))
    analyses: dict[str, list] = {"alpha": [], "beta": []}

    async def audited(key, ch, score):
        asset = await store_image(db, storage, f.noise_png(next(seq)), ImageSource.YOUTUBE_THUMBNAIL)
        video = await f.video(db, ch, asset=asset)
        row = await f.thumbnail_analysis(db, t, asset, **{k: score for k in SUBSCORES})
        analyses[key].append((video, row))

    alpha = await f.channel(db, projects=[p], title="Alpha", subscriber_count=50_000)
    beta = await f.channel(db, projects=[p], title="Beta", subscriber_count=5_000)
    gamma = await f.channel(db, projects=[p], title="Gamma")
    for _ in range(2):
        await audited("alpha", alpha, 2)
        await audited("beta", beta, 8)
    db.add(
        ChannelMetrics(
            channel_id=alpha.id,
            computed_at=NOW,
            window_videos=10,
            avg_views=40_000,
            median_views=40_000,
            videos_30d=6,
            last_video_at=NOW - timedelta(days=2),
        )
    )
    await db.flush()
    return {"ws": ws, "project": p, "alpha": alpha, "beta": beta, "gamma": gamma, "analyses": analyses}


@pytest.fixture
async def auth(client, owner, settings):
    await login(client, "owner@example.com")
    return csrf_headers(client, settings)


@pytest.fixture
async def leads(client, world, auth):
    r = await client.post("/api/leads/sync", headers=auth, json={})
    assert r.status_code == 200, r.text
    page = (await client.get("/api/leads")).json()
    return {item["channel"]["title"]: item for item in page["items"]}


async def _titles(client, **params):
    r = await client.get("/api/leads", params=params)
    assert r.status_code == 200, r.text
    return [i["channel"]["title"] for i in r.json()["items"]]


async def _lead(client, lead_id):
    r = await client.get(f"/api/leads/{lead_id}")
    assert r.status_code == 200, r.text
    return r.json()


def _tag_names(lead):
    return [t["name"] for t in lead["tags"]]


# ---------------------------------------------------------------------------
# Manual statuses
# ---------------------------------------------------------------------------
async def test_manual_status_flow_history_and_feedback(client, db, owner, leads, auth):
    alpha = leads["Alpha"]
    assert alpha["status"] == "analyzed" and alpha["rejected_reason"] is None
    url = f"/api/leads/{alpha['id']}/status"

    r = await client.post(url, headers=auth, json={"status": "shortlisted"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "shortlisted"
    again = await client.post(url, headers=auth, json={"status": "shortlisted"})
    assert again.status_code == 200  # same status: no-op
    assert (await client.post(url, headers=auth, json={"status": "selected"})).json()["status"] == "selected"

    rejected = await client.post(url, headers=auth, json={"status": "rejected", "reason": "  not my niche "})
    assert rejected.status_code == 200
    body = rejected.json()
    assert body["status"] == "rejected" and body["rejected_reason"] == "not my niche"
    # REJECTED → only back to the shortlist
    bad = await client.post(url, headers=auth, json={"status": "selected"})
    assert bad.status_code == 409 and bad.json()["error"]["code"] == "invalid_transition"
    restored = (await client.post(url, headers=auth, json={"status": "shortlisted"})).json()
    assert restored["status"] == "shortlisted" and restored["rejected_reason"] is None

    system = await client.post(url, headers=auth, json={"status": "sent"})
    assert system.status_code == 400 and system.json()["error"]["code"] == "status_not_manual"
    missing = await client.post("/api/leads/999999/status", headers=auth, json={"status": "shortlisted"})
    assert missing.status_code == 404

    detail = await _lead(client, alpha["id"])
    manual = [h for h in detail["history"] if h["actor_user_id"] is not None]
    assert [(h["from_status"], h["to_status"]) for h in manual] == [
        ("rejected", "shortlisted"),
        ("selected", "rejected"),
        ("shortlisted", "selected"),
        ("analyzed", "shortlisted"),
    ]
    assert manual[1]["reason"] == "not my niche" and manual[0]["actor_user_id"] == owner[0].id

    events = list(
        await db.scalars(
            select(FeedbackEvent)
            .where(FeedbackEvent.kind == FeedbackKind.LEAD_STATUS)
            .order_by(FeedbackEvent.id)
        )
    )
    assert [e.payload["to"] for e in events] == ["shortlisted", "selected", "rejected", "shortlisted"]
    assert all(e.ai_value == Decimal(alpha["score"]["total"]) for e in events)
    assert (
        await db.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.action == "lead.status_changed")
        )
        == 4
    )

    summary = (await client.get("/api/feedback/summary")).json()
    decisions = {d["status"]: d for d in summary["lead_decisions"]}
    assert decisions["shortlisted"]["decisions"] == 2 and decisions["rejected"]["decisions"] == 1
    assert Decimal(decisions["rejected"]["avg_lead_score"]) == Decimal(alpha["score"]["total"])


async def test_rescore_does_not_override_manual_status(client, db, storage, world, leads, auth):
    gamma = leads["Gamma"]
    assert gamma["status"] == "discovered"
    r = await client.post(f"/api/leads/{gamma['id']}/status", headers=auth, json={"status": "rejected"})
    assert r.status_code == 200
    # Gamma gets an audited thumbnail: the automatic DISCOVERED → ANALYZED must not touch a rejected lead
    asset = await store_image(db, storage, f.noise_png(1), ImageSource.YOUTUBE_THUMBNAIL)
    await f.video(db, world["gamma"], asset=asset)
    await f.thumbnail_analysis(db, await f.prompt_template(db, version=2), asset)
    resync = await client.post("/api/leads/sync", headers=auth, json={})
    assert resync.status_code == 200 and resync.json()["leads_analyzed"] == 0
    after = await _lead(client, gamma["id"])
    assert after["status"] == "rejected" and Decimal(after["score"]["avg_thumbnail_score"]) == Decimal("5.00")


# ---------------------------------------------------------------------------
# Bulk actions
# ---------------------------------------------------------------------------
async def test_bulk_by_ids_and_by_filters(client, db, leads, auth):
    ids = [leads[t]["id"] for t in ("Alpha", "Beta", "Gamma")]
    r = await client.post(
        "/api/leads/bulk",
        headers=auth,
        json={"selection": {"lead_ids": ids[:2]}, "action": {"type": "set_status", "status": "shortlisted"}},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"matched": 2, "changed": 2, "unchanged": 0, "failed": 0, "failures": []}

    # «all filtered» minus one unticked row; Gamma (discovered) can go straight to selected
    r = await client.post(
        "/api/leads/bulk",
        headers=auth,
        json={
            "selection": {"filters": {"project_id": None}, "exclude_ids": [ids[1]]},
            "action": {"type": "set_status", "status": "selected"},
        },
    )
    assert r.json() == {"matched": 2, "changed": 2, "unchanged": 0, "failed": 0, "failures": []}
    assert set(await _titles(client, status="selected")) == {"Alpha", "Gamma"}

    # partial failure: selected → analyzed is not allowed, shortlisted → analyzed is
    r = await client.post(
        "/api/leads/bulk",
        headers=auth,
        json={"selection": {"lead_ids": ids}, "action": {"type": "set_status", "status": "analyzed"}},
    )
    body = r.json()
    assert (body["matched"], body["changed"], body["failed"]) == (3, 1, 2)
    assert {x["lead_id"] for x in body["failures"]} == {ids[0], ids[2]}
    assert {x["error"] for x in body["failures"]} == {"invalid_transition"}

    # filters work as in the list: only status=analyzed (Beta)
    r = await client.post(
        "/api/leads/bulk",
        headers=auth,
        json={
            "selection": {"filters": {"status": ["analyzed"]}},
            "action": {"type": "set_status", "status": "rejected", "reason": "bulk"},
        },
    )
    assert r.json()["changed"] == 1
    assert (await _lead(client, ids[1]))["rejected_reason"] == "bulk"
    assert (
        await db.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.action == "lead.bulk_status")
        )
        == 4
    )

    for bad in (
        {"selection": {}, "action": {"type": "favorite"}},
        {"selection": {"lead_ids": ids, "filters": {}}, "action": {"type": "favorite"}},
        {"selection": {"lead_ids": []}, "action": {"type": "favorite"}},
        {"selection": {"lead_ids": ids}, "action": {"type": "explode"}},
    ):
        assert (await client.post("/api/leads/bulk", headers=auth, json=bad)).status_code == 422
    manual = await client.post(
        "/api/leads/bulk",
        headers=auth,
        json={"selection": {"lead_ids": ids}, "action": {"type": "set_status", "status": "approved"}},
    )
    assert manual.status_code == 400
    missing = await client.post(
        "/api/leads/bulk",
        headers=auth,
        json={"selection": {"lead_ids": [*ids, 999_999]}, "action": {"type": "favorite"}},
    )
    assert missing.status_code == 404 and missing.json()["error"]["details"]["lead_ids"] == [999_999]


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------
async def test_system_tags_follow_rules(client, leads):
    assert "weak-thumbnails" in _tag_names(leads["Alpha"])
    assert "weak-thumbnails" not in _tag_names(leads["Beta"]) and _tag_names(leads["Gamma"]) == []
    assert all(t["is_system"] and t["source"] == "system" for t in leads["Alpha"]["tags"])
    listed = (await client.get("/api/tags")).json()
    system = [t for t in listed if t["is_system"]]
    assert {t["name"] for t in system} == {
        "weak-thumbnails",
        "high-potential",
        "hot-lead",
        "visual-opportunity",
    }
    assert all(t["rule"] for t in system)
    assert next(t for t in system if t["name"] == "weak-thumbnails")["leads"] == 1


async def test_user_tags_crud_assign_and_filter(client, db, leads, auth):
    r = await client.post("/api/tags", headers=auth, json={"name": "  Finance   Leads ", "color": "#12ab34"})
    assert r.status_code == 201, r.text
    tag = r.json()
    assert tag["name"] == "Finance Leads" and not tag["is_system"] and tag["leads"] == 0
    dup = await client.post("/api/tags", headers=auth, json={"name": "finance leads"})
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "tag_exists"
    reserved = await client.post("/api/tags", headers=auth, json={"name": "Hot-Lead"})
    assert reserved.status_code == 409 and reserved.json()["error"]["code"] == "tag_reserved"
    assert (
        await client.post("/api/tags", headers=auth, json={"name": "x", "color": "red"})
    ).status_code == 422

    ids = [leads["Alpha"]["id"], leads["Gamma"]["id"]]
    add = {"selection": {"lead_ids": ids}, "action": {"type": "add_tag", "tag_id": tag["id"]}}
    assert (await client.post("/api/leads/bulk", headers=auth, json=add)).json()["changed"] == 2
    assert (await client.post("/api/leads/bulk", headers=auth, json=add)).json()["unchanged"] == 2
    assert set(await _titles(client, tag=tag["id"])) == {"Alpha", "Gamma"}
    alpha = await _lead(client, ids[0])
    assert alpha["tags"][-1] == {
        "id": tag["id"],
        "name": "Finance Leads",
        "color": "#12ab34",
        "is_system": False,
        "source": "user",
    }  # system tags first

    renamed = await client.patch(
        f"/api/tags/{tag['id']}", headers=auth, json={"name": "Finance", "color": None}
    )
    assert renamed.status_code == 200 and renamed.json()["color"] is None and renamed.json()["leads"] == 2
    remove = {"selection": {"lead_ids": [ids[1]]}, "action": {"type": "remove_tag", "tag_id": tag["id"]}}
    assert (await client.post("/api/leads/bulk", headers=auth, json=remove)).json()["changed"] == 1
    assert await _titles(client, tag=tag["id"]) == ["Alpha"]

    system = next(t for t in (await client.get("/api/tags")).json() if t["name"] == "weak-thumbnails")
    assert (
        await client.patch(f"/api/tags/{system['id']}", headers=auth, json={"name": "w"})
    ).status_code == 409
    recolor = await client.patch(f"/api/tags/{system['id']}", headers=auth, json={"color": "#ff0000"})
    assert recolor.status_code == 200 and recolor.json()["color"] == "#ff0000"
    assert (await client.delete(f"/api/tags/{system['id']}", headers=auth)).status_code == 409
    manual_system = {"selection": {"lead_ids": ids}, "action": {"type": "add_tag", "tag_id": system["id"]}}
    r = await client.post("/api/leads/bulk", headers=auth, json=manual_system)
    assert r.status_code == 409 and r.json()["error"]["code"] == "system_tag"

    assert (await client.delete(f"/api/tags/{tag['id']}", headers=auth)).status_code == 204
    assert all(t["name"] != "Finance" for t in (await _lead(client, ids[0]))["tags"])
    assert (await client.delete(f"/api/tags/{tag['id']}", headers=auth)).status_code == 404


# ---------------------------------------------------------------------------
# Favorites
# ---------------------------------------------------------------------------
async def test_favorites_channels_and_thumbnails(client, db, world, leads, auth, other_owner):
    alpha, beta = world["alpha"], world["beta"]
    r = await client.put(f"/api/favorites/channel/{alpha.id}", headers=auth)
    assert r.status_code == 200 and r.json()["is_favorite"] is True
    assert (
        await client.put(f"/api/favorites/channel/{alpha.id}", headers=auth)
    ).status_code == 200  # idempotent
    video, _ = world["analyses"]["beta"][0]
    assert (await client.put(f"/api/favorites/thumbnail/{video.id}", headers=auth)).status_code == 200

    assert await _titles(client, favorite="true") == ["Alpha"]
    assert set(await _titles(client, favorite="false")) == {"Beta", "Gamma"}
    assert (await _lead(client, leads["Alpha"]["id"]))["is_favorite"] is True

    listed = (await client.get("/api/favorites")).json()
    assert listed["total"] == 2
    kinds = {i["entity_type"]: i for i in listed["items"]}
    assert kinds["channel"]["channel"]["title"] == "Alpha" and kinds["channel"]["video"] is None
    assert kinds["thumbnail"]["video"]["channel_title"] == "Beta"
    assert kinds["thumbnail"]["video"]["image_url"] is not None
    assert (await client.get("/api/favorites", params={"entity_type": "thumbnail"})).json()["total"] == 1

    analyses = (
        await client.get(
            f"/api/projects/{world['project'].id}/thumbnail-analyses", params={"channel_id": beta.id}
        )
    ).json()
    assert {i["video_id"]: i["is_favorite"] for i in analyses["items"]}[video.id] is True

    bulk = {
        "selection": {"lead_ids": [leads["Alpha"]["id"], leads["Beta"]["id"]]},
        "action": {"type": "favorite"},
    }
    assert (await client.post("/api/leads/bulk", headers=auth, json=bulk)).json()["changed"] == 1
    bulk["action"] = {"type": "unfavorite"}
    assert (await client.post("/api/leads/bulk", headers=auth, json=bulk)).json()["changed"] == 2
    assert (await client.delete(f"/api/favorites/thumbnail/{video.id}", headers=auth)).json()[
        "is_favorite"
    ] is False
    assert (await client.get("/api/favorites")).json()["total"] == 0

    # channels of another workspace are not visible
    foreign = await f.channel(db, projects=[await f.project(db, other_owner[1].id)], title="Foreign")
    assert (await client.put(f"/api/favorites/channel/{foreign.id}", headers=auth)).status_code == 404
    assert (await client.put("/api/favorites/thumbnail/999999", headers=auth)).status_code == 404
    assert (await client.put(f"/api/favorites/concept/{alpha.id}", headers=auth)).status_code == 422


# ---------------------------------------------------------------------------
# Human score corrections (§47) and «AI vs human» (§46)
# ---------------------------------------------------------------------------
async def test_thumbnail_override_rescores_lead_and_keeps_ai_value(client, db, world, leads, auth):
    alpha = leads["Alpha"]
    assert Decimal(alpha["score"]["avg_thumbnail_score"]) == Decimal("2.00")
    _, analysis = world["analyses"]["alpha"][0]
    r = await client.put(
        "/api/score-overrides",
        headers=auth,
        json={
            "target_type": "thumbnail_analysis",
            "target_id": analysis.id,
            "field": "overall_score",
            "human_value": 8,
            "reason": "looks fine to me",
        },
    )
    assert r.status_code == 200, r.text
    saved = r.json()
    assert Decimal(saved["ai_value"]) == Decimal("2.00") and Decimal(saved["human_value"]) == 8
    assert saved["rescored"]["scores_changed"] == 1 and saved["rescored"]["leads_created"] == 0

    after = await _lead(client, alpha["id"])
    assert Decimal(after["score"]["avg_thumbnail_score"]) == Decimal("5.00")  # (2 + 8) / 2
    assert "weak-thumbnails" not in _tag_names(after)  # 5.00 is not < 5
    comp = {c["key"]: c for c in after["score"]["components"]}["thumbnail_opportunity"]
    assert "ручная оценка у 1" in comp["reason"]

    analyses = (
        await client.get(
            f"/api/projects/{world['project'].id}/thumbnail-analyses",
            params={"channel_id": world["alpha"].id},
        )
    ).json()
    item = next(i for i in analyses["items"] if i["analysis"]["id"] == analysis.id)
    assert Decimal(item["analysis"]["overall_score"]) == Decimal("2.00")  # the AI value is never overwritten
    assert [Decimal(o["human_value"]) for o in item["overrides"]] == [Decimal(8)]

    # a subscore: stored, shown, does not rescore
    sub = await client.put(
        "/api/score-overrides",
        headers=auth,
        json={
            "target_type": "thumbnail_analysis",
            "target_id": analysis.id,
            "field": "text_score",
            "human_value": 3,
        },
    )
    assert sub.status_code == 200 and sub.json()["rescored"] is None
    listed = (
        await client.get(
            "/api/score-overrides", params={"target_type": "thumbnail_analysis", "target_id": analysis.id}
        )
    ).json()
    assert [o["field"] for o in listed] == ["overall_score", "text_score"]

    removed = await client.delete(f"/api/score-overrides/{saved['id']}", headers=auth)
    assert removed.status_code == 200 and removed.json()["rescored"]["scores_changed"] == 1
    back = await _lead(client, alpha["id"])
    assert Decimal(back["score"]["avg_thumbnail_score"]) == Decimal("2.00")
    assert "weak-thumbnails" in _tag_names(back)
    assert (await client.delete(f"/api/score-overrides/{saved['id']}", headers=auth)).status_code == 404

    summary = (await client.get("/api/feedback/summary")).json()
    assert summary["events"] == 3  # set, set, removed
    [stats] = summary["overrides"]  # only the text_score override remains
    assert (
        stats["field"] == "text_score" and stats["ai_overestimates"] == 0 and stats["ai_underestimates"] == 1
    )


async def test_lead_override_drives_sort_filters_and_tags(client, db, leads, auth):
    gamma = leads["Gamma"]
    assert Decimal(gamma["score"]["total"]) == 0
    r = await client.put(
        "/api/score-overrides",
        headers=auth,
        json={"target_type": "lead", "target_id": gamma["id"], "field": "total", "human_value": 90},
    )
    assert r.status_code == 200, r.text
    assert (await _titles(client))[0] == "Gamma"
    assert await _titles(client, min_lead_score=80) == ["Gamma"]
    after = await _lead(client, gamma["id"])
    assert Decimal(after["score"]["total"]) == 0  # the code value stays
    assert [(o["field"], Decimal(o["human_value"])) for o in after["overrides"]] == [("total", Decimal(90))]
    assert "high-potential" in _tag_names(after) and "hot-lead" not in _tag_names(after)  # no activity

    for bad, code in (
        ({"field": "total", "human_value": 101}, 422),
        ({"field": "audience", "human_value": 5}, 400),
    ):
        resp = await client.put(
            "/api/score-overrides",
            headers=auth,
            json={"target_type": "lead", "target_id": gamma["id"], **bad},
        )
        assert resp.status_code == code, resp.text
    thumb_range = await client.put(
        "/api/score-overrides",
        headers=auth,
        json={
            "target_type": "thumbnail_analysis",
            "target_id": 1,
            "field": "overall_score",
            "human_value": 50,
        },
    )
    assert thumb_range.status_code == 400 and thumb_range.json()["error"]["code"] == "out_of_range"
    missing = await client.put(
        "/api/score-overrides",
        headers=auth,
        json={"target_type": "lead", "target_id": 999_999, "field": "total", "human_value": 5},
    )
    assert missing.status_code == 404


# ---------------------------------------------------------------------------
# Approvals gate (used by offers / outreach later)
# ---------------------------------------------------------------------------
async def test_approval_gate_binds_to_content(db, owner):
    user, ws = owner
    content = {"subject": "Free thumbnail", "body": "Hi!", "contact": "a@b.c"}
    h = content_hash(content)
    assert h == content_hash(dict(reversed(list(content.items()))))  # key order does not matter
    with pytest.raises(ConflictError) as e:
        await require_approval(db, ws.id, ApprovalTarget.OFFER, 1, h)
    assert e.value.code == "not_approved"
    await record_decision(db, ws.id, user.id, ApprovalTarget.OFFER, 1, ApprovalDecision.APPROVED, h)
    assert (
        await require_approval(db, ws.id, ApprovalTarget.OFFER, 1, h)
    ).decision == ApprovalDecision.APPROVED
    edited = content_hash(content | {"body": "Hi there!"})
    with pytest.raises(ConflictError) as e:
        await require_approval(db, ws.id, ApprovalTarget.OFFER, 1, edited)
    assert e.value.code == "approval_outdated"
    await record_decision(db, ws.id, user.id, ApprovalTarget.OFFER, 1, ApprovalDecision.REJECTED, h, "no")
    with pytest.raises(ConflictError):
        await require_approval(db, ws.id, ApprovalTarget.OFFER, 1, h)  # the latest decision counts
    with pytest.raises(ConflictError):  # decisions are per workspace
        await require_approval(db, ws.id + 10_000, ApprovalTarget.OFFER, 1, h)


# ---------------------------------------------------------------------------
# Roles and isolation
# ---------------------------------------------------------------------------
async def test_roles_and_isolation(client, db, world, leads, auth, other_owner, settings):
    lead_id = leads["Alpha"]["id"]
    await add_member(db, world["ws"], "viewer@example.com", WorkspaceRole.VIEWER)
    await client.post("/api/auth/logout", headers=auth)
    await login(client, "viewer@example.com")
    h = csrf_headers(client, settings)
    assert (
        await client.post(f"/api/leads/{lead_id}/status", headers=h, json={"status": "shortlisted"})
    ).status_code == 403
    bulk = {"selection": {"lead_ids": [lead_id]}, "action": {"type": "favorite"}}
    assert (await client.post("/api/leads/bulk", headers=h, json=bulk)).status_code == 403
    assert (await client.post("/api/tags", headers=h, json={"name": "v"})).status_code == 403
    override = {"target_type": "lead", "target_id": lead_id, "field": "total", "human_value": 1}
    assert (await client.put("/api/score-overrides", headers=h, json=override)).status_code == 403
    # favorites are personal: a viewer keeps their own, invisible to the owner
    assert (await client.put(f"/api/favorites/channel/{world['alpha'].id}", headers=h)).status_code == 200
    assert (await client.put(f"/api/favorites/channel/{world['alpha'].id}")).status_code == 403  # no CSRF
    assert (await _lead(client, lead_id))["is_favorite"] is True

    await client.post("/api/auth/logout", headers=h)
    await login(client, "owner@example.com")
    assert (await _lead(client, lead_id))["is_favorite"] is False

    await client.post("/api/auth/logout", headers=csrf_headers(client, settings))
    await login(client, "other@example.com")
    h = csrf_headers(client, settings)
    assert (
        await client.post(f"/api/leads/{lead_id}/status", headers=h, json={"status": "shortlisted"})
    ).status_code == 404
    assert (await client.post("/api/leads/bulk", headers=h, json=bulk)).status_code == 404
    assert (await client.put("/api/score-overrides", headers=h, json=override)).status_code == 404
    assert [t for t in (await client.get("/api/tags")).json() if not t["is_system"]] == []
    assert (await client.get("/api/feedback/summary")).json()["events"] == 0
    assert (
        await db.scalar(select(func.count()).select_from(Lead).where(Lead.status != LeadStatus.ANALYZED)) == 1
    )
    assert (
        await db.scalar(
            select(func.count())
            .select_from(LeadStatusHistory)
            .where(LeadStatusHistory.actor_user_id.is_not(None))
        )
        == 0
    )
