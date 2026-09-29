from conftest import add_member, csrf_headers, login
from sqlalchemy import select

from app.domains.identity.models import AuditLog, WorkspaceRole


async def _authed(client, settings, email="owner@example.com"):
    assert (await login(client, email)).status_code == 200
    return csrf_headers(client, settings)


async def test_project_crud_with_audit(client, owner, db, settings):
    h = await _authed(client, settings)
    r = await client.post(
        "/api/projects",
        headers=h,
        json={
            "name": "  Gaming RU  ",
            "region_code": "ru",
            "language": "ru",
            "results_per_query": 100,
            "published_after": "2026-01-01",
            "filter_settings": {"min_subscribers": 1000},
        },
    )
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["name"] == "Gaming RU" and p["region_code"] == "RU" and p["results_per_query"] == 100
    assert p["search_depth"] == 1 and p["videos_to_analyze"] == 12  # defaults
    assert p["queries_count"] == 0 and p["channels_count"] == 0 and p["status"] == "active"

    listed = (await client.get("/api/projects")).json()
    assert listed["total"] == 1 and listed["items"][0]["id"] == p["id"]

    r = await client.patch(
        f"/api/projects/{p['id']}",
        headers=h,
        json={"status": "archived", "description": "x", "region_code": None},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "archived" and r.json()["region_code"] is None
    assert (await client.get("/api/projects", params={"status": "active"})).json()["total"] == 0
    assert (await client.get("/api/projects", params={"status": "archived"})).json()["total"] == 1

    assert (await client.delete(f"/api/projects/{p['id']}", headers=h)).status_code == 204
    assert (await client.get(f"/api/projects/{p['id']}")).status_code == 404

    actions = (
        await db.scalars(select(AuditLog.action).where(AuditLog.entity_type == "search_project"))
    ).all()
    assert actions == ["project.created", "project.updated", "project.deleted"]


async def test_project_validation_and_name_conflict(client, owner, settings):
    h = await _authed(client, settings)
    for bad in (
        {"name": ""},
        {"name": "x", "results_per_query": 0},
        {"name": "x", "search_depth": 11},
        {"name": "x", "region_code": "RUS"},
        {"name": "x", "unknown": 1},
    ):
        r = await client.post("/api/projects", headers=h, json=bad)
        assert r.status_code == 422, (bad, r.text)
    assert (await client.post("/api/projects", headers=h, json={"name": "Dup"})).status_code == 201
    r = await client.post("/api/projects", headers=h, json={"name": "Dup"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "project_name_taken"
    other = (await client.post("/api/projects", headers=h, json={"name": "Other"})).json()
    r = await client.patch(f"/api/projects/{other['id']}", headers=h, json={"name": "Dup"})
    assert r.status_code == 409


async def test_projects_are_isolated_between_workspaces(client, owner, other_owner, settings):
    h = await _authed(client, settings, "other@example.com")
    pid = (await client.post("/api/projects", headers=h, json={"name": "Secret project"})).json()["id"]
    await client.post("/api/auth/logout", headers=h)

    h = await _authed(client, settings)
    assert (await client.get("/api/projects")).json()["total"] == 0
    assert (await client.get(f"/api/projects/{pid}")).status_code == 404
    assert (await client.patch(f"/api/projects/{pid}", headers=h, json={"name": "x"})).status_code == 404
    assert (await client.delete(f"/api/projects/{pid}", headers=h)).status_code == 404
    assert (await client.get(f"/api/projects/{pid}/queries")).status_code == 404
    r = await client.post(f"/api/projects/{pid}/queries/bulk", headers=h, json={"queries": ["x"]})
    assert r.status_code == 404


async def test_viewer_can_read_but_not_write(client, owner, db, settings):
    await add_member(db, owner[1], "viewer@example.com", WorkspaceRole.VIEWER)
    h = await _authed(client, settings, "viewer@example.com")
    assert (await client.get("/api/projects")).status_code == 200
    assert (await client.post("/api/projects", headers=h, json={"name": "x"})).status_code == 403


async def test_bulk_import_normalizes_dedupes_and_is_idempotent(client, owner, settings):
    h = await _authed(client, settings)
    pid = (await client.post("/api/projects", headers=h, json={"name": "Q"})).json()["id"]

    text = "Minecraft  mods\n\n  minecraft MODS \nroblox\n" + "x" * 301 + "\nＦｕｌｌｗｉｄｔｈ\n"
    r = await client.post(f"/api/projects/{pid}/queries/bulk", headers=h, json={"text": text})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["created"] == 3 and res["duplicates"] == 1
    assert [q["text"] for q in res["items"]] == ["Minecraft mods", "roblox", "Fullwidth"]
    assert res["rejected"] == [{"line": 5, "value": "x" * 80, "reason": "longer than 300"}]

    again = (
        await client.post(
            f"/api/projects/{pid}/queries/bulk", headers=h, json={"queries": ["ROBLOX", "new one"]}
        )
    ).json()
    assert again["created"] == 1 and again["duplicates"] == 1

    listed = (await client.get(f"/api/projects/{pid}/queries", params={"limit": 2})).json()
    assert listed["total"] == 4 and len(listed["items"]) == 2
    assert (await client.get(f"/api/projects/{pid}")).json()["queries_count"] == 4
    assert (await client.get(f"/api/projects/{pid}/queries", params={"status": "done"})).json()["total"] == 0

    r = await client.post(
        f"/api/projects/{pid}/queries/bulk", headers=h, json={"queries": ["a"], "text": "b"}
    )
    assert r.status_code == 422

    qid = listed["items"][0]["id"]
    assert (await client.delete(f"/api/projects/{pid}/queries/{qid}", headers=h)).status_code == 204
    assert (await client.delete(f"/api/projects/{pid}/queries/{qid}", headers=h)).status_code == 404
