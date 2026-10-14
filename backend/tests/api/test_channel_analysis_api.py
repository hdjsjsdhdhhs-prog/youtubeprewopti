"""Phase 3.6 API: estimate and confirmed start of the comparative channel audit, code-computed aggregates
(workspace weights), project list, channel report, scoping and roles."""

from __future__ import annotations

from decimal import Decimal

import factories as f
import pytest
from conftest import add_member, csrf_headers, login
from sqlalchemy import select

from app.domains.analysis.audit import SUBSCORES
from app.domains.analysis.models import AILevel, ChannelAnalysis
from app.domains.identity.models import WorkspaceRole
from app.domains.jobs.models import JobRun
from app.domains.media.models import ImageSource
from app.domains.media.service import store_image
from app.domains.projects.models import ProjectStatus


@pytest.fixture
async def world(db, owner, storage, settings, monkeypatch):
    """Alpha: 4 audited thumbnails with overall 2, 4, 6, 8; Beta: 3 audited (5 each); Gamma: 1 audited."""
    monkeypatch.setattr(settings, "ai_provider", "mock")
    p = await f.project(db, owner[1].id, "Channels")
    t = await f.prompt_template(db)
    seq = iter(range(500, 600))

    async def audited(ch, days_ago, score):
        asset = await store_image(db, storage, f.noise_png(next(seq)), ImageSource.YOUTUBE_THUMBNAIL)
        v = await f.video(db, ch, days_ago=days_ago, asset=asset)
        await f.thumbnail_analysis(db, t, asset, **{k: score for k in SUBSCORES})
        return v

    alpha = await f.channel(db, projects=[p], title="Alpha", subscriber_count=50_000)
    beta = await f.channel(db, projects=[p], title="Beta", subscriber_count=1_000)
    gamma = await f.channel(db, projects=[p], title="Gamma")
    await f.channel(db, projects=[p], title="Delta")  # nothing audited
    av = [await audited(alpha, d, s) for d, s in ((1, 2), (2, 4), (3, 6), (4, 8))]
    bv = [await audited(beta, d, 5) for d in (1, 2, 3)]
    await audited(gamma, 1, 5)
    ct = await f.prompt_template(db, "channel_analysis")
    return {
        "project": p, "template": t, "channel_template": ct, "alpha": alpha, "beta": beta, "gamma": gamma,
        "av": av, "bv": bv,
    }


@pytest.fixture
async def auth(client, owner, settings):
    await login(client, "owner@example.com")
    return csrf_headers(client, settings)


async def _estimate(client, p, auth, body=None):
    r = await client.post(f"/api/projects/{p.id}/channel-analysis/estimate", headers=auth, json=body or {})
    assert r.status_code == 200, r.text
    return r.json()


async def _stored(db, world, channel, videos, *, opportunity="high", consistency=4, outdated=6):
    row = ChannelAnalysis(
        channel_id=channel.id, prompt_template_id=world["channel_template"].id, provider="mock",
        model_key="mock-vision", detail="low", inputs_digest=f"d{channel.id}", is_current=True,
        video_ids=[v.id for v in videos], thumbnail_analysis_ids=[1] * len(videos),
        visual_consistency=Decimal(consistency), repeated_template=True,
        outdated_style_score=Decimal(outdated),
        has_design_system=False, visual_opportunity=AILevel(opportunity), patterns=[],
        recurring_problems=[{"code": "small_text", "frequency": "high", "description": "x"}],
        recurring_problem_codes=["small_text"], weakest_video_ids=[videos[0].id],
        redesign_candidates=[{"video_id": videos[0].id, "why": "идея"}], summary="Итог.",
    )
    db.add(row)
    await db.flush()
    return row


async def test_estimate_and_confirmed_start(client, db, world, auth):
    p = world["project"]
    url = f"/api/projects/{p.id}/channel-analysis"
    e = await _estimate(client, p, auth)
    assert e["preview"] == {"channels_in_project": 4, "channels_ready": 2, "channels_not_enough": 1,
                            "channels_without_audits": 1, "min_thumbnails": 3}
    assert (e["items"], e["already_analyzed"], e["images"], e["videos_per_channel"]) == (2, 0, 7, 8)
    assert e["model_key"] == "mock-vision" and Decimal(e["estimated_cost_usd"]) == 0
    assert e["estimated_input_tokens"] == 2 * 3000 + 7 * 85  # task estimate + low-detail images
    three = await _estimate(client, p, auth, {"videos_per_channel": 3})
    assert three["images"] == 6 and three["confirm_token"] != e["confirm_token"]
    bad = await client.post(f"{url}/estimate", headers=auth, json={"videos_per_channel": 2})
    assert bad.status_code == 422

    wrong = await client.post(url, headers=auth, json={"confirm_token": "nope"})
    assert wrong.status_code == 409
    assert wrong.json()["error"]["details"]["confirm_token"] == e["confirm_token"]
    changed = await client.post(
        url, headers=auth, json={"confirm_token": e["confirm_token"], "detail": "high"}
    )
    assert changed.status_code == 409

    ok = await client.post(url, headers=auth, json={"confirm_token": e["confirm_token"]})
    assert ok.status_code == 202, ok.text
    body = ok.json()
    assert body["created"] and body["items"] == 2 and body["job"]["type"] == "channel_analysis"
    job = await db.get(JobRun, body["job"]["id"])
    assert job.params == {
        "project_id": p.id, "channel_ids": sorted([world["alpha"].id, world["beta"].id]),
        "videos_per_channel": 8, "detail": "low", "model_key": "mock-vision",
    }
    assert job.progress_total == 2 and job.queue == "bulk"
    again = (await client.post(url, headers=auth, json={"confirm_token": e["confirm_token"]})).json()
    assert not again["created"] and again["job"]["id"] == job.id


async def test_channels_with_the_same_inputs_are_free(client, db, world, auth):
    from app.domains.analysis.channel_pipeline import inputs_digest, select_channel_inputs

    p = world["project"]
    inputs = await select_channel_inputs(db, p.id, 8)
    row = await _stored(db, world, world["alpha"], world["av"])
    row.inputs_digest = inputs_digest(inputs[world["alpha"].id])
    await db.flush()
    e = await _estimate(client, p, auth)
    assert (e["items"], e["already_analyzed"], e["images"]) == (1, 1, 3)
    started = (await client.post(f"/api/projects/{p.id}/channel-analysis", headers=auth,
                                 json={"confirm_token": e["confirm_token"]})).json()
    assert (await db.get(JobRun, started["job"]["id"])).params["channel_ids"] == [world["beta"].id]


async def test_start_rejects_archived_project_and_unconfigured_ai(
    client, db, world, auth, settings, monkeypatch
):
    p = world["project"]
    e = await _estimate(client, p, auth)
    monkeypatch.setattr(settings, "ai_provider", None)
    monkeypatch.setattr(settings, "demo_mode", False)
    r = await client.post(f"/api/projects/{p.id}/channel-analysis", headers=auth,
                          json={"confirm_token": e["confirm_token"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "ai_not_configured"
    p.status = ProjectStatus.ARCHIVED
    await db.flush()
    r = await client.post(f"/api/projects/{p.id}/channel-analysis", headers=auth,
                          json={"confirm_token": e["confirm_token"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "project_archived"


async def test_report_aggregates_follow_the_workspace_weights(client, db, world, auth):
    alpha, av = world["alpha"], world["av"]
    await _stored(db, world, alpha, av)
    url = f"/api/channels/{alpha.id}/visual-audit"
    d = (await client.get(url)).json()
    assert d["analysis"]["summary"] == "Итог." and d["analysis"]["video_ids"] == [v.id for v in av]
    assert d["analysis"]["redesign_candidates"] == [{"video_id": av[0].id, "why": "идея"}]
    agg = d["aggregates"]
    assert agg["thumbnails_analyzed"] == 4
    assert (Decimal(agg["avg_score"]), Decimal(agg["median_score"])) == (Decimal("5.00"), Decimal("5.00"))
    # 2, 4 weak (< 5); 6 medium; 8 strong (≥ 7)
    assert (Decimal(agg["pct_weak"]), Decimal(agg["pct_medium"]), Decimal(agg["pct_strong"])) == (
        Decimal("50.0"), Decimal("25.0"), Decimal("25.0"))
    assert (agg["best_video_id"], agg["worst_video_id"]) == (av[3].id, av[0].id)
    assert [v["video_id"] for v in d["videos"]] == [v.id for v in av]
    assert [Decimal(v["overall_score"]) for v in d["videos"]] == [Decimal(x) for x in ("2", "4", "6", "8")]

    # weights change the aggregates without re-running anything; redesign_potential (= score too) only
    put = await client.put("/api/scoring-profiles/thumbnail", headers=auth,
                           json={k: 0 for k in SUBSCORES} | {"redesign_potential": 1})
    assert put.status_code == 200
    assert Decimal((await client.get(url)).json()["aggregates"]["avg_score"]) == Decimal("5.00")

    beta_report = (await client.get(f"/api/channels/{world['beta'].id}/visual-audit")).json()
    assert beta_report["analysis"] is None and beta_report["aggregates"] is None
    assert beta_report["videos"] == []


async def test_project_list_sort_filters_and_summary(client, db, world, auth):
    p, alpha, beta = world["project"], world["alpha"], world["beta"]
    await _stored(db, world, alpha, world["av"], opportunity="high", consistency=2)
    await _stored(db, world, beta, world["bv"], opportunity="low", consistency=9)
    url = f"/api/projects/{p.id}/channel-analyses"
    d = (await client.get(url)).json()
    assert d["total"] == 2 and d["summary"] == {
        "channels_analyzed": 2, "opportunity": {"high": 1, "low": 1}, "mock_results": 2}
    # avg: alpha 5.00, beta 5.00 => tie broken by channel id
    assert [i["channel_id"] for i in d["items"]] == [alpha.id, beta.id]
    first = d["items"][0]
    assert first["title"] == "Alpha" and first["subscriber_count"] == 50_000
    assert first["aggregates"]["thumbnails_analyzed"] == 4

    async def ids(**params):
        r = await client.get(url, params=params)
        assert r.status_code == 200, r.text
        return [i["channel_id"] for i in r.json()["items"]]

    assert await ids(sort="visual_consistency", order="desc") == [beta.id, alpha.id]
    assert await ids(sort="subscribers") == [beta.id, alpha.id]
    assert await ids(opportunity="low") == [beta.id]
    assert await ids(problem="small_text") == [alpha.id, beta.id]
    assert await ids(problem="cluttered") == []
    assert (await client.get(url, params={"sort": "made_up"})).status_code == 422


async def test_results_are_scoped(client, db, world, auth, other_owner):
    await _stored(db, world, world["alpha"], world["av"])
    other = await f.project(db, world["project"].workspace_id, "Other project")
    assert (await client.get(f"/api/projects/{other.id}/channel-analyses")).json()["total"] == 0
    foreign_project = await f.project(db, other_owner[1].id)
    assert (await client.get(f"/api/projects/{foreign_project.id}/channel-analyses")).status_code == 404
    foreign_channel = await f.channel(db, projects=[foreign_project])
    assert (await client.get(f"/api/channels/{foreign_channel.id}/visual-audit")).status_code == 404


async def test_operator_can_start_viewer_cannot(client, db, owner, world, auth, settings):
    p = world["project"]
    e = await _estimate(client, p, auth)
    await add_member(db, owner[1], "viewer@example.com", WorkspaceRole.VIEWER)
    await client.post("/api/auth/logout", headers=auth)
    await login(client, "viewer@example.com")
    viewer = csrf_headers(client, settings)
    r = await client.post(f"/api/projects/{p.id}/channel-analysis", headers=viewer,
                          json={"confirm_token": e["confirm_token"]})
    assert r.status_code == 403
    assert (await client.get(f"/api/projects/{p.id}/channel-analyses")).status_code == 200
    assert not list(await db.scalars(select(JobRun).where(JobRun.type == "channel_analysis")))
