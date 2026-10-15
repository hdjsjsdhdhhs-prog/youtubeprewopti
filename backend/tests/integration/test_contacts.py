"""Phase 6 integration: contacts found by discovery, the contact_extraction job body, task registration."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import factories as f
import pytest
from sqlalchemy import func, select

import app.workers.tasks  # noqa: F401  (registers tasks on queue_app)
from app.domains.contacts.models import ChannelContactScan, Contact, ContactSource, ContactType
from app.domains.contacts.service import ScanCounts, run_contact_extraction, scan_channels
from app.domains.discovery.quota import SessionQuota
from app.domains.discovery.service import run_query
from app.domains.jobs.service import JobType
from app.domains.projects.models import SearchQuery
from app.providers.youtube import MockYouTubeProvider
from app.workers.queue import queue_app

NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)


class ContactsMockProvider(MockYouTubeProvider):
    """The offline mock, but every channel publishes an e-mail address in its description."""

    async def get_channels(self, channel_ids):
        return [
            replace(c, description=f"Business: team@{c.youtube_channel_id.lower()}.example.io")
            for c in await super().get_channels(channel_ids)
        ]


@pytest.fixture
def sessions(db):
    @asynccontextmanager
    async def shared():
        yield db

    return shared


async def _count(db, model, *where) -> int:
    return await db.scalar(select(func.count()).select_from(model).where(*where)) or 0


async def test_discovery_scans_the_texts_of_every_fetched_channel(db, owner, settings):
    project = await f.project(db, owner[1].id, "Discovery")
    project.results_per_query, project.search_depth, project.videos_to_analyze = 10, 2, 3
    query = SearchQuery(project_id=project.id, text="etf", text_normalized="etf")
    db.add(query)
    await db.flush()
    provider, quota = ContactsMockProvider(), SessionQuota(db, "youtube_mock", 10_000)

    out = await run_query(db, provider, quota, settings, project, query, now=NOW)
    assert out.channels_fetched == 10 and out.contacts_new == 10
    assert await _count(db, ChannelContactScan) == 10
    contacts = list(await db.scalars(select(Contact)))
    assert len(contacts) == 10 and {c.type for c in contacts} == {ContactType.EMAIL}
    assert {c.source for c in contacts} == {ContactSource.YOUTUBE_DESCRIPTION}
    assert all(c.workspace_id is None for c in contacts)  # public contacts are global, like channels

    # the channels are fresh now: nothing is fetched, nothing is scanned twice
    again = await run_query(db, provider, quota, settings, project, query, now=NOW)
    assert again.channels_fetched == 0 and again.contacts_new == 0
    assert await _count(db, Contact) == 10 and await _count(db, ChannelContactScan) == 10


async def test_discovery_without_contacts_still_records_the_scan(db, owner, settings):
    """The plain mock publishes no contact: every channel is scanned and is «not found», not «not scanned»."""
    project = await f.project(db, owner[1].id, "Plain")
    project.results_per_query, project.search_depth, project.videos_to_analyze = 5, 1, 2
    query = SearchQuery(project_id=project.id, text="plain", text_normalized="plain")
    db.add(query)
    await db.flush()

    out = await run_query(
        db, MockYouTubeProvider(), SessionQuota(db, "youtube_mock", 10_000), settings, project, query, now=NOW
    )
    assert out.channels_fetched == 5 and out.contacts_new == 0
    assert await _count(db, ChannelContactScan) == 5 and await _count(db, Contact) == 0


async def test_contact_extraction_job_body_is_resumable_and_reports_counts(db, owner, sessions):
    project = await f.project(db, owner[1].id, "Job")
    a = await f.channel(db, projects=[project], description="mail a@a.io")
    b = await f.channel(db, projects=[project], description="nothing here")
    c = await f.channel(db, projects=[project], description="t.me/c_chan and c@c.io")
    progress = AsyncMock()
    params = {"project_id": project.id, "channel_ids": [a.id, b.id, c.id], "rescan": False}

    first = await run_contact_extraction(sessions, params=params, progress=progress)
    assert first == {
        "channels_scanned": 3,
        "channels_unchanged": 0,
        "contacts_new": 3,
        "contacts_updated": 0,
        "contacts_gone": 0,
        "channels": 3,
    }
    progress.assert_awaited_with(3, 3)

    # a retried job (or a second run) skips channels whose texts did not change ...
    second = await run_contact_extraction(sessions, params=params, progress=progress)
    assert (
        second["channels_scanned"] == 0 and second["channels_unchanged"] == 3 and second["contacts_new"] == 0
    )
    # ... unless a rescan is asked for; then nothing is duplicated either
    forced = await run_contact_extraction(sessions, params=params | {"rescan": True}, progress=progress)
    assert forced["channels_scanned"] == 3 and forced["contacts_new"] == 0 and forced["contacts_updated"] == 0
    assert await _count(db, Contact) == 3

    # changed texts: the new contact is added, the vanished one is kept as history
    c.description = "t.me/c_chan_new"
    await db.flush()
    third = await run_contact_extraction(sessions, params=params, progress=progress)
    assert third["channels_scanned"] == 1 and third["contacts_new"] == 1 and third["contacts_gone"] == 2
    assert await _count(db, Contact) == 4 and await _count(db, Contact, Contact.present.is_(False)) == 2


async def test_scan_batches_are_independent_of_the_input_order(db, owner):
    project = await f.project(db, owner[1].id, "Order")
    channels = [await f.channel(db, projects=[project], description=f"x{i}@x.io") for i in range(3)]
    ids = [ch.id for ch in channels]
    counts = ScanCounts()
    counts.add(await scan_channels(db, reversed(ids), NOW))
    counts.add(await scan_channels(db, ids + ids, NOW))  # duplicates in the input are scanned once
    assert counts.channels_scanned == 3 and counts.channels_unchanged == 3 and counts.contacts_new == 3
    assert counts.channel_ids == set(ids)
    assert (await scan_channels(db, [], NOW)).as_result() == ScanCounts().as_result()


def test_the_contact_extraction_task_is_registered_in_the_bulk_queue():
    task = queue_app.tasks[JobType.CONTACT_EXTRACTION.value]
    assert task.queue == "bulk"
