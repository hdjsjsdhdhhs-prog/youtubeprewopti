"""Phase 6 API: scanning channel texts, manual contacts, statuses and opt-out, the suppression list,
Contactability weights, lead filters, the project scan job, roles and workspace isolation."""

from __future__ import annotations

from decimal import Decimal

import factories as f
import pytest
from conftest import add_member, csrf_headers, login
from sqlalchemy import func, select

from app.domains.contacts.models import (
    ChannelContactScan,
    Contact,
    ContactState,
    ContactStatus,
    SuppressionEntry,
)
from app.domains.identity.models import AuditLog, WorkspaceRole
from app.domains.jobs.models import JobRun
from app.domains.projects.models import ProjectChannel

ALPHA_DESCRIPTION = """Реклама: Info@Alpha.IO
Telegram: @alpha_tg
Сайт https://www.alpha.io/about
"""
ALPHA_VIDEO_DESCRIPTION = "Мы во ВКонтакте: vk.com/alpha_club"


@pytest.fixture
async def world(db, owner):
    """Alpha: e-mail + Telegram + website (channel texts) + VK (a video description) = 90 points;
    Beta: texts without any contact; Gamma: never scanned; Foreign: a channel of another workspace."""
    ws = owner[1]
    p = await f.project(db, ws.id, "Contacts")
    alpha = await f.channel(db, projects=[p], title="Alpha", description=ALPHA_DESCRIPTION)
    beta = await f.channel(db, projects=[p], title="Beta", description="Просто канал про котиков")
    gamma = await f.channel(db, projects=[p], title="Gamma", description="mail: gamma@gamma.io")
    await f.video(db, alpha, description=ALPHA_VIDEO_DESCRIPTION)
    await f.video(db, beta, description="без ссылок")
    foreign = await f.channel(db, title="Foreign", description="foreign@foreign.io")
    return {"ws": ws, "project": p, "alpha": alpha, "beta": beta, "gamma": gamma, "foreign": foreign}


@pytest.fixture
async def auth(client, owner, settings):
    await login(client, "owner@example.com")
    return csrf_headers(client, settings)


async def _sync(client, auth):
    r = await client.post("/api/leads/sync", headers=auth, json={})
    assert r.status_code == 200, r.text


async def _scan(client, auth, channel):
    r = await client.post(f"/api/channels/{channel.id}/contacts/scan", headers=auth)
    assert r.status_code == 200, r.text
    return r.json()


async def _contacts(client, channel) -> dict:
    r = await client.get(f"/api/channels/{channel.id}/contacts")
    assert r.status_code == 200, r.text
    return r.json()


def _item(contacts: dict, type_: str) -> dict:
    return next(i for i in contacts["items"] if i["type"] == type_)


async def _lead(client, channel) -> dict:
    r = await client.get("/api/leads", params={"channel_id": channel.id})
    assert r.status_code == 200, r.text
    return r.json()["items"][0]


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------
async def test_scan_finds_public_contacts_and_rescores_the_lead(client, db, world, auth):
    await _sync(client, auth)
    alpha = world["alpha"]
    before = await _contacts(client, alpha)
    assert before["status"] == "not_scanned" and before["scan"] is None
    assert before["contactability_score"] is None and before["items"] == []
    lead = await _lead(client, alpha)
    assert lead["score"]["contactability_score"] is None

    res = await _scan(client, auth, alpha)
    contacts = res["contacts"]
    assert contacts["status"] == "found"
    assert set(contacts["usable_types"]) == {"email", "telegram", "website", "vk"}
    assert Decimal(contacts["contactability_score"]) == Decimal("90")  # 30 + 30 + 20 + 10
    assert contacts["scan"]["texts_scanned"] == 2 and contacts["scan"]["contacts_found"] == 4
    assert res["rescored"]["scores_changed"] == 1

    email = _item(contacts, "email")
    assert email["value_normalized"] == "info@alpha.io" and email["source"] == "youtube_description"
    assert email["status"] == "public" and email["usable"] and not email["is_manual"]
    vk = _item(contacts, "vk")
    assert vk["source"] == "video_description" and vk["source_video_id"] is not None

    lead = await _lead(client, alpha)
    assert Decimal(lead["score"]["contactability_score"]) == Decimal("90")
    assert lead["score"]["contactability_profile_version"] == 0
    comp = next(c for c in lead["score"]["components"] if c["key"] == "contactability")
    assert comp["has_data"] and "90/100" in comp["reason"]
    # default weight 0: contacts are shown, but the Lead Score itself is not changed unasked
    assert comp["points"] == "0.00"

    # scanning again changes nothing: one row per contact
    again = await _scan(client, auth, alpha)
    assert again["rescored"]["scores_changed"] == 0
    assert await db.scalar(select(func.count()).select_from(Contact)) == 4
    assert await db.scalar(select(func.count()).select_from(ChannelContactScan)) == 1


async def test_scan_without_contacts_is_not_found_not_zero_data(client, db, world, auth):
    await _sync(client, auth)
    res = await _scan(client, auth, world["beta"])
    contacts = res["contacts"]
    assert contacts["status"] == "not_found" and contacts["items"] == []
    assert Decimal(contacts["contactability_score"]) == 0 and contacts["scan"]["contacts_found"] == 0
    lead = await _lead(client, world["beta"])
    assert Decimal(lead["score"]["contactability_score"]) == 0
    comp = next(c for c in lead["score"]["components"] if c["key"] == "contactability")
    assert comp["has_data"] and "not_found" in comp["reason"]
    # the channel that was never scanned stays «no data»
    assert (await _lead(client, world["gamma"]))["score"]["contactability_score"] is None


async def test_contacts_that_disappear_from_the_texts_are_kept_but_stop_counting(client, db, world, auth):
    await _sync(client, auth)
    alpha = world["alpha"]
    await _scan(client, auth, alpha)
    alpha.description = "Telegram: @alpha_tg\nСайт https://www.alpha.io/about"  # the e-mail is gone
    await db.flush()
    contacts = (await _scan(client, auth, alpha))["contacts"]
    email = _item(contacts, "email")
    assert email["present"] is False and email["usable"] is False
    assert set(contacts["usable_types"]) == {"telegram", "website", "vk"}
    assert Decimal(contacts["contactability_score"]) == Decimal("60")
    assert [i["type"] for i in contacts["items"]][-1] == "email"  # not usable => after the usable ones


async def test_unknown_and_foreign_channels_are_404(client, db, world, auth):
    for channel_id in (999_999, world["foreign"].id):
        assert (await client.get(f"/api/channels/{channel_id}/contacts")).status_code == 404
        assert (
            await client.post(f"/api/channels/{channel_id}/contacts/scan", headers=auth)
        ).status_code == 404
        r = await client.post(
            f"/api/channels/{channel_id}/contacts", headers=auth, json={"type": "email", "value": "a@b.io"}
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Manual contacts
# ---------------------------------------------------------------------------
async def test_manual_contacts_validate_dedupe_and_delete(client, db, world, auth):
    await _sync(client, auth)
    gamma = world["gamma"]
    url = f"/api/channels/{gamma.id}/contacts"
    bad = await client.post(url, headers=auth, json={"type": "email", "value": "not-an-email"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_contact"

    ok = await client.post(url, headers=auth, json={"type": "telegram", "value": "t.me/Gamma_Chat"})
    assert ok.status_code == 201, ok.text
    body = ok.json()
    assert body["contacts"]["status"] == "found"  # a manual contact is data even without a scan
    manual = _item(body["contacts"], "telegram")
    assert manual["value_normalized"] == "@gamma_chat" and manual["is_manual"] and manual["usable"]
    assert manual["source"] == "manual" and manual["status"] == "unverified"
    assert Decimal(body["contacts"]["contactability_score"]) == Decimal("30")
    assert body["rescored"]["scores_changed"] == 1

    dup = await client.post(url, headers=auth, json={"type": "telegram", "value": "@GAMMA_CHAT"})
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "contact_exists"

    # a public contact cannot be deleted, only marked invalid
    await _scan(client, auth, gamma)
    public = _item(await _contacts(client, gamma), "email")
    r = await client.delete(f"/api/contacts/{public['id']}", headers=auth)
    assert r.status_code == 409 and r.json()["error"]["code"] == "contact_not_manual"

    r = await client.delete(f"/api/contacts/{manual['id']}", headers=auth)
    assert r.status_code == 200 and {i["type"] for i in r.json()["contacts"]["items"]} == {"email"}
    assert (await client.delete(f"/api/contacts/{manual['id']}", headers=auth)).status_code == 404


# ---------------------------------------------------------------------------
# Statuses, opt-out, suppression list
# ---------------------------------------------------------------------------
async def test_invalid_status_removes_a_contact_from_contactability_and_back(client, db, world, auth):
    await _sync(client, auth)
    alpha = world["alpha"]
    contacts = (await _scan(client, auth, alpha))["contacts"]
    telegram = _item(contacts, "telegram")

    r = await client.patch(
        f"/api/contacts/{telegram['id']}", headers=auth, json={"status": "invalid", "note": "dead"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert _item(body["contacts"], "telegram")["usable"] is False
    assert Decimal(body["contacts"]["contactability_score"]) == Decimal("60")
    assert body["rescored"]["scores_changed"] == 1
    # the verdict is the workspace's own row; the public contact itself is untouched
    assert await db.scalar(select(Contact.status).where(Contact.id == telegram["id"])) == ContactStatus.PUBLIC
    assert await db.scalar(select(func.count()).select_from(ContactState)) == 1

    r = await client.patch(f"/api/contacts/{telegram['id']}", headers=auth, json={"status": "verified"})
    assert r.status_code == 200
    restored = r.json()["contacts"]
    assert _item(restored, "telegram")["status"] == "verified" and _item(restored, "telegram")["verified_at"]
    assert Decimal(restored["contactability_score"]) == Decimal("90")
    assert await db.scalar(select(func.count()).select_from(ContactState)) == 1  # updated, not duplicated


async def test_opt_out_is_final_suppresses_the_value_and_stays_in_the_workspace(
    client, db, world, auth, other_owner, settings
):
    await _sync(client, auth)
    alpha = world["alpha"]
    # another workspace follows the same public channel
    other_project = await f.project(db, other_owner[1].id, "Other")
    db.add(ProjectChannel(project_id=other_project.id, channel_id=alpha.id))
    await db.flush()

    contacts = (await _scan(client, auth, alpha))["contacts"]
    telegram = _item(contacts, "telegram")
    r = await client.patch(
        f"/api/contacts/{telegram['id']}", headers=auth, json={"status": "opted_out", "note": "asked"}
    )
    assert r.status_code == 200, r.text
    after = _item(r.json()["contacts"], "telegram")
    assert after["status"] == "opted_out" and after["suppressed"] and not after["usable"]

    suppression = (await client.get("/api/suppression-list")).json()
    assert suppression["total"] == 1
    entry = suppression["items"][0]
    assert (entry["type"], entry["value_normalized"], entry["reason"]) == ("telegram", "@alpha_tg", "opt_out")

    # final: no way back through the contact itself
    r = await client.patch(f"/api/contacts/{telegram['id']}", headers=auth, json={"status": "verified"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "opted_out_final"

    # the other workspace neither sees our verdict nor our suppression list
    await login(client, "other@example.com")
    seen = _item(await _contacts(client, alpha), "telegram")
    assert seen["status"] == "public" and seen["usable"] and not seen["suppressed"]
    assert (await client.get("/api/suppression-list")).json()["total"] == 0

    # undoing it needs an admin: an operator is refused, the owner can
    await add_member(db, world["ws"], "operator@example.com", WorkspaceRole.OPERATOR)
    await login(client, "operator@example.com")
    op_auth = csrf_headers(client, settings)
    assert (await client.delete(f"/api/suppression-list/{entry['id']}", headers=op_auth)).status_code == 403

    await login(client, "owner@example.com")
    auth = csrf_headers(client, settings)
    r = await client.delete(f"/api/suppression-list/{entry['id']}", headers=auth)
    assert r.status_code == 204
    back = _item(await _contacts(client, alpha), "telegram")
    assert back["status"] == "unverified" and back["usable"] and not back["suppressed"]
    assert await db.scalar(select(func.count()).select_from(SuppressionEntry)) == 0
    actions = set(await db.scalars(select(AuditLog.action).where(AuditLog.workspace_id == world["ws"].id)))
    assert {"contact.status_changed", "suppression.removed"} <= actions


async def test_suppression_list_is_idempotent_normalised_and_applies_to_every_channel(
    client, db, world, auth
):
    await _sync(client, auth)
    for ch in (world["alpha"], world["gamma"]):
        await _scan(client, auth, ch)
    r = await client.post(
        "/api/suppression-list", headers=auth, json={"type": "email", "value": " INFO@Alpha.io "}
    )
    assert r.status_code == 201, r.text
    entry = r.json()
    assert entry["value_normalized"] == "info@alpha.io" and entry["reason"] == "manual"
    again = await client.post(
        "/api/suppression-list",
        headers=auth,
        json={"type": "email", "value": "info@alpha.io", "reason": "complaint"},
    )
    assert again.status_code == 201 and again.json()["id"] == entry["id"]  # idempotent, first reason kept
    assert (await client.get("/api/suppression-list")).json()["total"] == 1

    email = _item(await _contacts(client, world["alpha"]), "email")
    assert email["suppressed"] and not email["usable"]
    assert _item(await _contacts(client, world["gamma"]), "email")["usable"]  # a different address
    bad = await client.post("/api/suppression-list", headers=auth, json={"type": "email", "value": "nope"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_contact"


# ---------------------------------------------------------------------------
# Leads: filters / sort, weights
# ---------------------------------------------------------------------------
async def test_lead_filters_and_sort_by_contactability(client, db, world, auth):
    await _sync(client, auth)
    await _scan(client, auth, world["alpha"])
    await _scan(client, auth, world["beta"])

    def titles(page) -> list[str]:
        return [i["channel"]["title"] for i in page["items"]]

    async def leads(**params):
        r = await client.get("/api/leads", params=params)
        assert r.status_code == 200, r.text
        return r.json()

    assert titles(await leads(contact_type="telegram")) == ["Alpha"]
    assert titles(await leads(contact_type=["instagram", "vk"])) == ["Alpha"]
    assert titles(await leads(min_contactability=50)) == ["Alpha"]
    # never scanned (Gamma) is no data: not even «0 or more»
    assert sorted(titles(await leads(min_contactability=0))) == ["Alpha", "Beta"]
    ordered = titles(await leads(sort="contactability", order="desc"))
    assert ordered[:2] == ["Alpha", "Beta"]
    assert (await client.get("/api/leads", params={"min_contactability": 101})).status_code == 422
    assert (await client.get("/api/leads", params={"contact_type": "fax"})).status_code == 422


async def test_contactability_weights_are_versioned_per_workspace_and_rescore(client, db, world, auth):
    await _sync(client, auth)
    await _scan(client, auth, world["alpha"])
    r = await client.get("/api/scoring-profiles/contactability")
    assert r.status_code == 200
    assert r.json()["version"] == 0 and r.json()["is_default"] and r.json()["weights"]["email"] == 30

    new = {"email": 50, "telegram": 10, "website": 0, "vk": 0, "other": 0}
    r = await client.put("/api/scoring-profiles/contactability", headers=auth, json=new)
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["version"] == 1 and not saved["is_default"] and saved["rescored"]["scores_changed"] >= 1
    lead = await _lead(client, world["alpha"])
    assert Decimal(lead["score"]["contactability_score"]) == Decimal("60")  # email 50 + telegram 10
    assert lead["score"]["contactability_profile_version"] == 1
    assert (await client.get("/api/scoring-profiles/contactability")).json()["version"] == 1

    zero = await client.put(
        "/api/scoring-profiles/contactability",
        headers=auth,
        json={"email": 0, "telegram": 0, "website": 0, "vk": 0, "other": 0},
    )
    assert zero.status_code == 422
    over = await client.put("/api/scoring-profiles/contactability", headers=auth, json=new | {"email": 101})
    assert over.status_code == 422


# ---------------------------------------------------------------------------
# Project scan job + stats
# ---------------------------------------------------------------------------
async def test_project_scan_job_selects_unscanned_channels_and_stats(client, db, world, auth):
    pid = world["project"].id
    stats = (await client.get(f"/api/projects/{pid}/contacts/stats")).json()
    assert stats["channels"] == 3 and stats["scanned"] == 0 and stats["with_contacts"] == 0

    r = await client.post(f"/api/projects/{pid}/contacts/scan", headers=auth, json={})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["created"] and body["channels"] == 3 and body["remaining"] == 0
    assert body["job"]["type"] == "contact_extraction" and body["job"]["status"] == "queued"
    job = await db.scalar(select(JobRun).where(JobRun.id == body["job"]["id"]))
    assert job.queue == "bulk" and sorted(job.params["channel_ids"]) == sorted(
        c.id for c in (world["alpha"], world["beta"], world["gamma"])
    )
    again = await client.post(f"/api/projects/{pid}/contacts/scan", headers=auth, json={})
    assert again.status_code == 202 and not again.json()["created"]  # the identical active job is reused
    assert again.json()["job"]["id"] == body["job"]["id"]

    # after the channels were scanned there is nothing left (unless a rescan is asked for)
    for ch in (world["alpha"], world["beta"], world["gamma"]):
        await _scan(client, auth, ch)
    job.status = "completed"
    await db.flush()
    nothing = (await client.post(f"/api/projects/{pid}/contacts/scan", headers=auth, json={})).json()
    assert nothing == {"job": None, "created": False, "channels": 0, "remaining": 0}
    forced = await client.post(f"/api/projects/{pid}/contacts/scan", headers=auth, json={"rescan": True})
    assert (
        forced.status_code == 202 and forced.json()["channels"] == 3 and forced.json()["job"]["id"] != job.id
    )

    stats = (await client.get(f"/api/projects/{pid}/contacts/stats")).json()
    assert stats["scanned"] == 3 and stats["with_contacts"] == 2 and stats["not_found"] == 1
    assert (
        stats["by_type"]["email"] == 2 and stats["by_type"]["telegram"] == 1 and stats["by_type"]["vk"] == 1
    )
    assert (await client.get("/api/projects/999999/contacts/stats")).status_code == 404
    assert (await client.post("/api/projects/999999/contacts/scan", headers=auth, json={})).status_code == 404


# ---------------------------------------------------------------------------
# Roles and isolation
# ---------------------------------------------------------------------------
async def test_viewer_reads_but_cannot_change_contacts(client, db, owner, world, auth, settings):
    await _sync(client, auth)
    await _scan(client, auth, world["alpha"])
    contact_id = _item(await _contacts(client, world["alpha"]), "email")["id"]
    await add_member(db, owner[1], "viewer@example.com", WorkspaceRole.VIEWER)
    await login(client, "viewer@example.com")
    h = csrf_headers(client, settings)

    assert (await client.get(f"/api/channels/{world['alpha'].id}/contacts")).status_code == 200
    assert (await client.get("/api/suppression-list")).status_code == 200
    assert (await client.get(f"/api/projects/{world['project'].id}/contacts/stats")).status_code == 200
    attempts = [
        client.post(f"/api/channels/{world['alpha'].id}/contacts/scan", headers=h),
        client.post(
            f"/api/channels/{world['alpha'].id}/contacts",
            headers=h,
            json={"type": "email", "value": "a@b.io"},
        ),
        client.patch(f"/api/contacts/{contact_id}", headers=h, json={"status": "invalid"}),
        client.delete(f"/api/contacts/{contact_id}", headers=h),
        client.post("/api/suppression-list", headers=h, json={"type": "email", "value": "a@b.io"}),
        client.post(f"/api/projects/{world['project'].id}/contacts/scan", headers=h, json={}),
        client.put("/api/scoring-profiles/contactability", headers=h, json={"email": 10}),
    ]
    for attempt in attempts:
        assert (await attempt).status_code == 403


async def test_other_workspace_cannot_touch_our_manual_contacts_or_projects(
    client, db, world, auth, other_owner, settings
):
    await _sync(client, auth)
    manual = await client.post(
        f"/api/channels/{world['gamma'].id}/contacts",
        headers=auth,
        json={"type": "email", "value": "me@gamma.io"},
    )
    assert manual.status_code == 201, manual.text
    manual_id = _item(manual.json()["contacts"], "email")["id"]

    await login(client, "other@example.com")
    h = csrf_headers(client, settings)
    # the channel is not in the other workspace's projects, and neither is the contact
    assert (await client.get(f"/api/channels/{world['gamma'].id}/contacts")).status_code == 404
    assert (
        await client.patch(f"/api/contacts/{manual_id}", headers=h, json={"status": "invalid"})
    ).status_code == 404
    assert (await client.delete(f"/api/contacts/{manual_id}", headers=h)).status_code == 404
    p = await f.project(db, other_owner[1].id, "Other")
    assert (
        await client.post(f"/api/projects/{world['project'].id}/contacts/scan", headers=h, json={})
    ).status_code == 404
    assert (await client.get(f"/api/projects/{p.id}/contacts/stats")).json()["channels"] == 0

    # the same public channel in the other workspace: it sees the public contacts but not our manual one
    db.add(ProjectChannel(project_id=p.id, channel_id=world["gamma"].id))
    await db.flush()
    seen = await _contacts(client, world["gamma"])
    assert (
        seen["items"] == [] and seen["status"] == "not_scanned"
    )  # nothing scanned, our manual one is hidden
    assert (
        await db.scalar(select(func.count()).select_from(Contact).where(Contact.workspace_id.is_not(None)))
        == 1
    )
