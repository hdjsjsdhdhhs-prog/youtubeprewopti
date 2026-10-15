"""Tags (ТЗ §36, Phase 4).

* **User tags** — created and assigned by operators (one by one or in bulk).
* **System tags** — fixed set ``SYSTEM_TAGS``, assigned by deterministic rules (no AI) on every rescore
  (``service.sync_leads``) and removed when the rule no longer holds. Their names are reserved; operators
  can recolor them but not rename, delete or assign them by hand.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import datetime
from decimal import Decimal

from sqlalchemy import and_, delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.domains.identity.service import audit
from app.domains.leads import scoring
from app.domains.leads.models import Lead, LeadTag, Tag, TagSource
from app.domains.leads.schemas import LeadTagOut, TagIn, TagOut, TagPatch

WEAK_THUMBNAIL_SCORE = Decimal(5)
HIGH_POTENTIAL_SCORE = Decimal(60)

# system_key -> (name, rule shown to the user)
SYSTEM_TAGS: dict[str, tuple[str, str]] = {
    "weak_thumbnails": (
        "weak-thumbnails",
        f"средний балл превью канала < {WEAK_THUMBNAIL_SCORE} (ручные оценки учитываются)",
    ),
    "high_potential": ("high-potential", f"Lead Score ≥ {HIGH_POTENTIAL_SCORE} (ручной, если задан)"),
    "hot_lead": ("hot-lead", f"Lead Score ≥ {HIGH_POTENTIAL_SCORE} и высокая активность канала"),
    "visual_opportunity": ("visual-opportunity", "аудит канала: высокая визуальная возможность"),
}


def name_key(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().casefold()


RESERVED_NAMES = frozenset(name_key(name) for name, _ in SYSTEM_TAGS.values())


def system_tag_keys(inputs: scoring.LeadInputs, total: Decimal, now: datetime) -> set[str]:
    """Rules of the system tags. ``total`` is the effective Lead Score (human value if set)."""
    keys: set[str] = set()
    if inputs.avg_thumbnail_score is not None and inputs.avg_thumbnail_score < WEAK_THUMBNAIL_SCORE:
        keys.add("weak_thumbnails")
    if total >= HIGH_POTENTIAL_SCORE:
        keys.add("high_potential")
        if scoring.activity_level(inputs.last_video_at, inputs.videos_30d, now) == scoring.ActivityLevel.HIGH:
            keys.add("hot_lead")
    if inputs.visual_opportunity == "high":
        keys.add("visual_opportunity")
    return keys


async def ensure_system_tags(db: AsyncSession, workspace_id: int) -> dict[str, int]:
    """system_key -> tag id, creating the missing system tags of the workspace."""
    await db.execute(
        insert(Tag)
        .values(
            [
                {"workspace_id": workspace_id, "name": name, "name_key": name_key(name), "system_key": key}
                for key, (name, _) in SYSTEM_TAGS.items()
            ]
        )
        .on_conflict_do_nothing()
    )
    rows = await db.execute(
        select(Tag.system_key, Tag.id).where(Tag.workspace_id == workspace_id, Tag.system_key.is_not(None))
    )
    return {key: tid for key, tid in rows.tuples() if key is not None}


async def apply_system_tags(db: AsyncSession, workspace_id: int, desired: dict[int, set[str]]) -> int:
    """Make the system tags of the given leads exactly ``desired``; returns added + removed."""
    if not desired:
        return 0
    ids = await ensure_system_tags(db, workspace_id)
    by_id = {tid: key for key, tid in ids.items()}
    current: dict[int, set[str]] = {}
    for lead_id, tag_id in (
        await db.execute(
            select(LeadTag.lead_id, LeadTag.tag_id).where(
                LeadTag.lead_id.in_(list(desired)), LeadTag.source == TagSource.SYSTEM
            )
        )
    ).tuples():
        if tag_id in by_id:
            current.setdefault(lead_id, set()).add(by_id[tag_id])
    add: list[dict[str, object]] = []
    remove: list[tuple[int, int]] = []
    for lead_id, keys in desired.items():
        have = current.get(lead_id, set())
        add.extend(
            {"lead_id": lead_id, "tag_id": ids[k], "source": TagSource.SYSTEM}
            for k in keys - have
            if k in ids
        )
        remove.extend((lead_id, ids[k]) for k in have - keys)
    if add:
        # a user tag can never be a system one (reserved names), so a conflict means «already there»
        await db.execute(insert(LeadTag).values(add).on_conflict_do_nothing())
    if remove:
        await db.execute(
            delete(LeadTag).where(
                tuple_(LeadTag.lead_id, LeadTag.tag_id).in_(remove), LeadTag.source == TagSource.SYSTEM
            )
        )
    return len(add) + len(remove)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def _rule(tag: Tag) -> str | None:
    return SYSTEM_TAGS[tag.system_key][1] if tag.system_key in SYSTEM_TAGS else None


def _tag_out(tag: Tag, leads: int) -> TagOut:
    return TagOut(
        id=tag.id, name=tag.name, color=tag.color, is_system=tag.system_key is not None,
        system_key=tag.system_key, rule=_rule(tag), leads=leads, created_at=tag.created_at,
    )


async def list_tags(db: AsyncSession, workspace_id: int) -> list[TagOut]:
    await ensure_system_tags(db, workspace_id)
    counts = (
        select(LeadTag.tag_id, func.count().label("n"))
        .join(Lead, Lead.id == LeadTag.lead_id)
        .where(Lead.workspace_id == workspace_id)
        .group_by(LeadTag.tag_id)
        .subquery()
    )
    rows = await db.execute(
        select(Tag, func.coalesce(counts.c.n, 0))
        .outerjoin(counts, counts.c.tag_id == Tag.id)
        .where(Tag.workspace_id == workspace_id)
        .order_by(Tag.system_key.is_(None), Tag.name_key)
    )
    return [_tag_out(tag, int(n)) for tag, n in rows.tuples()]


async def tags_for(db: AsyncSession, lead_ids: Sequence[int]) -> dict[int, list[LeadTagOut]]:
    out: dict[int, list[LeadTagOut]] = {}
    if not lead_ids:
        return out
    rows = await db.execute(
        select(LeadTag.lead_id, LeadTag.source, Tag)
        .join(Tag, Tag.id == LeadTag.tag_id)
        .where(LeadTag.lead_id.in_(list(lead_ids)))
        .order_by(LeadTag.lead_id, Tag.system_key.is_(None), Tag.name_key)
    )
    for lead_id, source, tag in rows.tuples():
        out.setdefault(lead_id, []).append(
            LeadTagOut(
                id=tag.id, name=tag.name, color=tag.color, is_system=tag.system_key is not None, source=source
            )
        )
    return out


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
async def _get(db: AsyncSession, workspace_id: int, tag_id: int) -> Tag:
    tag = await db.scalar(select(Tag).where(Tag.id == tag_id, Tag.workspace_id == workspace_id))
    if tag is None:
        raise NotFoundError("Tag not found")
    return tag


async def get_user_tag(db: AsyncSession, workspace_id: int, tag_id: int) -> Tag:
    tag = await _get(db, workspace_id, tag_id)
    if tag.system_key is not None:
        raise ConflictError(
            "System tags are assigned by their rules only", code="system_tag", details={"rule": _rule(tag)}
        )
    return tag


async def _check_name(db: AsyncSession, workspace_id: int, name: str, except_id: int | None = None) -> str:
    key = name_key(name)
    if key in RESERVED_NAMES:
        raise ConflictError("This name is reserved for a system tag", code="tag_reserved")
    conds = [Tag.workspace_id == workspace_id, Tag.name_key == key]
    if except_id is not None:
        conds.append(Tag.id != except_id)
    if await db.scalar(select(Tag.id).where(*conds)) is not None:
        raise ConflictError("A tag with this name already exists", code="tag_exists")
    return key


async def _count(db: AsyncSession, workspace_id: int, tag_id: int) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(LeadTag)
            .join(Lead, Lead.id == LeadTag.lead_id)
            .where(LeadTag.tag_id == tag_id, Lead.workspace_id == workspace_id)
        )
        or 0
    )


async def create_tag(db: AsyncSession, workspace_id: int, actor_id: int, body: TagIn) -> TagOut:
    name = re.sub(r"\s+", " ", body.name)
    key = await _check_name(db, workspace_id, name)
    tag = Tag(workspace_id=workspace_id, name=name, name_key=key, color=body.color, created_by=actor_id)
    db.add(tag)
    await db.flush()
    await db.refresh(tag)
    await audit(
        db, "tag.created", workspace_id=workspace_id, actor_user_id=actor_id, entity_type="tag",
        entity_id=tag.id, diff={"name": name, "color": body.color},
    )
    return _tag_out(tag, 0)


async def update_tag(
    db: AsyncSession, workspace_id: int, actor_id: int, tag_id: int, body: TagPatch
) -> TagOut:
    tag = await _get(db, workspace_id, tag_id)
    before = {"name": tag.name, "color": tag.color}
    if "name" in body.model_fields_set and body.name is not None:
        if tag.system_key is not None:
            raise ConflictError("System tags cannot be renamed", code="system_tag")
        name = re.sub(r"\s+", " ", body.name)
        tag.name_key = await _check_name(db, workspace_id, name, tag.id)
        tag.name = name
    if "color" in body.model_fields_set:
        tag.color = body.color
    await db.flush()
    await db.refresh(tag)
    await audit(
        db, "tag.updated", workspace_id=workspace_id, actor_user_id=actor_id, entity_type="tag",
        entity_id=tag.id, diff={"before": before, "after": {"name": tag.name, "color": tag.color}},
    )
    return _tag_out(tag, await _count(db, workspace_id, tag.id))


async def delete_tag(db: AsyncSession, workspace_id: int, actor_id: int, tag_id: int) -> None:
    tag = await get_user_tag(db, workspace_id, tag_id)
    leads = await _count(db, workspace_id, tag.id)
    await db.execute(delete(Tag).where(Tag.id == tag.id))
    await audit(
        db, "tag.deleted", workspace_id=workspace_id, actor_user_id=actor_id, entity_type="tag",
        entity_id=tag_id, diff={"name": tag.name, "leads": leads},
    )


# ---------------------------------------------------------------------------
# Assignment (user tags)
# ---------------------------------------------------------------------------
async def add_tag(db: AsyncSession, tag: Tag, lead_ids: Iterable[int], actor_id: int) -> set[int]:
    """Lead ids that got the tag now (the others already had it)."""
    ids = list(dict.fromkeys(lead_ids))
    if not ids:
        return set()
    added = await db.scalars(
        insert(LeadTag)
        .values(
            [{"lead_id": i, "tag_id": tag.id, "source": TagSource.USER, "created_by": actor_id} for i in ids]
        )
        .on_conflict_do_nothing()
        .returning(LeadTag.lead_id)
    )
    return set(added)


async def remove_tag(db: AsyncSession, tag: Tag, lead_ids: Iterable[int]) -> set[int]:
    ids = list(dict.fromkeys(lead_ids))
    if not ids:
        return set()
    removed = await db.scalars(
        delete(LeadTag)
        .where(and_(LeadTag.tag_id == tag.id, LeadTag.lead_id.in_(ids), LeadTag.source == TagSource.USER))
        .returning(LeadTag.lead_id)
    )
    return set(removed)
