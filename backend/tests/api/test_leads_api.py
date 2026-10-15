"""Phase 3.7 API: automatic leads, explainable Lead / Priority Score, §9 filters and sorting, weights and
Ideal Lead Profile (rescoring), status history, roles and workspace isolation."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import factories as f
import pytest
from conftest import add_member, csrf_headers, login
from sqlalchemy import func, select

from app.core.state_machine import LeadStatus
from app.domains.analysis.audit import SUBSCORES
from app.domains.analysis.models import AILevel, ChannelAnalysis
from app.domains.identity.models import WorkspaceRole
from app.domains.leads.models import Lead, LeadScore, LeadStatusHistory
from app.domains.leads.service import sync_leads
from app.domains.media.models import ImageSource
from app.domains.media.service import store_image
from app.domains.projects.models import (
    ChannelDiscovery,
    DiscoveryMethod,
    SearchQuery,
    TaxonomyLevel,
    TaxonomyNode,
)
from app.domains.youtube.models import ChannelMetrics, ChannelStatsSnapshot

NOW = datetime.now(UTC)


async def _metrics(db, ch, *, last_days, videos_30d, median, avg=None, trend=None):
    db.add(
        ChannelMetrics(
            channel_id=ch.id,
            computed_at=NOW,
            window_videos=10,
            avg_views=avg or median,
            median_views=median,
            videos_30d=videos_30d,
            last_video_at=NOW - timedelta(days=last_days),
            views_trend=trend,
        )
    )
    await db.flush()


@pytest.fixture
async def world(db, owner, storage):
    """Alpha: weak audited thumbnails (2.0), very active, 50k subs, niche Business > Finance;
    Beta: strong thumbnails (8.0), medium activity, 5k subs; Gamma: nothing but the channel."""
    ws = owner[1]
    p = await f.project(db, ws.id, "Leads")
    t = await f.prompt_template(db)
    business = TaxonomyNode(level=TaxonomyLevel.NICHE, name="Business", slug="business")
    db.add(business)
    await db.flush()
    finance = TaxonomyNode(parent_id=business.id, level=TaxonomyLevel.TOPIC, name="Finance", slug="finance")
    db.add(finance)
    await db.flush()
    query = SearchQuery(project_id=p.id, text="money", text_normalized="money", taxonomy_node_id=finance.id)
    db.add(query)
    await db.flush()

    seq = iter(range(700, 800))

    async def audited(ch, score, redesign):
        asset = await store_image(db, storage, f.noise_png(next(seq)), ImageSource.YOUTUBE_THUMBNAIL)
        await f.video(db, ch, asset=asset)
        await f.thumbnail_analysis(
            db, t, asset, **({k: score for k in SUBSCORES} | {"redesign_potential": redesign})
        )

    alpha = await f.channel(db, projects=[p], title="Alpha", subscriber_count=50_000, country="US")
    beta = await f.channel(db, projects=[p], title="Beta", subscriber_count=5_000)
    gamma = await f.channel(db, projects=[p], title="Gamma")
    for _ in range(3):
        await audited(alpha, 2, 9)
        await audited(beta, 8, 2)
    await _metrics(db, alpha, last_days=2, videos_30d=6, median=40_000, trend=0.2)
    await _metrics(db, beta, last_days=20, videos_30d=1, median=2_000)
    db.add(
        ChannelDiscovery(
            project_id=p.id,
            channel_id=alpha.id,
            search_query_id=query.id,
            method=DiscoveryMethod.KEYWORD_SEARCH,
        )
    )
    for days, subs in ((40, 40_000), (10, 44_000)):
        db.add(
            ChannelStatsSnapshot(
                channel_id=alpha.id, captured_on=date.today() - timedelta(days=days), subscriber_count=subs
            )
        )
    await db.flush()
    return {
        "ws": ws,
        "project": p,
        "alpha": alpha,
        "beta": beta,
        "gamma": gamma,
        "business": business,
        "finance": finance,
    }


@pytest.fixture
async def auth(client, owner, settings):
    await login(client, "owner@example.com")
    return csrf_headers(client, settings)


async def _sync(client, auth, body=None):
    r = await client.post("/api/leads/sync", headers=auth, json=body or {})
    assert r.status_code == 200, r.text
    return r.json()


async def _leads(client, **params):
    r = await client.get("/api/leads", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _by_title(page):
    return {item["channel"]["title"]: item for item in page["items"]}


async def test_sync_creates_advances_and_scores_once(client, db, world, auth):
    first = await _sync(client, auth)
    assert first.pop("system_tags_changed") >= 1  # at least Alpha's weak-thumbnails (Phase 4)
    assert first == {"leads_created": 3, "leads_analyzed": 2, "scores_changed": 3, "scores_unchanged": 0}
    again = await _sync(client, auth, {"project_id": world["project"].id})
    assert again == {
        "leads_created": 0,
        "leads_analyzed": 0,
        "scores_changed": 0,
        "scores_unchanged": 3,
        "system_tags_changed": 0,
    }
    assert await db.scalar(select(func.count()).select_from(LeadScore)) == 3

    leads = {lead.channel_id: lead for lead in await db.scalars(select(Lead))}
    assert leads[world["alpha"].id].status == LeadStatus.ANALYZED
    assert leads[world["gamma"].id].status == LeadStatus.DISCOVERED
    assert all(lead.created_from_project_id == world["project"].id for lead in leads.values())
    history = list(
        await db.scalars(
            select(LeadStatusHistory)
            .where(LeadStatusHistory.lead_id == leads[world["alpha"].id].id)
            .order_by(LeadStatusHistory.id)
        )
    )
    assert [(h.from_status, h.to_status, h.actor_user_id) for h in history] == [
        (None, LeadStatus.DISCOVERED, None),
        (LeadStatus.DISCOVERED, LeadStatus.ANALYZED, None),
    ]
    missing = await client.post("/api/leads/sync", headers=auth, json={"project_id": 999_999})
    assert missing.status_code == 404


async def test_scores_are_explainable_and_ranked(client, db, world, auth):
    await _sync(client, auth)
    page = await _leads(client)
    assert page["total"] == 3 and page["summary"]["leads"] == 3 and page["summary"]["scored"] == 3
    assert page["summary"]["by_status"] == {"analyzed": 2, "discovered": 1}
    assert page["lead_profile"]["version"] == 0 and page["lead_profile"]["is_default"]
    assert [i["channel"]["title"] for i in page["items"]] == ["Alpha", "Beta", "Gamma"]  # by lead score
    alpha = _by_title(page)["Alpha"]
    assert alpha["status"] == "analyzed" and alpha["activity_level"] == "high"
    s = alpha["score"]
    assert Decimal(s["avg_thumbnail_score"]) == Decimal("2.00")
    comps = {c["key"]: c for c in s["components"]}
    assert list(comps) == [
        "thumbnail_opportunity",
        "channel_fit",
        "audience",
        "views",
        "activity",
        "growth",
        "contactability",
        "niche_value",
    ]
    assert Decimal(comps["thumbnail_opportunity"]["max"]) == 25
    assert Decimal(comps["thumbnail_opportunity"]["points"]) == Decimal(s["thumbnail_opportunity"]) > 20
    assert comps["contactability"]["has_data"] is False
    assert comps["channel_fit"]["has_data"] is False  # no ideal lead profile yet
    assert comps["growth"]["inputs"]["subscriber_growth_30d"] == pytest.approx(0.1, abs=1e-3)
    total_points = sum(Decimal(c["points"]) for c in comps.values())
    # default maxima sum to 100 => total = Σ points (the total is rounded once, the points one by one)
    assert abs(Decimal(s["total"]) - total_points) <= Decimal("0.05")
    assert Decimal(s["data_coverage"]) == Decimal("70.00")  # channel fit 20 + niche value 10 missing

    gamma = _by_title(page)["Gamma"]["score"]
    assert Decimal(gamma["total"]) == 0 and Decimal(gamma["data_coverage"]) == 0
    assert gamma["avg_thumbnail_score"] is None

    detail = (await client.get(f"/api/leads/{alpha['id']}")).json()
    assert [h["to_status"] for h in detail["history"]] == ["analyzed", "discovered"]
    assert len(detail["score_history"]) == 1 and detail["ideal_lead_profile"] is None


async def test_filters_and_sorting(client, db, world, auth):
    await _sync(client, auth)
    db.add(
        ChannelAnalysis(
            channel_id=world["beta"].id,
            prompt_template_id=(await f.prompt_template(db, "channel_analysis")).id,
            provider="mock",
            model_key="mock-vision",
            detail="low",
            inputs_digest="d",
            is_current=True,
            video_ids=[1],
            thumbnail_analysis_ids=[1],
            visual_consistency=Decimal(5),
            repeated_template=False,
            outdated_style_score=Decimal(5),
            has_design_system=False,
            visual_opportunity=AILevel.LOW,
            patterns=[],
            recurring_problems=[],
            recurring_problem_codes=[],
            weakest_video_ids=[],
            redesign_candidates=[],
            summary="s",
        )
    )
    await db.flush()

    def titles(page):
        return [i["channel"]["title"] for i in page["items"]]

    assert titles(await _leads(client, max_thumbnail_score=5.5)) == ["Alpha"]
    assert titles(await _leads(client, min_thumbnail_score=3, max_thumbnail_score=9)) == ["Beta"]
    assert titles(await _leads(client, min_lead_score=40)) == ["Alpha"]
    assert titles(
        await _leads(client, min_subscribers=5_000, max_subscribers=100_000, sort="subscribers", order="asc")
    ) == ["Beta", "Alpha"]
    assert titles(await _leads(client, min_avg_views=10_000)) == ["Alpha"]
    assert titles(await _leads(client, activity="high")) == ["Alpha"]
    assert set(titles(await _leads(client, activity="medium"))) == {"Alpha", "Beta"}
    assert titles(await _leads(client, max_days_since_last_upload=14)) == ["Alpha"]
    assert titles(await _leads(client, status="discovered")) == ["Gamma"]
    assert set(titles(await _leads(client, status=["discovered", "analyzed"]))) == {"Alpha", "Beta", "Gamma"}
    assert titles(await _leads(client, visual_opportunity="low")) == ["Beta"]
    assert titles(await _leads(client, niche=world["business"].id)) == ["Alpha"]  # topic Finance inside
    assert titles(await _leads(client, q="gam")) == ["Gamma"]
    assert titles(await _leads(client, channel_id=world["beta"].id)) == ["Beta"]
    # all at once (§9: «все фильтры должны работать одновременно»)
    assert titles(
        await _leads(
            client,
            max_thumbnail_score=6,
            min_lead_score=50,
            min_subscribers=10_000,
            activity="high",
            max_days_since_last_upload=14,
        )
    ) == ["Alpha"]
    assert titles(await _leads(client, sort="thumbnail_score", order="asc")) == ["Alpha", "Beta", "Gamma"]
    assert titles(await _leads(client, sort="priority"))[0] == "Alpha"
    assert titles(await _leads(client, sort="visual_opportunity"))[0] == "Beta"  # the only one, nulls last
    paged = await _leads(client, limit=1, offset=1)
    assert paged["total"] == 3 and titles(paged) == ["Beta"]
    other = await f.project(db, world["ws"].id, "Empty")
    empty = await _leads(client, project_id=other.id)
    assert empty["total"] == 0 and empty["summary"]["leads"] == 0
    assert (await client.get("/api/leads", params={"activity": "max"})).status_code == 422
    assert (await client.get("/api/leads", params={"max_thumbnail_score": 11})).status_code == 422


async def test_weights_rescore_and_keep_history(client, db, world, auth):
    await _sync(client, auth)
    only_audience = {
        "thumbnail_opportunity": 0,
        "channel_fit": 0,
        "audience": 10,
        "views": 0,
        "activity": 0,
        "growth": 0,
        "contactability": 0,
        "niche_value": 0,
    }
    r = await client.put("/api/scoring-profiles/lead", headers=auth, json=only_audience)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == 1 and body["rescored"]["scores_changed"] == 3
    page = await _leads(client)
    alpha = _by_title(page)["Alpha"]["score"]
    # log10(50k / 1k) / 3 = 0.566
    assert Decimal(alpha["total"]) == Decimal("56.63") and alpha["lead_profile_version"] == 1
    assert Decimal(alpha["audience"]) == Decimal("5.66")
    assert Decimal(_by_title(page)["Gamma"]["score"]["total"]) == 0  # subscribers unknown

    prio = await client.put(
        "/api/scoring-profiles/priority", headers=auth, json=only_audience | {"audience": 0, "views": 1}
    )
    assert prio.status_code == 200 and prio.json()["version"] == 1
    assert [i["channel"]["title"] for i in (await _leads(client, sort="priority"))["items"]][:2] == [
        "Alpha",
        "Beta",
    ]

    niche = await client.put(
        "/api/scoring-profiles/lead",
        headers=auth,
        json=only_audience | {"niche_value": 10, "niche_values": {str(world["business"].id): 8}},
    )
    assert niche.status_code == 200 and niche.json()["weights"]["niche_values"] == {
        str(world["business"].id): 8
    }
    comps = {c["key"]: c for c in _by_title(await _leads(client))["Alpha"]["score"]["components"]}
    assert (
        comps["niche_value"]["fraction"] == 0.8
        and comps["niche_value"]["inputs"]["taxonomy_node_id"] == world["business"].id
    )  # Finance inherits its niche's value

    lead_id = _by_title(page)["Alpha"]["id"]
    detail = (await client.get(f"/api/leads/{lead_id}")).json()
    assert len(detail["score_history"]) == 4  # default, audience-only, priority change, niche values
    assert (await client.get("/api/scoring-profiles/lead")).json()["version"] == 2
    bad = await client.put("/api/scoring-profiles/lead", headers=auth, json={k: 0 for k in only_audience})
    assert bad.status_code == 422


async def test_thumbnail_weight_change_rescores_leads(client, db, world, auth):
    await _sync(client, auth)
    before = _by_title(await _leads(client))["Beta"]["score"]
    weights = {k: 0 for k in SUBSCORES} | {"redesign_potential": 1}
    r = await client.put("/api/scoring-profiles/thumbnail", headers=auth, json=weights)
    assert r.status_code == 200, r.text
    after = _by_title(await _leads(client))["Beta"]["score"]
    assert Decimal(after["avg_thumbnail_score"]) == Decimal("2.00") != Decimal(before["avg_thumbnail_score"])
    assert after["thumbnail_profile_version"] == 1


async def test_ideal_lead_profile_drives_channel_fit(client, db, world, auth):
    await _sync(client, auth)
    r = await client.post(
        "/api/ideal-lead-profiles",
        headers=auth,
        json={
            "name": "Business 10k–100k",
            "criteria": {
                "min_subscribers": 10_000,
                "max_subscribers": 100_000,
                "min_activity": "high",
                "max_thumbnail_score": 6,
                "niches": [world["business"].id],
                "contact_types": ["telegram"],
            },
        },
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["is_default"] and created["rescored"]["scores_changed"] == 3
    page = _by_title(await _leads(client))
    fit = {c["key"]: c for c in page["Alpha"]["score"]["components"]}["channel_fit"]
    # Alpha's contacts were never scanned: the contact criterion is unknown, so it does not count as met
    assert fit["fraction"] == pytest.approx(0.8) and "нет данных: контакт: telegram" in fit["reason"]
    # a Telegram contact found by hand satisfies it; the profile's lead is rescored right away
    added = await client.post(
        f"/api/channels/{world['alpha'].id}/contacts",
        headers=auth,
        json={"type": "telegram", "value": "@alpha_news"},
    )
    assert added.status_code == 201, added.text
    page = _by_title(await _leads(client))
    fit = {c["key"]: c for c in page["Alpha"]["score"]["components"]}["channel_fit"]
    assert fit["fraction"] == 1.0 and "совпало 5 из 5" in fit["reason"]
    beta_fit = {c["key"]: c for c in page["Beta"]["score"]["components"]}["channel_fit"]
    assert beta_fit["fraction"] == 0.0 and page["Beta"]["score"]["ideal_lead_profile_id"] == created["id"]

    dup = await client.post("/api/ideal-lead-profiles", headers=auth, json={"name": "Business 10k–100k"})
    assert dup.status_code == 409
    second = (
        await client.post(
            "/api/ideal-lead-profiles",
            headers=auth,
            json={"name": "Small", "criteria": {"max_subscribers": 10_000}},
        )
    ).json()
    listed = (await client.get("/api/ideal-lead-profiles")).json()
    assert [(p["name"], p["is_default"]) for p in listed] == [("Small", True), ("Business 10k–100k", False)]
    assert _by_title(await _leads(client))["Beta"]["score"]["channel_fit"] == "20.00"

    patched = await client.patch(
        f"/api/ideal-lead-profiles/{created['id']}",
        headers=auth,
        json={"is_default": True, "criteria": {"countries": ["us"]}},
    )
    assert patched.status_code == 200 and patched.json()["criteria"]["countries"] == ["US"]
    assert {p["name"]: p["is_default"] for p in (await client.get("/api/ideal-lead-profiles")).json()} == {
        "Small": False,
        "Business 10k–100k": True,
    }
    assert (await client.delete(f"/api/ideal-lead-profiles/{created['id']}", headers=auth)).status_code == 200
    alpha = _by_title(await _leads(client))["Alpha"]["score"]
    assert alpha["ideal_lead_profile_id"] is None and alpha["channel_fit"] == "0.00"
    assert (await client.delete(f"/api/ideal-lead-profiles/{second['id']}", headers=auth)).status_code == 200
    bad = await client.post(
        "/api/ideal-lead-profiles",
        headers=auth,
        json={"name": "x", "criteria": {"min_subscribers": 5, "max_subscribers": 1}},
    )
    assert bad.status_code == 422


async def test_roles_and_workspace_isolation(client, db, world, auth, other_owner, settings):
    await sync_leads(db, world["ws"].id)
    [lead_id] = [
        lead.id for lead in await db.scalars(select(Lead).where(Lead.channel_id == world["alpha"].id))
    ]
    viewer = await add_member(db, world["ws"], "viewer@example.com", WorkspaceRole.VIEWER)
    operator = await add_member(db, world["ws"], "op@example.com", WorkspaceRole.OPERATOR)
    assert viewer and operator

    await client.post("/api/auth/logout", headers=auth)
    await login(client, "viewer@example.com")
    h = csrf_headers(client, settings)
    assert (await client.get("/api/leads")).json()["total"] == 3
    assert (await client.post("/api/leads/sync", headers=h, json={})).status_code == 403
    assert (await client.post("/api/ideal-lead-profiles", headers=h, json={"name": "v"})).status_code == 403

    await client.post("/api/auth/logout", headers=h)
    await login(client, "op@example.com")
    h = csrf_headers(client, settings)
    assert (await client.post("/api/leads/sync", headers=h, json={})).status_code == 200
    assert (await client.post("/api/ideal-lead-profiles", headers=h, json={"name": "op"})).status_code == 201
    assert (
        await client.put("/api/scoring-profiles/lead", headers=h, json={})
    ).status_code == 403  # admin only
    assert (await client.post("/api/leads/sync", json={})).status_code == 403  # no CSRF

    await client.post("/api/auth/logout", headers=h)
    await login(client, "other@example.com")
    assert (await client.get(f"/api/leads/{lead_id}")).status_code == 404
    assert (await client.get("/api/leads")).json()["total"] == 0
    assert (await client.get("/api/ideal-lead-profiles")).json() == []
