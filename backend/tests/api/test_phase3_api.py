"""Phase 3.1–3.4 API: project thumbnail ingestion, metrics in video listings, prefilter, cost estimate
(budget gate), budgets and the AI model registry."""

from __future__ import annotations

from decimal import Decimal

import factories as f
import pytest
from conftest import add_member, csrf_headers, login

from app.domains.identity.models import WorkspaceRole
from app.domains.media.models import ImageSource
from app.domains.media.service import ensure_image_metrics, store_image
from app.domains.projects.models import ProjectStatus


async def _asset(db, storage, color, *, metrics=True):
    a = await store_image(db, storage, f.png_bytes(color, (160, 90)), ImageSource.YOUTUBE_THUMBNAIL)
    if metrics:
        await ensure_image_metrics(db, storage, a)
    return a


@pytest.fixture
async def world(db, owner, storage):
    """Project with: channel A (10k subs) — a short, 3 analysable videos (bright, dark, not downloaded) and
    one older video; channel B (100 subs) — reuses A's bright thumbnail; channel C — stored, no metrics."""
    ws = owner[1].id
    p = await f.project(db, ws, "Phase3")
    bright, dark, older = [
        await _asset(db, storage, c) for c in ((240, 240, 240), (10, 10, 10), (120, 60, 60))
    ]
    raw = await _asset(db, storage, (0, 120, 0), metrics=False)
    a = await f.channel(db, projects=[p], subscriber_count=10_000)
    b = await f.channel(db, projects=[p], subscriber_count=100)
    c = await f.channel(db, projects=[p], subscriber_count=5_000)
    v = {
        "short": await f.video(db, a, days_ago=0, is_short=True),
        "a1": await f.video(db, a, days_ago=1, asset=bright, view_count=5000),
        "a2": await f.video(db, a, days_ago=2, asset=dark, view_count=50),
        "a3": await f.video(db, a, days_ago=3),
        "a4": await f.video(db, a, days_ago=4, asset=older),
        "b1": await f.video(db, b, days_ago=1, asset=bright),
        "c1": await f.video(db, c, days_ago=1, asset=raw),
    }
    return {"project": p, "channels": (a, b, c), "videos": v, "assets": {"bright": bright, "dark": dark}}


@pytest.fixture
async def auth(client, owner, settings):
    await login(client, "owner@example.com")
    return csrf_headers(client, settings)


async def test_project_ingestion_queues_missing_downloads_and_metrics(client, db, world, auth, monkeypatch):
    p, v = world["project"], world["videos"]
    stats = (await client.get(f"/api/projects/{p.id}/thumbnails/stats")).json()
    assert stats == {
        "videos": 7,
        "with_thumbnail": 7,
        "downloaded": 5,
        "pending": 2,
        "failed": 0,
        "with_metrics": 4,
        "metrics_algo_version": 1,
    }

    r = await client.post(f"/api/projects/{p.id}/thumbnails/download", headers=auth)
    assert r.status_code == 202
    body = r.json()
    assert body["created"] and (body["items"], body["remaining"]) == (3, 0)
    detail = (await client.get(f"/api/jobs/{body['job']['id']}")).json()
    assert detail["params"] == {
        "project_id": p.id,
        "video_ids": sorted([v["short"].id, v["a3"].id, v["c1"].id]),
    }
    assert (await client.get("/api/jobs", params={"project_id": p.id, "type": "thumbnail_download"})).json()[
        "total"
    ] == 1

    again = (await client.post(f"/api/projects/{p.id}/thumbnails/download", headers=auth)).json()
    assert not again["created"] and again["job"]["id"] == body["job"]["id"]


async def test_project_ingestion_cap_archive_and_access(client, db, world, auth, other_owner, monkeypatch):
    p = world["project"]
    monkeypatch.setattr("app.domains.media.service.MAX_VIDEOS_PER_PROJECT_JOB", 1)
    body = (await client.post(f"/api/projects/{p.id}/thumbnails/download", headers=auth)).json()
    assert (body["items"], body["remaining"]) == (1, 2)
    assert body["job"]["progress_total"] == 1

    p.status = ProjectStatus.ARCHIVED
    await db.flush()
    r = await client.post(f"/api/projects/{p.id}/thumbnails/download", headers=auth)
    assert r.status_code == 409 and r.json()["error"]["code"] == "project_archived"
    foreign = await f.project(db, other_owner[1].id)
    assert (
        await client.post(f"/api/projects/{foreign.id}/thumbnails/download", headers=auth)
    ).status_code == 404
    assert (await client.get(f"/api/projects/{foreign.id}/thumbnails/stats")).status_code == 404


async def test_video_listing_includes_metrics(client, world, auth):
    a = world["channels"][0]
    items = {i["id"]: i for i in (await client.get(f"/api/channels/{a.id}/videos")).json()["items"]}
    bright = items[world["videos"]["a1"].id]["thumbnail"]["metrics"]
    assert bright["luminance_mean"] > 0.9 and bright["contrast_rms"] == 0 and bright["algo_version"] == 1
    assert bright["dominant_colors"][0] == {"hex": "#f0f0f0", "share": 1.0}
    assert items[world["videos"]["a3"].id]["thumbnail"]["metrics"] is None  # not downloaded
    assert (await client.get(f"/api/videos/{world['videos']['a2'].id}")).json()["thumbnail"]["metrics"][
        "luminance_mean"
    ] < 0.1


async def _preview(client, p, auth, body=None):
    r = await client.post(f"/api/projects/{p.id}/prefilter/preview", headers=auth, json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def test_prefilter_selection(client, db, world, auth):
    p, v = world["project"], world["videos"]
    d = await _preview(client, p, auth, {"videos_per_channel": 3})
    # A: a1, a2, a3 (short excluded, a4 beyond 3 newest) · B: b1 · C: c1 (no metrics)
    assert (d["channels_in_project"], d["channels_matched"], d["videos_considered"]) == (3, 3, 5)
    assert (d["thumbnails_not_ready"], d["excluded_by_metrics"], d["thumbnails_selected"]) == (2, 0, 3)
    assert set(d["sample_video_ids"]) == {v["a1"].id, v["a2"].id, v["b1"].id}

    dark_only = await _preview(
        client, p, auth, {"videos_per_channel": 3, "metrics": {"luminance_mean": {"max": 0.5}}}
    )
    assert (dark_only["thumbnails_selected"], dark_only["excluded_by_metrics"]) == (1, 2)
    assert dark_only["sample_video_ids"] == [v["a2"].id]

    with_shorts = await _preview(client, p, auth, {"videos_per_channel": 3, "exclude_shorts": False})
    assert with_shorts["videos_considered"] == 5 and v["a3"].id not in with_shorts["sample_video_ids"]
    popular = await _preview(client, p, auth, {"videos_per_channel": 3, "min_video_views": 1000})
    assert popular["sample_video_ids"] == [v["a1"].id]

    # the project's saved channel filters narrow the channels (unless switched off)
    p.filter_settings = {"min_subscribers": 1000}
    await db.flush()
    narrowed = await _preview(client, p, auth, {"videos_per_channel": 3})
    assert narrowed["channels_matched"] == 2 and v["b1"].id not in narrowed["sample_video_ids"]
    everything = await _preview(client, p, auth, {"videos_per_channel": 3, "apply_channel_filters": False})
    assert everything["channels_matched"] == 3


async def test_prefilter_settings_are_saved_validated_and_used(client, world, auth):
    p = world["project"]
    url = f"/api/projects/{p.id}"
    bad = [
        {"videos_per_channel": 0},
        {"metrics": {"luminance_mean": {"min": 0.8, "max": 0.2}}},
        {"unknown": 1},
    ]
    for settings in bad:
        r = await client.patch(url, headers=auth, json={"prefilter_settings": settings})
        assert r.status_code == 422, settings
    saved = await client.patch(url, headers=auth, json={"prefilter_settings": {"videos_per_channel": 1}})
    assert saved.status_code == 200 and saved.json()["prefilter_settings"]["videos_per_channel"] == 1
    d = await _preview(client, p, auth)  # no body => saved settings
    assert d["videos_considered"] == 3  # newest non-short per channel: a1, b1, c1
    reset = await client.patch(url, headers=auth, json={"prefilter_settings": None})
    assert reset.json()["prefilter_settings"] == {}


async def _estimate(client, p, auth, body=None):
    r = await client.post(f"/api/projects/{p.id}/thumbnail-analysis/estimate", headers=auth, json=body or {})
    assert r.status_code == 200, r.text
    return r.json()


async def test_estimate_with_mock_provider(client, world, auth, settings, monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "mock")
    p = world["project"]
    e = await _estimate(client, p, auth, {"prefilter": {"videos_per_channel": 3}})
    assert (e["provider"], e["model_key"], e["api_model_id"]) == ("mock", "mock-vision", "mock-vision-1")
    assert e["items"] == 2  # a1 and b1 share one image asset => analysed once
    assert e["preview"]["thumbnails_selected"] == 3
    assert Decimal(e["estimated_cost_usd"]) == 0 and e["pricing_verified"] and e["within_budgets"]
    assert e["budgets"] == [] and e["estimated_input_tokens"] == 2 * (1200 + 85)

    same = await _estimate(client, p, auth, {"prefilter": {"videos_per_channel": 3}})
    assert same["confirm_token"] == e["confirm_token"]
    for changed in (
        {"prefilter": {"videos_per_channel": 2}},
        {"prefilter": {"videos_per_channel": 3}, "detail": "high"},
    ):
        assert (await _estimate(client, p, auth, changed))["confirm_token"] != e["confirm_token"]


async def test_estimate_unpriced_openai_and_not_configured(client, world, auth, settings, monkeypatch):
    p = world["project"]
    monkeypatch.setattr(settings, "ai_provider", "openai")
    e = await _estimate(client, p, auth)
    assert (e["provider"], e["model_key"], e["api_model_id"]) == ("openai", "vision-standard", "gpt-6-sol")
    assert e["estimated_cost_usd"] is None and e["cost_per_item_usd"] is None and not e["pricing_verified"]

    monkeypatch.setattr(settings, "ai_provider", None)
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "openai_api_key", None)
    none = await _estimate(client, p, auth)
    # the selection is still computed (default 6 per channel => a4 too): bright, dark, older
    assert none["provider"] is None and none["model_key"] is None and none["items"] == 3


async def test_estimate_reports_budget_checks(client, world, auth, settings, monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "mock")
    p = world["project"]
    created = await client.post(
        "/api/budgets",
        headers=auth,
        json={"scope": "project", "scope_ref": str(p.id), "period": "day", "max_ai_operations": 1},
    )
    assert created.status_code == 201, created.text
    e = await _estimate(client, p, auth, {"prefilter": {"videos_per_channel": 3}})
    assert not e["within_budgets"]
    [check] = e["budgets"]
    assert check["would_exceed"] and "operations limit 1" in check["reason"]


async def test_budget_crud_validation_and_roles(client, db, owner, other_owner, auth, settings):
    ok = await client.post(
        "/api/budgets", headers=auth, json={"scope": "global", "period": "month", "limit_usd": "25.5"}
    )
    assert ok.status_code == 201
    b = ok.json()
    assert (Decimal(b["limit_usd"]), Decimal(b["spent_usd"]), b["operations"]) == (Decimal("25.5"), 0, 0)
    assert Decimal(b["remaining_usd"]) == Decimal("25.5") and b["remaining_operations"] is None

    cases = [
        ({"scope": "global", "scope_ref": "1", "period": "day", "limit_usd": "1"}, 422, None),
        ({"scope": "task", "period": "day", "limit_usd": "1"}, 422, None),
        ({"scope": "global", "period": "day"}, 422, None),
        ({"scope": "global", "period": "month", "max_ai_operations": 5}, 409, "budget_exists"),
        ({"scope": "task", "scope_ref": "nope", "period": "day", "limit_usd": "1"}, 409, "invalid_scope_ref"),
        (
            {"scope": "project", "scope_ref": "abc", "period": "day", "limit_usd": "1"},
            409,
            "invalid_scope_ref",
        ),
    ]
    for body, status, code in cases:
        r = await client.post("/api/budgets", headers=auth, json=body)
        assert r.status_code == status, (body, r.text)
        if code:
            assert r.json()["error"]["code"] == code
    foreign = await f.project(db, other_owner[1].id)
    r = await client.post(
        "/api/budgets",
        headers=auth,
        json={"scope": "project", "scope_ref": str(foreign.id), "period": "day", "limit_usd": "1"},
    )
    assert r.status_code == 404

    patched = await client.patch(f"/api/budgets/{b['id']}", headers=auth, json={"max_ai_operations": 10})
    assert patched.status_code == 200 and patched.json()["max_ai_operations"] == 10
    cleared = await client.patch(
        f"/api/budgets/{b['id']}", headers=auth, json={"limit_usd": None, "max_ai_operations": None}
    )
    assert cleared.status_code == 409 and cleared.json()["error"]["code"] == "budget_without_limit"
    assert len((await client.get("/api/budgets")).json()) == 1
    usage = (await client.get("/api/ai/usage")).json()
    assert [s["period"] for s in usage["spend"]] == ["day", "month", "total"] and len(usage["budgets"]) == 1

    await add_member(db, owner[1], "operator@example.com", WorkspaceRole.OPERATOR)
    await client.post("/api/auth/logout", headers=auth)
    await login(client, "operator@example.com")
    op = csrf_headers(client, settings)
    assert (await client.get("/api/budgets")).status_code == 200
    assert (
        await client.post(
            "/api/budgets", headers=op, json={"scope": "global", "period": "day", "limit_usd": "1"}
        )
    ).status_code == 403
    assert (await client.delete(f"/api/budgets/{b['id']}", headers=op)).status_code == 403

    await client.post("/api/auth/logout", headers=op)
    await login(client, "owner@example.com")
    owner_h = csrf_headers(client, settings)
    assert (await client.delete(f"/api/budgets/{b['id']}", headers=owner_h)).status_code == 204
    assert (await client.get("/api/budgets")).json() == []


async def test_ai_status_and_model_prices(client, auth, settings, monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "mock")
    status = (await client.get("/api/ai/status")).json()
    assert status["provider"] == "mock" and status["configured"]
    models = {m["key"]: m for m in status["models"]}
    assert set(models) == {
        "vision-standard",
        "vision-premium",
        "text-bulk",
        "text-premium",
        "mock-vision",
        "mock-text",
    }
    assert (
        models["vision-standard"]["pricing_verified_at"] is None
        and models["mock-vision"]["pricing_verified_at"]
    )

    url = "/api/ai/models/vision-standard"
    priced = await client.patch(
        url, headers=auth, json={"price_input_per_1m": "2.5", "price_output_per_1m": "10"}
    )
    assert priced.status_code == 200 and priced.json()["pricing_verified_at"] is not None
    half = await client.patch(url, headers=auth, json={"price_output_per_1m": None})
    assert half.json()["pricing_verified_at"] is None and half.json()["price_output_per_1m"] is None
    renamed = await client.patch(url, headers=auth, json={"api_model_id": "gpt-6.1-sol"})
    assert renamed.json()["api_model_id"] == "gpt-6.1-sol"
    assert (
        await client.patch("/api/ai/models/nope", headers=auth, json={"enabled": False})
    ).status_code == 404
    assert (await client.patch(url, headers=auth, json={"price_input_per_1m": "-1"})).status_code == 422
