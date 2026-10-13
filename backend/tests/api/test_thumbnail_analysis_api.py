"""Phase 3.5 API: starting the confirmed AI audit, cached images in the estimate, results with the
deterministic overall score, scoring profile (weights) and roles."""

from __future__ import annotations

from decimal import Decimal

import factories as f
import pytest
from conftest import add_member, csrf_headers, login
from sqlalchemy import select, update

from app.domains.ai.models import PromptTemplate
from app.domains.analysis.audit import SUBSCORES
from app.domains.analysis.models import AILevel, ThumbnailAnalysis
from app.domains.identity.models import WorkspaceRole
from app.domains.jobs.models import JobRun
from app.domains.media.models import ImageSource
from app.domains.media.service import ensure_image_metrics, store_image
from app.domains.projects.models import ProjectStatus


async def _asset(db, storage, color):
    a = await store_image(db, storage, f.png_bytes(color, (160, 90)), ImageSource.YOUTUBE_THUMBNAIL)
    await ensure_image_metrics(db, storage, a)
    return a


@pytest.fixture
async def world(db, owner, storage, settings, monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "mock")
    p = await f.project(db, owner[1].id, "Audit")
    a = await f.channel(db, projects=[p], title="Alpha")
    b = await f.channel(db, projects=[p], title="Beta")
    assets = [await _asset(db, storage, c) for c in ((240, 240, 240), (10, 10, 10), (200, 20, 20))]
    v = [
        await f.video(db, a, days_ago=1, asset=assets[0], view_count=100),
        await f.video(db, a, days_ago=2, asset=assets[1], view_count=900),
        await f.video(db, b, days_ago=1, asset=assets[2], view_count=50),
    ]
    return {"project": p, "channels": (a, b), "assets": assets, "videos": v}


@pytest.fixture
async def auth(client, owner, settings):
    await login(client, "owner@example.com")
    return csrf_headers(client, settings)


async def _template(db) -> PromptTemplate:
    t = PromptTemplate(
        name="thumbnail_analysis", version=1, content_hash="x" * 64, body="b", output_schema={}
    )
    db.add(t)
    await db.flush()
    return t


async def _analysis(db, template, asset, *, provider="mock", model_key="mock-vision", codes=(), **scores):
    values = {k: Decimal(str(scores.get(k, 5))) for k in SUBSCORES}
    row = ThumbnailAnalysis(
        image_asset_id=asset.id, prompt_template_id=template.id, provider=provider, model_key=model_key,
        detail="low", is_current=True, has_face=False, text_present=False, text_word_count_est=0,
        focal_point="-", background_separation=AILevel.LOW, clutter_level=AILevel.LOW,
        improvement_opportunity=AILevel(scores.get("opportunity", "medium")),
        problems=[{"code": c, "severity": "high", "description": c} for c in codes],
        recommendations=[], problem_codes=sorted(set(codes)), visual_summary="s", **values,
    )
    db.add(row)
    await db.flush()
    return row


async def _estimate(client, p, auth, body=None):
    r = await client.post(f"/api/projects/{p.id}/thumbnail-analysis/estimate", headers=auth, json=body or {})
    assert r.status_code == 200, r.text
    return r.json()


async def test_start_requires_the_confirmed_estimate(client, db, world, auth):
    p = world["project"]
    url = f"/api/projects/{p.id}/thumbnail-analysis"
    e = await _estimate(client, p, auth)
    # solid colours share pHash 0 but are distinct images: 3 items (pHash reuse happens in the job)
    assert e["items"] == 3 and e["already_analyzed"] == 0

    wrong = await client.post(url, headers=auth, json={"confirm_token": "nope"})
    assert wrong.status_code == 409 and wrong.json()["error"]["code"] == "confirmation_required"
    assert wrong.json()["error"]["details"]["confirm_token"] == e["confirm_token"]
    changed = await client.post(
        url, headers=auth, json={"confirm_token": e["confirm_token"], "detail": "high"}
    )
    assert changed.status_code == 409  # detail differs from the confirmed estimate

    ok = await client.post(url, headers=auth, json={"confirm_token": e["confirm_token"]})
    assert ok.status_code == 202, ok.text
    body = ok.json()
    assert body["created"] and body["items"] == 3 and body["job"]["type"] == "thumbnail_analysis"
    job = await db.get(JobRun, body["job"]["id"])
    assert job.params == {
        "project_id": p.id, "asset_ids": sorted(a.id for a in world["assets"]), "detail": "low",
        "model_key": "mock-vision",
    }
    assert job.progress_total == 3 and job.estimated_cost_usd == 0 and job.queue == "bulk"
    again = (await client.post(url, headers=auth, json={"confirm_token": e["confirm_token"]})).json()
    assert not again["created"] and again["job"]["id"] == job.id


async def test_already_analysed_images_are_free_and_not_resent(client, db, world, auth):
    p = world["project"]
    t = await _template(db)
    await _analysis(db, t, world["assets"][0])
    await _analysis(db, t, world["assets"][1], provider="vibecode", model_key="vc-vision-standard")
    e = await _estimate(client, p, auth)
    # the mock audit counts for the mock provider; the vibecode one does not
    assert (e["items"], e["already_analyzed"]) == (2, 1)
    started = (
        await client.post(f"/api/projects/{p.id}/thumbnail-analysis", headers=auth,
                          json={"confirm_token": e["confirm_token"]})
    ).json()
    job = await db.get(JobRun, started["job"]["id"])
    assert job.params["asset_ids"] == sorted(a.id for a in world["assets"][1:])

    # the job finished: both remaining images now have mock audits
    await _analysis(db, t, world["assets"][2])
    await db.execute(
        update(ThumbnailAnalysis)
        .where(ThumbnailAnalysis.image_asset_id == world["assets"][1].id)
        .values(model_key="mock-vision", provider="mock")
    )
    done = await _estimate(client, p, auth)
    assert (done["items"], done["already_analyzed"]) == (0, 3)
    nothing = (
        await client.post(f"/api/projects/{p.id}/thumbnail-analysis", headers=auth,
                          json={"confirm_token": done["confirm_token"]})
    ).json()
    assert nothing["job"] is None and nothing["items"] == 0 and nothing["already_analyzed"] == 3


async def test_start_rejects_archived_project_and_unconfigured_ai(
    client, db, world, auth, settings, monkeypatch
):
    p = world["project"]
    e = await _estimate(client, p, auth)
    monkeypatch.setattr(settings, "ai_provider", None)
    monkeypatch.setattr(settings, "demo_mode", False)
    r = await client.post(f"/api/projects/{p.id}/thumbnail-analysis", headers=auth,
                          json={"confirm_token": e["confirm_token"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "ai_not_configured"
    p.status = ProjectStatus.ARCHIVED
    await db.flush()
    r = await client.post(f"/api/projects/{p.id}/thumbnail-analysis", headers=auth,
                          json={"confirm_token": e["confirm_token"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "project_archived"


async def test_results_overall_score_filters_and_summary(client, db, world, auth):
    p, v = world["project"], world["videos"]
    t = await _template(db)
    a0, a1, a2 = world["assets"]
    await _analysis(db, t, a0, codes=["cluttered", "small_text"], composition_score=9, opportunity="low")
    await _analysis(
        db, t, a1, codes=["small_text"], composition_score=1, redesign_potential=10, opportunity="high"
    )
    await _analysis(db, t, a2, provider="vibecode", model_key="vc-vision-standard", opportunity="high")
    url = f"/api/projects/{p.id}/thumbnail-analyses"

    d = (await client.get(url)).json()
    assert d["total"] == 3 and d["scoring"] == {
        "kind": "thumbnail", "version": 0, "is_default": True, "weights": d["scoring"]["weights"]}
    # default weights: 10 subscores, redesign_potential not counted
    overall = {i["video_id"]: Decimal(i["analysis"]["overall_score"]) for i in d["items"]}
    assert overall == {v[0].id: Decimal("5.40"), v[1].id: Decimal("4.60"), v[2].id: Decimal("5.00")}
    assert [i["video_id"] for i in d["items"]] == [v[1].id, v[2].id, v[0].id]  # weakest first
    first = d["items"][0]
    assert first["channel_title"] == "Alpha" and first["analysis"]["problems"][0]["code"] == "small_text"
    s = d["summary"]
    assert (s["videos_analyzed"], Decimal(s["avg_overall"]), s["mock_results"]) == (3, Decimal("5.00"), 2)
    assert s["opportunity"] == {"low": 1, "high": 2}
    assert s["top_problems"] == [{"code": "small_text", "count": 2}, {"code": "cluttered", "count": 1}]

    async def ids(**params):
        return [i["video_id"] for i in (await client.get(url, params=params)).json()["items"]]

    assert await ids(problem="cluttered") == [v[0].id]
    assert await ids(opportunity="high") == [v[1].id, v[2].id]
    assert await ids(max_overall="5") == [v[1].id, v[2].id]
    assert await ids(channel_id=world["channels"][1].id) == [v[2].id]
    assert await ids(sort="views", order="desc") == [v[1].id, v[0].id, v[2].id]
    assert (await ids(sort="redesign_potential", order="desc"))[0] == v[1].id
    assert (await client.get(url, params={"problem": "made_up"})).status_code == 422

    # new weights change the score of stored audits without re-running anything
    put = await client.put("/api/scoring-profiles/thumbnail", headers=auth,
                           json={k: 0 for k in SUBSCORES} | {"composition_score": 1})
    assert put.status_code == 200 and put.json()["version"] == 1
    d2 = (await client.get(url)).json()
    rescored = {i["video_id"]: Decimal(i["analysis"]["overall_score"]) for i in d2["items"]}
    assert rescored[v[0].id] == Decimal("9.00")
    assert d2["scoring"]["version"] == 1


async def test_results_are_project_scoped(client, db, world, auth, other_owner):
    t = await _template(db)
    await _analysis(db, t, world["assets"][0])
    other = await f.project(db, world["project"].workspace_id, "Other project")
    assert (await client.get(f"/api/projects/{other.id}/thumbnail-analyses")).json()["total"] == 0
    foreign = await f.project(db, other_owner[1].id)
    assert (await client.get(f"/api/projects/{foreign.id}/thumbnail-analyses")).status_code == 404


async def test_scoring_profile_versions_validation_and_roles(client, db, owner, auth, settings):
    url = "/api/scoring-profiles/thumbnail"
    default = (await client.get(url)).json()
    assert default["version"] == 0 and default["is_default"] and default["weights"]["redesign_potential"] == 0
    assert (await client.put(url, headers=auth, json={k: 0 for k in SUBSCORES})).status_code == 422
    assert (await client.put(url, headers=auth, json={"text_score": 11})).status_code == 422
    v1 = (await client.put(url, headers=auth, json={"text_score": 3})).json()
    v2 = (await client.put(url, headers=auth, json={"text_score": 2})).json()
    assert (v1["version"], v2["version"], v2["weights"]["text_score"], v2["is_default"]) == (1, 2, 2, False)
    assert (await client.get(url)).json()["version"] == 2

    await add_member(db, owner[1], "operator@example.com", WorkspaceRole.OPERATOR)
    await client.post("/api/auth/logout", headers=auth)
    await login(client, "operator@example.com")
    op = csrf_headers(client, settings)
    assert (await client.get(url)).status_code == 200
    assert (await client.put(url, headers=op, json={"text_score": 1})).status_code == 403


async def test_operator_can_start_viewer_cannot(client, db, owner, world, auth, settings):
    p = world["project"]
    e = await _estimate(client, p, auth)
    await add_member(db, owner[1], "viewer@example.com", WorkspaceRole.VIEWER)
    await client.post("/api/auth/logout", headers=auth)
    await login(client, "viewer@example.com")
    viewer = csrf_headers(client, settings)
    r = await client.post(f"/api/projects/{p.id}/thumbnail-analysis", headers=viewer,
                          json={"confirm_token": e["confirm_token"]})
    assert r.status_code == 403
    assert (await client.get(f"/api/projects/{p.id}/thumbnail-analyses")).status_code == 200
    assert not list(await db.scalars(select(JobRun).where(JobRun.type == "thumbnail_analysis")))
