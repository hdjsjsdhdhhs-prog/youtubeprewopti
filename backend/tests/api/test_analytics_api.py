"""Phase 9: analytics API — dashboard tiles and funnel, AI spend and cost per lead, niches, thumbnail score
ranges, campaigns, A/B by style / attachment / channel, AI vs human; periods, project scope, workspace
isolation and authentication. Data is seeded directly: the analytics only read other domains' tables."""

from __future__ import annotations

import hashlib
import itertools
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import factories as f
import pytest
from conftest import login

from app.core.state_machine import LeadStatus as L
from app.domains.ai.models import AICall, AICallStatus
from app.domains.campaigns.models import Campaign, CampaignLead, CampaignStatus
from app.domains.feedback.models import FeedbackEvent, FeedbackKind
from app.domains.generation.models import GeneratedThumbnail, GenerationRequest, VariantStatus
from app.domains.leads.models import Lead, LeadScore, LeadStatusHistory
from app.domains.leads.scoring import COMPONENTS
from app.domains.offers.models import Offer, OfferStatus, OfferStyle, OfferVariant, OutreachChannel
from app.domains.outreach.models import Message, MessageEvent, MessageEventType, MessageStatus
from app.domains.projects.models import (
    ChannelDiscovery,
    DiscoveryMethod,
    SearchQuery,
    TaxonomyLevel,
    TaxonomyNode,
)

_n = itertools.count(1)
NOW = datetime.now(UTC)


async def _lead(db, ws_id, channel, status, history=(), *, total=None, avg=None, created_at=None) -> Lead:
    lead = Lead(workspace_id=ws_id, channel_id=channel.id, status=status)
    if created_at is not None:
        lead.created_at = created_at
    db.add(lead)
    await db.flush()
    prev = None
    for st in history:
        db.add(LeadStatusHistory(lead_id=lead.id, from_status=prev, to_status=st))
        prev = st
    if total is not None:
        zero = Decimal(0)
        db.add(
            LeadScore(
                lead_id=lead.id,
                is_current=True,
                lead_profile_version=0,
                priority_profile_version=0,
                thumbnail_profile_version=0,
                total=Decimal(total),
                priority_score=Decimal(total),
                data_coverage=Decimal(100),
                avg_thumbnail_score=Decimal(avg) if avg is not None else None,
                components=[],
                **{c: zero for c in COMPONENTS},
            )
        )
    await db.flush()
    return lead


async def _offer(
    db, ws_id, lead, style, status=OfferStatus.SENT, project_id=None
) -> tuple[Offer, OfferVariant]:
    offer = Offer(
        workspace_id=ws_id,
        lead_id=lead.id,
        project_id=project_id,
        status=status,
        variant_count=1,
        language="ru",
        inputs={},
    )
    db.add(offer)
    await db.flush()
    variant = OfferVariant(
        offer_id=offer.id,
        round=1,
        label="A",
        style=style,
        is_selected=True,
        body="…",
        cost_usd=Decimal("0.01"),
    )
    db.add(variant)
    await db.flush()
    return offer, variant


async def _message(
    db,
    ws_id,
    lead,
    offer,
    variant,
    events=(),
    *,
    attachment=None,
    campaign_id=None,
    channel=OutreachChannel.EMAIL,
    status=MessageStatus.SENT,
) -> Message:
    n = next(_n)
    m = Message(
        workspace_id=ws_id,
        lead_id=lead.id,
        offer_id=offer.id,
        offer_variant_id=variant.id,
        channel=channel,
        contact_type="email",
        to_address=f"a{n}@example.com",
        body="…",
        content_hash="0" * 64,
        idempotency_key=hashlib.sha256(f"k{n}".encode()).hexdigest(),
        status=status,
        scheduled_at=NOW,
        sent_at=NOW if status == MessageStatus.SENT else None,
        attachment_asset_id=attachment,
        campaign_id=campaign_id,
    )
    db.add(m)
    await db.flush()
    for t in events:
        db.add(MessageEvent(message_id=m.id, type=t))
    await db.flush()
    return m


def _call(
    ws_id, task, *, actual=None, estimated=None, status=AICallStatus.OK, project_id=None, **kw
) -> AICall:
    return AICall(
        workspace_id=ws_id,
        project_id=project_id,
        task=task,
        provider="mock",
        model_key=kw.pop("model", "mock-text"),
        api_model_id="mock-1",
        status=status,
        actual_cost_usd=actual,
        estimated_cost_usd=estimated,
        input_tokens=100,
        output_tokens=50,
        duration_ms=200,
        **kw,
    )


@pytest.fixture
async def world(db, owner):
    ws = owner[1]
    p = await f.project(db, ws.id, "Analytics")
    gaming = TaxonomyNode(level=TaxonomyLevel.NICHE, name="Gaming", slug=f"gaming-{next(_n)}")
    db.add(gaming)
    await db.flush()
    minecraft = TaxonomyNode(
        parent_id=gaming.id, level=TaxonomyLevel.TOPIC, name="Minecraft", slug="minecraft"
    )
    db.add(minecraft)
    await db.flush()
    q = SearchQuery(
        project_id=p.id, text="minecraft", text_normalized="minecraft", taxonomy_node_id=minecraft.id
    )
    db.add(q)
    await db.flush()
    ch = [await f.channel(db, projects=[p], title=t) for t in ("One", "Two", "Three", "Four")]
    for c in ch[:2]:
        db.add(
            ChannelDiscovery(
                project_id=p.id, channel_id=c.id, search_query_id=q.id, method=DiscoveryMethod.DEMO
            )
        )
    await db.flush()

    l1 = await _lead(
        db,
        ws.id,
        ch[0],
        L.SENT,
        (L.ANALYZED, L.SELECTED, L.OFFER_DRAFT, L.PENDING_APPROVAL, L.APPROVED, L.SENT),
        total=70,
        avg="4.5",
    )
    l2 = await _lead(db, ws.id, ch[1], L.SELECTED, (L.ANALYZED, L.SELECTED), total=40, avg="6.2")
    l3 = await _lead(db, ws.id, ch[2], L.DISCOVERED)
    l4 = await _lead(db, ws.id, ch[3], L.REJECTED, (L.ANALYZED, L.REJECTED), total=20, avg="8")

    camp = Campaign(
        workspace_id=ws.id,
        project_id=p.id,
        name="Осень",
        status=CampaignStatus.RUNNING,
        styles=["short"],
        channels=["email"],
        send_days=[1, 2, 3, 4, 5],
    )
    db.add(camp)
    await db.flush()
    o1, v1 = await _offer(db, ws.id, l1, OfferStyle.SHORT, project_id=p.id)
    db.add(CampaignLead(campaign_id=camp.id, lead_id=l1.id, style=OfferStyle.SHORT, offer_id=o1.id))
    await _message(
        db,
        ws.id,
        l1,
        o1,
        v1,
        (MessageEventType.DELIVERED, MessageEventType.POSITIVE_REPLY),
        campaign_id=camp.id,
    )
    o2, _ = await _offer(
        db, ws.id, l2, OfferStyle.PROFESSIONAL, OfferStatus.PENDING_APPROVAL, project_id=p.id
    )

    db.add_all(
        [
            _call(ws.id, "thumbnail_analysis", actual=Decimal("0.10"), project_id=p.id),
            _call(
                ws.id,
                "thumbnail_generation",
                estimated=Decimal("0.20"),
                model="vc-gpt-image-2",
                image_outputs=1,
            ),
            _call(ws.id, "offer_generation", status=AICallStatus.ERROR),  # no price known
        ]
    )
    req = GenerationRequest(
        workspace_id=ws.id,
        lead_id=l1.id,
        project_id=p.id,
        variant_count=2,
        model_key="mock-image",
        brief={},
    )
    db.add(req)
    await db.flush()
    db.add_all(
        [
            GeneratedThumbnail(
                generation_request_id=req.id,
                label="A",
                strategy="contrast",
                prompt="…",
                model_key="mock-image",
                status=VariantStatus.FINAL,
            ),
            GeneratedThumbnail(
                generation_request_id=req.id,
                label="B",
                strategy="face",
                prompt="…",
                model_key="mock-image",
                status=VariantStatus.REJECTED,
            ),
        ]
    )
    db.add_all(
        [
            FeedbackEvent(
                workspace_id=ws.id,
                kind=FeedbackKind.OFFER_DECISION,
                target_type="offer",
                target_id=o1.id,
                field="decision",
                payload={"decision": "approve", "style": "short", "personalization_score": "0.80"},
            ),
            FeedbackEvent(
                workspace_id=ws.id,
                kind=FeedbackKind.OFFER_EDIT,
                target_type="offer",
                target_id=o2.id,
                field="content",
                payload={"style": "professional", "changes": {}},
            ),
            FeedbackEvent(
                workspace_id=ws.id,
                kind=FeedbackKind.LEAD_STATUS,
                target_type="lead",
                target_id=l4.id,
                field="status",
                payload={"to": "rejected", "lead_score": "75"},
            ),
            FeedbackEvent(
                workspace_id=ws.id,
                kind=FeedbackKind.LEAD_STATUS,
                target_type="lead",
                target_id=l2.id,
                field="status",
                payload={"to": "selected", "lead_score": "40"},
            ),
        ]
    )
    await db.flush()
    return {"ws": ws, "project": p, "leads": [l1, l2, l3, l4], "campaign": camp, "gaming": gaming}


@pytest.fixture
async def auth(client, owner):
    await login(client, "owner@example.com")


async def _get(client, path, **params):
    r = await client.get(f"/api/analytics/{path}", params=params)
    assert r.status_code == 200, r.text
    return r.json()


async def test_requires_login(client):
    assert (await client.get("/api/analytics/overview")).status_code == 401


async def test_empty_workspace(client, auth):
    body = await _get(client, "overview")
    assert body["tiles"]["total_channels"] == 0
    assert body["tiles"]["conversion"] is None
    assert [s["leads"] for s in body["funnel"]] == [0] * 8
    assert body["costs"]["total_usd"] == "0" or Decimal(body["costs"]["total_usd"]) == 0
    assert body["best_niche"] is None and body["best_offer_variant"] is None
    assert body["min_sample"] == 30


async def test_overview_tiles_and_funnel(client, auth, world):
    body = await _get(client, "overview")
    t = body["tiles"]
    assert (t["total_channels"], t["new_channels"], t["analyzed"], t["selected"]) == (4, 4, 3, 2)
    assert (t["generated_samples"], t["offers_pending_approval"]) == (1, 1)
    assert (t["sent"], t["replies"], t["positive_replies"]) == (1, 1, 1)
    assert Decimal(t["conversion"]) == 1 and Decimal(t["reply_rate"]) == 1
    funnel = {s["key"]: s for s in body["funnel"]}
    assert [s["key"] for s in body["funnel"]] == [
        "found",
        "analyzed",
        "selected",
        "offer",
        "approved",
        "sent",
        "replied",
        "positive",
    ]
    assert [s["leads"] for s in body["funnel"]] == [4, 3, 2, 1, 1, 1, 1, 1]
    assert Decimal(funnel["selected"]["rate_from_previous"]) == Decimal("0.6667")
    assert Decimal(funnel["sent"]["rate_from_start"]) == Decimal("0.25")
    assert funnel["found"]["rate_from_previous"] is None
    c = body["costs"]
    assert Decimal(c["total_usd"]) == Decimal("0.30")
    assert (Decimal(c["analysis_usd"]), Decimal(c["generation_usd"])) == (Decimal("0.10"), Decimal("0.20"))
    assert Decimal(c["per_lead_usd"]) == Decimal("0.075")
    assert Decimal(c["per_positive_usd"]) == Decimal("0.30")
    assert c["unpriced_calls"] == 1 and c["calls"] == 3
    assert any("нижняя граница" in n for n in body["notes"])
    # one sent message is far from the minimum sample: nothing is «best»
    assert body["best_niche"] is None and body["best_campaign"] is None and body["best_score_range"] is None


async def test_period_and_project_scope(client, auth, world, db, other_owner):
    future = (date.today() + timedelta(days=1)).isoformat()
    body = await _get(client, "overview", date_from=future)
    assert body["tiles"]["new_channels"] == 0 and body["tiles"]["total_channels"] == 4
    assert Decimal(body["costs"]["total_usd"]) == 0
    old = world["leads"][2]
    old.created_at = NOW - timedelta(days=60)
    await db.flush()
    recent = await _get(client, "overview", date_from=(date.today() - timedelta(days=7)).isoformat())
    assert recent["tiles"]["new_channels"] == 3
    scoped = await _get(client, "costs", project_id=world["project"].id)
    assert Decimal(scoped["summary"]["total_usd"]) == Decimal("0.10")  # only the call of the project

    r = await client.get(
        "/api/analytics/overview", params={"date_from": "2026-02-01", "date_to": "2026-01-01"}
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_period"
    assert (await client.get("/api/analytics/overview", params={"project_id": 999999})).status_code == 404
    foreign = await f.project(db, other_owner[1].id, "Foreign")
    assert (await client.get("/api/analytics/niches", params={"project_id": foreign.id})).status_code == 404


async def test_other_workspace_is_invisible(client, auth, world, db, other_owner):
    ws2 = other_owner[1]
    c = await f.channel(db, projects=[await f.project(db, ws2.id)])
    await _lead(db, ws2.id, c, L.ANALYZED, (L.ANALYZED,), total=90, avg="2")
    db.add(_call(ws2.id, "thumbnail_analysis", actual=Decimal("5")))
    await db.flush()
    body = await _get(client, "overview")
    assert body["tiles"]["total_channels"] == 4
    assert Decimal(body["costs"]["total_usd"]) == Decimal("0.30")


async def test_costs_by_task_and_model(client, auth, world):
    body = await _get(client, "costs")
    tasks = {r["key"]: r for r in body["by_task"]}
    assert set(tasks) == {"thumbnail_analysis", "thumbnail_generation", "offer_generation"}
    assert tasks["offer_generation"]["failed_calls"] == 1 and tasks["offer_generation"]["unpriced_calls"] == 1
    assert (
        tasks["thumbnail_analysis"]["input_tokens"] == 100
        and tasks["thumbnail_analysis"]["avg_duration_ms"] == 200
    )
    models = {r["key"]: r for r in body["by_model"]}
    assert models["vc-gpt-image-2"]["image_outputs"] == 1 and models["vc-gpt-image-2"]["provider"] == "mock"
    assert body["by_model"][0]["key"] == "vc-gpt-image-2"  # most expensive first


async def test_niches(client, auth, world):
    body = await _get(client, "niches")
    rows = {r["name"]: r for r in body["items"]}
    gaming = rows["Gaming"]  # the topic query counts for its niche
    assert gaming["niche_id"] == world["gaming"].id
    assert (gaming["leads"], gaming["analyzed"], gaming["selected"]) == (2, 2, 2)
    assert Decimal(gaming["avg_thumbnail_score"]) == Decimal("5.35")
    assert gaming["weak_thumbnails"] == 1  # 4.5 < 5
    assert gaming["high_opportunity"] == 1  # Lead Score 70 ≥ 60
    assert gaming["outcomes"]["sent"] == 1 and gaming["outcomes"]["positive_reply"] == 1
    assert gaming["outcomes"]["small_sample"] is True
    other = rows["Без ниши"]
    assert other["niche_id"] is None and other["leads"] == 2
    assert body["items"][-1]["niche_id"] is None  # «no niche» last
    assert body["best"] is None and "30" in body["note"]


async def test_score_ranges(client, auth, world):
    body = await _get(client, "score-ranges")
    rows = {r["key"]: r for r in body["items"]}
    assert [r["key"] for r in body["items"]][:7] == ["1-3", "3-4", "4-5", "5-6", "6-7", "7-8", "8-10"]
    assert rows["4-5"]["leads"] == 1 and rows["4-5"]["outcomes"]["replied"] == 1
    assert rows["6-7"]["selected"] == 1 and Decimal(rows["6-7"]["selection_rate"]) == 1
    assert rows["8-10"]["leads"] == 1 and rows["8-10"]["selected"] == 0
    assert rows["none"]["leads"] == 1 and rows["none"]["low"] is None
    assert rows["1-3"]["leads"] == 0 and rows["1-3"]["selection_rate"] is None


async def test_campaigns(client, auth, world):
    body = await _get(client, "campaigns")
    (row,) = body["items"]
    assert (row["id"], row["name"], row["status"]) == (world["campaign"].id, "Осень", "running")
    assert (row["leads"], row["approved"]) == (1, 1)
    assert Decimal(row["cost_usd"]) == Decimal("0.01") and Decimal(row["cost_per_reply_usd"]) == Decimal(
        "0.01"
    )
    o = row["outcomes"]
    assert (o["sent"], o["delivered"], o["replied"], o["positive_reply"], o["no_reply"]) == (1, 1, 1, 1, 0)
    assert body["best"] is None


async def test_ab_by_style_attachment_and_channel(client, auth, world, db):
    body = await _get(client, "ab")
    assert [g["key"] for g in body["groups"]] == ["professional", "short", "free_sample"]
    groups = {g["key"]: g for g in body["groups"]}
    assert groups["short"]["outcomes"]["sent"] == 1 and groups["short"]["label"].startswith("B")
    assert groups["professional"]["outcomes"]["sent"] == 0  # pending offer: nothing sent
    assert (body["verdict"], body["leader"], body["p_value"]) == ("insufficient_data", None, None)
    assert "пиксель" in body["note"]

    att = await _get(client, "ab", dimension="attachment")
    assert {g["key"]: g["outcomes"]["sent"] for g in att["groups"]} == {"with": 0, "without": 1}
    ch = await _get(client, "ab", dimension="channel")
    assert [g["key"] for g in ch["groups"]] == ["email"]
    only = await _get(client, "ab", campaign_id=world["campaign"].id)
    assert sum(g["outcomes"]["sent"] for g in only["groups"]) == 1
    assert (await client.get("/api/analytics/ab", params={"campaign_id": 999999})).status_code == 404
    assert (await client.get("/api/analytics/ab", params={"dimension": "subject"})).status_code == 422


async def test_ab_names_a_leader_only_beyond_chance(client, auth, world, db):
    """35 «short» messages with 20 replies vs 35 «professional» with 2: a leader. Equal rates: none."""
    ws_id = world["ws"].id
    p = world["project"]

    async def sends(style, n, replies):
        for i in range(n):
            c = await f.channel(db, projects=[p])
            lead = await _lead(db, ws_id, c, L.SENT, (L.SENT,))
            offer, variant = await _offer(db, ws_id, lead, style)
            await _message(
                db, ws_id, lead, offer, variant, (MessageEventType.REPLIED,) if i < replies else ()
            )

    await sends(OfferStyle.SHORT, 34, 19)  # + the world's one positive reply = 35 sent / 20 replies
    await sends(OfferStyle.PROFESSIONAL, 35, 2)
    body = await _get(client, "ab")
    assert (body["verdict"], body["leader"]) == ("leader", "short")
    assert Decimal(body["p_value"]) < Decimal("0.05")
    overview = await _get(client, "overview")
    assert overview["best_offer_variant"]["key"] == "short"
    assert overview["best_offer_variant"]["sent"] == 35

    await sends(OfferStyle.FREE_SAMPLE, 35, 19)  # close to «short»: the top two are within chance
    body = await _get(client, "ab")
    assert body["verdict"] == "no_difference" and body["leader"] == "short"


async def test_ai_feedback(client, auth, world):
    body = await _get(client, "ai-feedback")
    strategies = {r["key"]: r for r in body["generation_by_strategy"]}
    assert (strategies["contrast"]["approved"], strategies["contrast"]["final"]) == (1, 1)
    assert strategies["face"]["rejected"] == 1 and Decimal(strategies["face"]["approval_rate"]) == 0
    (model,) = body["generation_by_model"]
    assert (model["key"], model["generated"], Decimal(model["approval_rate"])) == (
        "mock-image",
        2,
        Decimal("0.5"),
    )
    offers = {r["style"]: r for r in body["offers_by_style"]}
    assert offers["short"]["approve"] == 1 and Decimal(
        offers["short"]["avg_personalization_approved"]
    ) == Decimal("0.8")
    assert offers["professional"]["edits"] == 1 and offers["professional"]["approve"] == 0
    bands = {b["key"]: b for b in body["lead_decisions_by_score"]}
    assert bands["60-80"]["rejected"] == 1  # the code rated it high, the human rejected it
    assert bands["40-60"]["selected"] == 1 and Decimal(bands["40-60"]["selection_rate"]) == 1
    assert body["scores"]["events"] == 4
