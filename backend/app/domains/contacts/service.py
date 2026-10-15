"""Contacts (Phase 6, ТЗ §18–19): scanning channel texts, the workspace view of contacts, manual contacts,
statuses, the suppression list and the Contactability weights.

* **Scan** (``scan_channels``) — deterministic (``extract.py``), global: public contacts are shared by all
  workspaces like channels. Runs during discovery for every (re)fetched channel, as the
  ``contact_extraction`` job for a project and on demand for one channel. A channel whose texts did not
  change since the last scan with the current extractor is skipped. Contacts that disappeared from the texts
  are kept with ``present = false`` (history) and no longer count.
* **Workspace view** — a workspace sees the public contacts of its channels plus its own manual ones; its
  verdicts are in ``contact_states``; values in its ``suppression_list`` are never usable. Every change
  rescores the leads of the affected channels.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, delete, exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core.errors import AppError, ConflictError, NotFoundError
from app.domains.analysis import scoring as profiles
from app.domains.analysis.models import ScoringKind
from app.domains.contacts import scoring
from app.domains.contacts.extract import (
    EXTRACTOR_VERSION,
    TYPE_ORDER,
    TextSource,
    extract_channel,
    normalize_manual,
)
from app.domains.contacts.models import (
    UNUSABLE,
    ChannelContactScan,
    Contact,
    ContactSource,
    ContactState,
    ContactStatus,
    ContactType,
    SuppressionEntry,
    SuppressionReason,
)
from app.domains.contacts.schemas import (
    ChannelContacts,
    ContactabilityProfileOut,
    ContactabilityProfileSaved,
    ContactChange,
    ContactOut,
    ContactScanOut,
    ContactScanStart,
    ContactScanStartResult,
    ContactStats,
    ContactStatusIn,
    ManualContactIn,
    SuppressionIn,
    SuppressionList,
    SuppressionOut,
)
from app.domains.identity.service import audit
from app.domains.jobs.schemas import JobRunOut
from app.domains.jobs.service import JobQueue, JobType, enqueue_job
from app.domains.leads.results import RescoreResult
from app.domains.projects.models import ProjectChannel
from app.domains.projects.service import get_project
from app.domains.youtube.models import Channel, Video
from app.domains.youtube.service import get_visible_channel

SCAN_VIDEOS = 50  # newest stored videos of a channel whose descriptions are scanned
SCAN_BATCH = 50  # channels per transaction in the job
MAX_SCAN_CHANNELS = 5000  # per job


class InvalidContactError(AppError):
    status_code = 422
    code = "invalid_contact"


# ---------------------------------------------------------------------------
# Scanning (global)
# ---------------------------------------------------------------------------
@dataclass
class ScanCounts:
    channels_scanned: int = 0
    channels_unchanged: int = 0
    contacts_new: int = 0
    contacts_updated: int = 0
    contacts_gone: int = 0
    channel_ids: set[int] = field(default_factory=set)  # scanned (texts changed or forced)

    def add(self, other: ScanCounts) -> None:
        for k in (
            "channels_scanned",
            "channels_unchanged",
            "contacts_new",
            "contacts_updated",
            "contacts_gone",
        ):
            setattr(self, k, getattr(self, k) + getattr(other, k))
        self.channel_ids |= other.channel_ids

    def as_result(self) -> dict[str, int]:
        return {
            "channels_scanned": self.channels_scanned,
            "channels_unchanged": self.channels_unchanged,
            "contacts_new": self.contacts_new,
            "contacts_updated": self.contacts_updated,
            "contacts_gone": self.contacts_gone,
        }


def _video_url(youtube_video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={youtube_video_id}"


async def _texts(db: AsyncSession, channel_ids: Sequence[int]) -> dict[int, list[TextSource]]:
    out: dict[int, list[TextSource]] = {}
    for c in await db.scalars(select(Channel).where(Channel.id.in_(channel_ids)).order_by(Channel.id)):
        out[c.id] = [TextSource(c.description or "", ContactSource.YOUTUBE_DESCRIPTION, c.url)]
    ranked = (
        select(
            Video.channel_id,
            Video.id,
            Video.youtube_video_id,
            Video.description,
            func.row_number()
            .over(
                partition_by=Video.channel_id,
                order_by=(Video.published_at.desc().nulls_last(), Video.id.desc()),
            )
            .label("rn"),
        )
        .where(Video.channel_id.in_(channel_ids))
        .subquery()
    )
    rows = await db.execute(
        select(ranked.c.channel_id, ranked.c.id, ranked.c.youtube_video_id, ranked.c.description)
        .where(ranked.c.rn <= SCAN_VIDEOS)
        .order_by(ranked.c.channel_id, ranked.c.rn)
    )
    for cid, vid, yt_id, desc in rows.tuples():
        if cid in out and desc:
            out[cid].append(TextSource(desc, ContactSource.VIDEO_DESCRIPTION, _video_url(yt_id), vid))
    return out


def _digest(sources: Sequence[TextSource]) -> str:
    payload = [EXTRACTOR_VERSION, [[s.source.value, s.video_id, s.text] for s in sources]]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest()


async def scan_channels(
    db: AsyncSession, channel_ids: Iterable[int], now: datetime | None = None, *, force: bool = False
) -> ScanCounts:
    """Scan the channels' description + newest video descriptions; the caller commits."""
    ids = sorted(set(channel_ids))
    counts = ScanCounts()
    if not ids:
        return counts
    now = now or datetime.now(UTC)
    texts = await _texts(db, ids)
    scans = {
        s.channel_id: s
        for s in await db.scalars(select(ChannelContactScan).where(ChannelContactScan.channel_id.in_(ids)))
    }
    existing: dict[int, dict[tuple[ContactType, str], Contact]] = {}
    for c in await db.scalars(
        select(Contact).where(Contact.channel_id.in_(ids), Contact.workspace_id.is_(None))
    ):
        existing.setdefault(c.channel_id, {})[(c.type, c.value_normalized)] = c

    for cid, sources in texts.items():
        digest = _digest(sources)
        scan = scans.get(cid)
        if (
            not force
            and scan is not None
            and scan.extractor_version == EXTRACTOR_VERSION
            and scan.inputs_digest == digest
        ):
            counts.channels_unchanged += 1
            continue
        found = extract_channel(sources)
        known = existing.get(cid, {})
        keys = set()
        for f in found:
            key = (f.type, f.normalized)
            keys.add(key)
            values = {
                "value": f.value,
                "source": f.source,
                "source_url": f.source_url,
                "source_video_id": f.source_video_id,
                "seen_in_videos": f.seen_in_videos,
                "confidence": Decimal(str(f.confidence)),
            }
            row = known.get(key)
            if row is None:
                db.add(
                    Contact(
                        channel_id=cid,
                        workspace_id=None,
                        type=f.type,
                        value_normalized=f.normalized,
                        status=ContactStatus.PUBLIC,
                        present=True,
                        discovered_at=now,
                        last_seen_at=now,
                        **values,
                    )
                )
                counts.contacts_new += 1
                continue
            changed = not row.present or any(getattr(row, k) != v for k, v in values.items())
            for k, v in values.items():
                setattr(row, k, v)
            row.present = True
            row.last_seen_at = now
            counts.contacts_updated += int(changed)
        for key, row in known.items():
            if key not in keys and row.present:
                row.present = False
                counts.contacts_gone += 1
        values_scan = {
            "extractor_version": EXTRACTOR_VERSION,
            "inputs_digest": digest,
            "texts_scanned": sum(1 for s in sources if s.text),
            "contacts_found": len(found),
            "scanned_at": now,
        }
        await db.execute(
            insert(ChannelContactScan)
            .values(channel_id=cid, **values_scan)
            .on_conflict_do_update(index_elements=["channel_id"], set_=values_scan)
        )
        counts.channels_scanned += 1
        counts.channel_ids.add(cid)
    await db.flush()
    return counts


async def run_contact_extraction(
    sessions: Callable[[], AsyncSession],
    *,
    params: dict[str, Any],
    progress: Callable[[int, int], Any],
) -> dict[str, Any]:
    """Worker body of a ``contact_extraction`` job (deterministic, free); every batch commits on its own, so a
    retried job resumes with unchanged channels skipped."""
    ids = [int(c) for c in params.get("channel_ids", [])]
    force = bool(params.get("rescan", False))
    total = ScanCounts()
    for start in range(0, len(ids), SCAN_BATCH):
        batch = ids[start : start + SCAN_BATCH]
        async with sessions() as db:
            total.add(await scan_channels(db, batch, force=force))
            await db.commit()
        await progress(min(start + SCAN_BATCH, len(ids)), len(ids))
    return {**total.as_result(), "channels": len(ids)}


# ---------------------------------------------------------------------------
# The workspace view
# ---------------------------------------------------------------------------
def _state_join(workspace_id: int) -> ColumnElement[bool]:
    return and_(ContactState.contact_id == Contact.id, ContactState.workspace_id == workspace_id)


def _effective_status() -> ColumnElement[Any]:
    return func.coalesce(ContactState.status, Contact.status)


def _suppressed(workspace_id: int) -> ColumnElement[bool]:
    return exists(
        select(SuppressionEntry.id).where(
            SuppressionEntry.workspace_id == workspace_id,
            SuppressionEntry.type == Contact.type,
            SuppressionEntry.value_normalized == Contact.value_normalized,
        )
    )


def _visible_contact(workspace_id: int) -> ColumnElement[bool]:
    return or_(Contact.workspace_id.is_(None), Contact.workspace_id == workspace_id)


def _usable(workspace_id: int) -> ColumnElement[bool]:
    return and_(
        Contact.present,
        _visible_contact(workspace_id),
        _effective_status().not_in(list(UNUSABLE)),
        ~_suppressed(workspace_id),
    )


def has_usable_contact(
    workspace_id: int,
    channel_id: ColumnElement[Any] | InstrumentedAttribute[int],
    types: Sequence[ContactType] = (),
) -> ColumnElement[bool]:
    """EXISTS a usable contact of the channel (of any of ``types``) — for lead filters."""
    stmt: Select[Any] = (
        select(Contact.id)
        .outerjoin(ContactState, _state_join(workspace_id))
        .where(Contact.channel_id == channel_id, _usable(workspace_id))
    )
    if types:
        stmt = stmt.where(Contact.type.in_(list(types)))
    return exists(stmt)


async def contact_inputs(
    db: AsyncSession, workspace_id: int, channel_ids: Sequence[int]
) -> tuple[set[int], dict[int, set[str]]]:
    """(channels with contact data — scanned or with a manual contact, usable contact types per channel)."""
    ids = list(channel_ids)
    if not ids:
        return set(), {}
    scanned = set(
        await db.scalars(select(ChannelContactScan.channel_id).where(ChannelContactScan.channel_id.in_(ids)))
    )
    scanned |= set(
        await db.scalars(
            select(Contact.channel_id).where(
                Contact.channel_id.in_(ids), Contact.workspace_id == workspace_id
            )
        )
    )
    types: dict[int, set[str]] = {}
    rows = await db.execute(
        select(Contact.channel_id, Contact.type)
        .outerjoin(ContactState, _state_join(workspace_id))
        .where(Contact.channel_id.in_(ids), _usable(workspace_id))
        .distinct()
    )
    for cid, t in rows.tuples():
        types.setdefault(cid, set()).add(t.value)
    return scanned, types


@dataclass(frozen=True)
class ActiveContactWeights:
    version: int  # 0 = built-in defaults
    weights: scoring.ContactabilityWeights


async def active_contact_weights(db: AsyncSession, workspace_id: int) -> ActiveContactWeights:
    row = await profiles.active_profile(db, workspace_id, ScoringKind.CONTACTABILITY)
    if row is None:
        return ActiveContactWeights(0, scoring.ContactabilityWeights())
    return ActiveContactWeights(row.version, scoring.ContactabilityWeights.model_validate(row.weights))


def contact_profile_out(a: ActiveContactWeights) -> ContactabilityProfileOut:
    return ContactabilityProfileOut(version=a.version, is_default=a.version == 0, weights=a.weights)


async def _rescore(db: AsyncSession, workspace_id: int, channel_ids: Iterable[int]) -> RescoreResult:
    from app.domains.leads.service import sync_leads  # leads → contacts; avoid a cycle

    return await sync_leads(db, workspace_id, channel_ids=sorted(set(channel_ids)))


async def set_contact_weights(
    db: AsyncSession, workspace_id: int, actor_id: int, weights: scoring.ContactabilityWeights
) -> ContactabilityProfileSaved:
    from app.domains.leads.service import sync_leads

    before = await active_contact_weights(db, workspace_id)
    row = await profiles.save_profile_version(
        db, workspace_id, actor_id, ScoringKind.CONTACTABILITY, weights.as_dict(), before.weights.as_dict()
    )
    rescored = await sync_leads(db, workspace_id)
    return ContactabilityProfileSaved(
        **contact_profile_out(ActiveContactWeights(row.version, weights)).model_dump(), rescored=rescored
    )


def _contact_out(c: Contact, state: ContactState | None, suppressed: bool, workspace_id: int) -> ContactOut:
    status = state.status if state is not None else c.status
    return ContactOut(
        id=c.id,
        type=c.type,
        value=c.value,
        value_normalized=c.value_normalized,
        source=c.source,
        source_url=c.source_url,
        source_video_id=c.source_video_id,
        seen_in_videos=c.seen_in_videos,
        confidence=c.confidence,
        status=status,
        present=c.present,
        suppressed=suppressed,
        usable=c.present and status not in UNUSABLE and not suppressed,
        is_manual=c.workspace_id == workspace_id,
        note=state.note if state is not None and state.note else c.note,
        discovered_at=c.discovered_at,
        last_seen_at=c.last_seen_at,
        verified_at=state.verified_at if state is not None else c.verified_at,
    )


async def channel_contacts(db: AsyncSession, workspace_id: int, channel_id: int) -> ChannelContacts:
    await get_visible_channel(db, workspace_id, channel_id)
    rows = await db.execute(
        select(Contact, ContactState, _suppressed(workspace_id).label("suppressed"))
        .outerjoin(ContactState, _state_join(workspace_id))
        .where(Contact.channel_id == channel_id, _visible_contact(workspace_id))
    )
    items = [_contact_out(c, st, bool(sup), workspace_id) for c, st, sup in rows.tuples()]
    items.sort(
        key=lambda o: (not o.usable, not o.present, TYPE_ORDER[o.type], -o.confidence, o.value_normalized)
    )
    scan = await db.get(ChannelContactScan, channel_id)
    has_data = scan is not None or any(o.is_manual for o in items)
    usable_types = sorted({o.type for o in items if o.usable}, key=lambda t: TYPE_ORDER[t])
    weights = (await active_contact_weights(db, workspace_id)).weights.as_dict()
    return ChannelContacts(
        channel_id=channel_id,
        status="not_scanned" if not has_data else ("found" if usable_types else "not_found"),
        scan=ContactScanOut.model_validate(scan) if scan is not None else None,
        contactability_score=scoring.contactability_score(usable_types, weights) if has_data else None,
        usable_types=usable_types,
        items=items,
    )


async def scan_channel_now(
    db: AsyncSession, workspace_id: int, actor_id: int, channel_id: int
) -> ContactChange:
    await get_visible_channel(db, workspace_id, channel_id)
    counts = await scan_channels(db, [channel_id], force=True)
    await audit(
        db,
        "contacts.scanned",
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        entity_type="channel",
        entity_id=channel_id,
        diff=counts.as_result(),
    )
    rescored = await _rescore(db, workspace_id, [channel_id])
    return ContactChange(contacts=await channel_contacts(db, workspace_id, channel_id), rescored=rescored)


def _normalized(type_: ContactType, value: str) -> tuple[str, str]:
    norm = normalize_manual(type_, value)
    if norm is None:
        raise InvalidContactError(
            f"'{value}' is not a valid {type_.value} contact", details={"type": type_.value}
        )
    return norm


async def add_manual_contact(
    db: AsyncSession, workspace_id: int, actor_id: int, channel_id: int, body: ManualContactIn
) -> ContactChange:
    await get_visible_channel(db, workspace_id, channel_id)
    display, normalized = _normalized(body.type, body.value)
    duplicate = await db.scalar(
        select(Contact.id).where(
            Contact.channel_id == channel_id,
            Contact.type == body.type,
            Contact.value_normalized == normalized,
            _visible_contact(workspace_id),
        )
    )
    if duplicate is not None:
        raise ConflictError(
            "The channel already has this contact", code="contact_exists", details={"id": duplicate}
        )
    now = datetime.now(UTC)
    status = ContactStatus(body.status)
    row = Contact(
        channel_id=channel_id,
        workspace_id=workspace_id,
        type=body.type,
        value=display,
        value_normalized=normalized,
        source=ContactSource.MANUAL,
        confidence=Decimal("1.00"),
        status=status,
        present=True,
        discovered_at=now,
        last_seen_at=now,
        verified_at=now if status == ContactStatus.VERIFIED else None,
        note=body.note,
        created_by=actor_id,
    )
    db.add(row)
    await db.flush()
    await audit(
        db,
        "contact.added",
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        entity_type="contact",
        entity_id=row.id,
        diff={"channel_id": channel_id, "type": body.type.value, "value": normalized},
    )
    rescored = await _rescore(db, workspace_id, [channel_id])
    return ContactChange(contacts=await channel_contacts(db, workspace_id, channel_id), rescored=rescored)


async def _visible_contact_row(db: AsyncSession, workspace_id: int, contact_id: int) -> Contact:
    row = await db.scalar(select(Contact).where(Contact.id == contact_id, _visible_contact(workspace_id)))
    if row is None:
        raise NotFoundError("Contact not found")
    await get_visible_channel(db, workspace_id, row.channel_id)
    return row


async def _channels_with_value(db: AsyncSession, type_: ContactType, normalized: str) -> list[int]:
    return list(
        await db.scalars(
            select(Contact.channel_id)
            .where(Contact.type == type_, Contact.value_normalized == normalized)
            .distinct()
        )
    )


async def _suppress(
    db: AsyncSession,
    workspace_id: int,
    actor_id: int | None,
    type_: ContactType,
    normalized: str,
    reason: SuppressionReason,
    note: str = "",
) -> bool:
    inserted = await db.scalar(
        insert(SuppressionEntry)
        .values(
            workspace_id=workspace_id,
            type=type_,
            value_normalized=normalized,
            reason=reason,
            note=note,
            created_by=actor_id,
        )
        .on_conflict_do_nothing(index_elements=["workspace_id", "type", "value_normalized"])
        .returning(SuppressionEntry.id)
    )
    return inserted is not None


async def set_contact_status(
    db: AsyncSession, workspace_id: int, actor_id: int, contact_id: int, body: ContactStatusIn
) -> ContactChange:
    row = await _visible_contact_row(db, workspace_id, contact_id)
    state = await db.get(ContactState, (workspace_id, contact_id))
    current = state.status if state is not None else row.status
    suppressed = await db.scalar(
        select(SuppressionEntry.id).where(
            SuppressionEntry.workspace_id == workspace_id,
            SuppressionEntry.type == row.type,
            SuppressionEntry.value_normalized == row.value_normalized,
        )
    )
    target = ContactStatus(body.status)
    if (current == ContactStatus.OPTED_OUT or suppressed is not None) and target != ContactStatus.OPTED_OUT:
        raise ConflictError(
            "The contact opted out / is suppressed: remove it from the suppression list first (admin)",
            code="opted_out_final",
        )
    now = datetime.now(UTC)
    verified_at = now if target == ContactStatus.VERIFIED else None
    if row.workspace_id == workspace_id:  # manual: the row is the workspace's own
        row.status = target
        row.note = body.note or row.note
        row.verified_at = verified_at or row.verified_at
    elif state is None:
        db.add(
            ContactState(
                workspace_id=workspace_id,
                contact_id=contact_id,
                status=target,
                note=body.note,
                user_id=actor_id,
                verified_at=verified_at,
            )
        )
    else:
        state.status = target
        state.note = body.note or state.note
        state.user_id = actor_id
        state.verified_at = verified_at or state.verified_at
    if target == ContactStatus.OPTED_OUT:
        await _suppress(
            db, workspace_id, actor_id, row.type, row.value_normalized, SuppressionReason.OPT_OUT, body.note
        )
    await db.flush()
    await audit(
        db,
        "contact.status_changed",
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        entity_type="contact",
        entity_id=contact_id,
        diff={"from": current.value, "to": target.value, "note": body.note},
    )
    channels = (
        await _channels_with_value(db, row.type, row.value_normalized)
        if target == ContactStatus.OPTED_OUT
        else [row.channel_id]
    )
    rescored = await _rescore(db, workspace_id, channels)
    return ContactChange(contacts=await channel_contacts(db, workspace_id, row.channel_id), rescored=rescored)


async def delete_manual_contact(
    db: AsyncSession, workspace_id: int, actor_id: int, contact_id: int
) -> ContactChange:
    row = await _visible_contact_row(db, workspace_id, contact_id)
    if row.workspace_id != workspace_id:
        raise ConflictError(
            "Public contacts cannot be deleted: mark them invalid instead", code="contact_not_manual"
        )
    channel_id = row.channel_id
    await db.execute(delete(Contact).where(Contact.id == contact_id))
    await audit(
        db,
        "contact.deleted",
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        entity_type="contact",
        entity_id=contact_id,
        diff={"channel_id": channel_id, "type": row.type.value, "value": row.value_normalized},
    )
    rescored = await _rescore(db, workspace_id, [channel_id])
    return ContactChange(contacts=await channel_contacts(db, workspace_id, channel_id), rescored=rescored)


# ---------------------------------------------------------------------------
# Suppression list
# ---------------------------------------------------------------------------
async def list_suppression(
    db: AsyncSession, workspace_id: int, *, limit: int, offset: int
) -> SuppressionList:
    base = select(SuppressionEntry).where(SuppressionEntry.workspace_id == workspace_id)
    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = await db.scalars(
        base.order_by(SuppressionEntry.created_at.desc(), SuppressionEntry.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return SuppressionList(
        items=[SuppressionOut.model_validate(r) for r in rows], total=int(total), limit=limit, offset=offset
    )


async def add_suppression(
    db: AsyncSession, workspace_id: int, actor_id: int, body: SuppressionIn
) -> SuppressionOut:
    _, normalized = _normalized(body.type, body.value)
    await _suppress(
        db, workspace_id, actor_id, body.type, normalized, SuppressionReason(body.reason), body.note
    )
    row = await db.scalar(
        select(SuppressionEntry).where(
            SuppressionEntry.workspace_id == workspace_id,
            SuppressionEntry.type == body.type,
            SuppressionEntry.value_normalized == normalized,
        )
    )
    assert row is not None
    await audit(
        db,
        "suppression.added",
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        entity_type="suppression",
        entity_id=row.id,
        diff={"type": body.type.value, "value": normalized, "reason": body.reason},
    )
    await _rescore(db, workspace_id, await _channels_with_value(db, body.type, normalized))
    return SuppressionOut.model_validate(row)


async def remove_suppression(db: AsyncSession, workspace_id: int, actor_id: int, entry_id: int) -> None:
    """Admin only. A contact opted out by this workspace goes back to its own status."""
    row = await db.scalar(
        select(SuppressionEntry).where(
            SuppressionEntry.id == entry_id, SuppressionEntry.workspace_id == workspace_id
        )
    )
    if row is None:
        raise NotFoundError("Suppression entry not found")
    await db.execute(delete(SuppressionEntry).where(SuppressionEntry.id == entry_id))
    # the opted_out verdicts of this value end with the entry (the contacts become «unverified»)
    contact_ids = select(Contact.id).where(
        Contact.type == row.type, Contact.value_normalized == row.value_normalized
    )
    for st in await db.scalars(
        select(ContactState).where(
            ContactState.workspace_id == workspace_id,
            ContactState.contact_id.in_(contact_ids),
            ContactState.status == ContactStatus.OPTED_OUT,
        )
    ):
        st.status = ContactStatus.UNVERIFIED
    for c in await db.scalars(
        select(Contact).where(
            Contact.workspace_id == workspace_id,
            Contact.type == row.type,
            Contact.value_normalized == row.value_normalized,
            Contact.status == ContactStatus.OPTED_OUT,
        )
    ):
        c.status = ContactStatus.UNVERIFIED
    await audit(
        db,
        "suppression.removed",
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        entity_type="suppression",
        entity_id=entry_id,
        diff={"type": row.type.value, "value": row.value_normalized, "reason": row.reason.value},
    )
    await db.flush()
    await _rescore(db, workspace_id, await _channels_with_value(db, row.type, row.value_normalized))


# ---------------------------------------------------------------------------
# Projects: start a scan job, statistics
# ---------------------------------------------------------------------------
async def start_project_scan(
    db: AsyncSession, workspace_id: int, actor_id: int, project_id: int, body: ContactScanStart
) -> ContactScanStartResult:
    await get_project(db, workspace_id, project_id)
    current_scan = exists(
        select(ChannelContactScan.channel_id).where(
            ChannelContactScan.channel_id == ProjectChannel.channel_id,
            ChannelContactScan.extractor_version == EXTRACTOR_VERSION,
        )
    )
    stmt = select(ProjectChannel.channel_id).where(ProjectChannel.project_id == project_id)
    if not body.rescan:
        stmt = stmt.where(~current_scan)
    ids = list(await db.scalars(stmt.order_by(ProjectChannel.channel_id)))
    chosen, remaining = ids[:MAX_SCAN_CHANNELS], max(0, len(ids) - MAX_SCAN_CHANNELS)
    if not chosen:
        return ContactScanStartResult(job=None, created=False, channels=0, remaining=0)
    enq = await enqueue_job(
        db,
        type_=JobType.CONTACT_EXTRACTION,
        queue=JobQueue.BULK,
        params={"project_id": project_id, "channel_ids": chosen, "rescan": body.rescan},
        workspace_id=workspace_id,
        created_by=actor_id,
        progress_total=len(chosen),
    )
    if enq.created:
        await audit(
            db,
            "job.enqueued",
            workspace_id=workspace_id,
            actor_user_id=actor_id,
            entity_type="job_run",
            entity_id=enq.job.id,
            diff={
                "type": enq.job.type,
                "project_id": project_id,
                "channels": len(chosen),
                "rescan": body.rescan,
            },
        )
    return ContactScanStartResult(
        job=JobRunOut.model_validate(enq.job), created=enq.created, channels=len(chosen), remaining=remaining
    )


async def project_contact_stats(db: AsyncSession, workspace_id: int, project_id: int) -> ContactStats:
    await get_project(db, workspace_id, project_id)
    ids = list(
        await db.scalars(select(ProjectChannel.channel_id).where(ProjectChannel.project_id == project_id))
    )
    scanned, types = await contact_inputs(db, workspace_id, ids)
    by_type: dict[str, int] = {t.value: 0 for t in ContactType}
    for ts in types.values():
        for t in ts:
            by_type[t] += 1
    with_contacts = sum(1 for cid in ids if types.get(cid))
    return ContactStats(
        channels=len(ids),
        scanned=len(scanned),
        with_contacts=with_contacts,
        not_found=sum(1 for cid in scanned if not types.get(cid)),
        by_type=by_type,
    )
