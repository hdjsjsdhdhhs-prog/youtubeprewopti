import factories as f
from conftest import add_member, csrf_headers, login
from sqlalchemy import text

from app.domains.identity.models import WorkspaceRole
from app.domains.media.models import ImageSource
from app.domains.media.service import ensure_image_metrics, store_image


async def _seed(db, owner, other_owner, storage):
    p = await f.project(db, owner[1].id)
    ch = await f.channel(db, projects=[p])
    asset = await store_image(db, storage, f.png_bytes(), ImageSource.YOUTUBE_THUMBNAIL)
    await ensure_image_metrics(db, storage, asset)  # fully ingested => not queued again
    stored = await f.video(db, ch, asset=asset)
    pending = [await f.video(db, ch), await f.video(db, ch)]
    hidden = await f.channel(db, projects=[await f.project(db, other_owner[1].id)])
    empty = await f.channel(db, projects=[p])
    return ch, stored, pending, hidden, empty


async def test_enqueue_channel_thumbnails(client, db, owner, other_owner, storage, settings):
    ch, stored, pending, hidden, empty = await _seed(db, owner, other_owner, storage)
    url = f"/api/channels/{ch.id}/thumbnails/download"
    assert (await client.post(url)).status_code == 401

    await login(client, "owner@example.com")
    assert (await client.post(url)).status_code == 403  # no CSRF token
    h = csrf_headers(client, settings)

    r = await client.post(url, headers=h)
    assert r.status_code == 202
    body = r.json()
    assert body["created"] and body["items"] == 2
    job = body["job"]
    assert (job["type"], job["status"], job["queue"], job["progress_total"]) == (
        "thumbnail_download", "queued", "bulk", 2)
    detail = (await client.get(f"/api/jobs/{job['id']}")).json()
    assert detail["params"] == {"video_ids": sorted(v.id for v in pending), "channel_id": ch.id}
    assert stored.id not in detail["params"]["video_ids"]

    again = (await client.post(url, headers=h)).json()
    assert not again["created"] and again["job"]["id"] == job["id"]
    assert await db.scalar(text("SELECT count(*) FROM procrastinate_jobs")) == 1

    nothing = await client.post(f"/api/channels/{empty.id}/thumbnails/download", headers=h)
    assert nothing.status_code == 202 and nothing.json() == {"job": None, "created": False, "items": 0}
    assert (await client.post(f"/api/channels/{hidden.id}/thumbnails/download", headers=h)).status_code == 404


async def test_cancel_job_endpoint(client, db, owner, other_owner, storage, settings):
    ch, *_ = await _seed(db, owner, other_owner, storage)
    await login(client, "owner@example.com")
    h = csrf_headers(client, settings)
    job_id = (await client.post(f"/api/channels/{ch.id}/thumbnails/download", headers=h)).json()["job"]["id"]
    other_job = await f.job_run(db, other_owner[1].id)

    assert (await client.post(f"/api/jobs/{other_job.id}/cancel", headers=h)).status_code == 404
    r = await client.post(f"/api/jobs/{job_id}/cancel", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    again = await client.post(f"/api/jobs/{job_id}/cancel", headers=h)
    assert again.status_code == 409 and again.json()["error"]["code"] == "invalid_transition"

    # a new download request is allowed once the previous job is no longer active
    assert (await client.post(f"/api/channels/{ch.id}/thumbnails/download", headers=h)).json()["created"]


async def test_viewer_cannot_enqueue_or_cancel(client, db, owner, other_owner, storage, settings):
    ch, *_ = await _seed(db, owner, other_owner, storage)
    job = await f.job_run(db, owner[1].id)
    await add_member(db, owner[1], "viewer@example.com", WorkspaceRole.VIEWER)
    await login(client, "viewer@example.com")
    h = csrf_headers(client, settings)
    assert (await client.post(f"/api/channels/{ch.id}/thumbnails/download", headers=h)).status_code == 403
    assert (await client.post(f"/api/jobs/{job.id}/cancel", headers=h)).status_code == 403
    assert (await client.get(f"/api/jobs/{job.id}")).json()["status"] == "queued"
