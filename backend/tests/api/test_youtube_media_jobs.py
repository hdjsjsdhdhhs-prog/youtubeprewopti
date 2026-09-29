import factories as f
import pytest
from conftest import login
from sqlalchemy import func, select

from app.domains.media.imaging import InvalidImageError
from app.domains.media.models import ImageAsset, ImageSource
from app.domains.media.service import store_image


@pytest.fixture
async def seeded(db, owner, other_owner, storage):
    """Workspace A sees channels c1, c2 (c2 via two projects); c3 belongs only to workspace B."""
    ws_a, ws_b = owner[1], other_owner[1]
    p1, p2 = await f.project(db, ws_a.id, "Gaming"), await f.project(db, ws_a.id, "Music")
    pb = await f.project(db, ws_b.id, "B project")
    c1 = await f.channel(
        db, projects=[p1], title="Alpha 100% Gaming", subscriber_count=5_000, country="RU", handle="@alpha"
    )
    c2 = await f.channel(db, projects=[p1, p2], title="Beta Music", subscriber_count=50_000, country="US")
    c3 = await f.channel(db, projects=[pb], title="Gamma Hidden", subscriber_count=10)
    asset = await store_image(db, storage, f.png_bytes(), ImageSource.YOUTUBE_THUMBNAIL)
    other_asset = await store_image(db, storage, f.png_bytes((0, 0, 255)), ImageSource.YOUTUBE_THUMBNAIL)
    v_new = await f.video(db, c1, days_ago=1, asset=asset)
    v_old = await f.video(db, c1, days_ago=30)
    v_hidden = await f.video(db, c3, asset=other_asset)
    return {
        "p1": p1,
        "p2": p2,
        "c1": c1,
        "c2": c2,
        "c3": c3,
        "asset": asset,
        "other_asset": other_asset,
        "v_new": v_new,
        "v_old": v_old,
        "v_hidden": v_hidden,
    }


async def test_channels_list_visibility_filters_sort_pagination(client, seeded):
    await login(client, "owner@example.com")
    r = await client.get("/api/channels")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2  # c3 is invisible; c2 counted once despite two projects
    assert [c["title"] for c in body["items"]] == ["Beta Music", "Alpha 100% Gaming"]  # subscribers desc

    async def titles(**params):
        return [c["title"] for c in (await client.get("/api/channels", params=params)).json()["items"]]

    assert await titles(sort="subscribers", order="asc") == ["Alpha 100% Gaming", "Beta Music"]
    assert await titles(project_id=seeded["p2"].id) == ["Beta Music"]
    assert await titles(q="100%") == ["Alpha 100% Gaming"]  # LIKE wildcards are escaped
    assert await titles(q="%") == ["Alpha 100% Gaming"]  # literal '%', not "match everything"
    assert await titles(q="_") == []  # literal '_', not "any single character"
    assert await titles(q="ALPHA") == ["Alpha 100% Gaming"]
    assert await titles(q="Gamma") == []
    assert await titles(min_subscribers=10_000) == ["Beta Music"]
    assert await titles(max_subscribers=10_000, country="ru") == ["Alpha 100% Gaming"]
    page = (await client.get("/api/channels", params={"limit": 1, "offset": 1})).json()
    assert page["total"] == 2 and len(page["items"]) == 1 and page["items"][0]["title"] == "Alpha 100% Gaming"
    assert (await client.get("/api/channels", params={"sort": "discovered"})).status_code == 200
    assert (await client.get("/api/channels", params={"limit": 501})).status_code == 422


async def test_channel_detail_and_videos(client, seeded):
    await login(client, "owner@example.com")
    c2 = (await client.get(f"/api/channels/{seeded['c2'].id}")).json()
    assert [p["name"] for p in c2["projects"]] == ["Gaming", "Music"]
    assert c2["url"].endswith(seeded["c2"].youtube_channel_id)

    vids = (await client.get(f"/api/channels/{seeded['c1'].id}/videos")).json()
    assert vids["total"] == 2
    assert [v["id"] for v in vids["items"]] == [seeded["v_new"].id, seeded["v_old"].id]  # newest first
    thumb = vids["items"][0]["thumbnail"]
    assert thumb["fetch_status"] == "ok" and thumb["image_url"] == f"/api/images/{seeded['asset'].id}"
    assert thumb["width"] == 64 and thumb["height"] == 36
    assert vids["items"][1]["thumbnail"]["image_url"] is None

    detail = (await client.get(f"/api/videos/{seeded['v_new'].id}")).json()
    assert detail["youtube_video_id"] == seeded["v_new"].youtube_video_id and "description" in detail


async def test_other_workspace_entities_are_404(client, seeded):
    await login(client, "owner@example.com")
    assert (await client.get(f"/api/channels/{seeded['c3'].id}")).status_code == 404
    assert (await client.get(f"/api/channels/{seeded['c3'].id}/videos")).status_code == 404
    assert (await client.get(f"/api/videos/{seeded['v_hidden'].id}")).status_code == 404
    assert (await client.get(f"/api/images/{seeded['other_asset'].id}")).status_code == 404
    assert (await client.get("/api/images/999999999")).status_code == 404


async def test_image_serving_with_cache_validation(client, seeded, storage):
    asset = seeded["asset"]
    assert (await client.get(f"/api/images/{asset.id}")).status_code == 401  # anonymous

    await login(client, "owner@example.com")
    r = await client.get(f"/api/images/{asset.id}")
    assert r.status_code == 200
    assert r.content == f.png_bytes() and r.headers["content-type"] == "image/png"
    assert r.headers["etag"] == f'"{asset.sha256}"'
    assert (
        r.headers["cache-control"].startswith("private") and r.headers["x-content-type-options"] == "nosniff"
    )

    r304 = await client.get(f"/api/images/{asset.id}", headers={"If-None-Match": r.headers["etag"]})
    assert r304.status_code == 304 and r304.content == b""

    storage.delete(asset.storage_key)
    missing = await client.get(f"/api/images/{asset.id}")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "image_missing"


async def test_store_image_deduplicates_and_validates(db, storage):
    data = f.png_bytes((1, 2, 3))
    a = await store_image(db, storage, data, ImageSource.UPLOAD)
    b = await store_image(db, storage, data, ImageSource.YOUTUBE_THUMBNAIL)
    assert a.id == b.id and a.format == "png" and a.mime == "image/png"
    assert (
        await db.scalar(select(func.count()).select_from(ImageAsset).where(ImageAsset.sha256 == a.sha256))
        == 1
    )
    with pytest.raises(InvalidImageError):
        await store_image(db, storage, b"<svg>not allowed</svg>", ImageSource.UPLOAD)


async def test_jobs_list_filter_detail_and_isolation(client, db, owner, other_owner):
    ws_a, ws_b = owner[1], other_owner[1]
    j1 = await f.job_run(db, ws_a.id, type="thumbnail_download", params={"video_id": 1})
    await f.job_run(db, ws_a.id, type="discovery", status="failed", error_code="quota_exceeded")
    jb = await f.job_run(db, ws_b.id)

    await login(client, "owner@example.com")
    body = (await client.get("/api/jobs")).json()
    assert body["total"] == 2
    assert (await client.get("/api/jobs", params={"status": "failed"})).json()["items"][0][
        "error_code"
    ] == "quota_exceeded"
    assert (await client.get("/api/jobs", params={"type": "thumbnail_download"})).json()["total"] == 1
    detail = (await client.get(f"/api/jobs/{j1.id}")).json()
    assert detail["params"] == {"video_id": 1} and detail["status"] == "queued"
    assert (await client.get(f"/api/jobs/{jb.id}")).status_code == 404
    assert (await client.get("/api/jobs", params={"status": "bogus"})).status_code == 422
