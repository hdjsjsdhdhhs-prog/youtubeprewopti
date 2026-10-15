"""Offer templates of the owner (Phase 7). A template is guidance the model follows, never code.

Editing adds a new version under the same ``key`` (offers keep the exact version they were written from);
deleting archives every version (old offers still show theirs).
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.domains.identity.service import audit
from app.domains.offers.models import OfferTemplate
from app.domains.offers.schemas import OfferTemplateIn, OfferTemplateOut


async def list_templates(db: AsyncSession, workspace_id: int) -> list[OfferTemplateOut]:
    """Latest version of every template that is not archived, by name."""
    latest = (
        select(OfferTemplate.key, func.max(OfferTemplate.version).label("version"))
        .where(OfferTemplate.workspace_id == workspace_id, OfferTemplate.is_archived.is_(False))
        .group_by(OfferTemplate.key)
        .subquery()
    )
    rows = await db.scalars(
        select(OfferTemplate)
        .join(latest, (latest.c.key == OfferTemplate.key) & (latest.c.version == OfferTemplate.version))
        .where(OfferTemplate.workspace_id == workspace_id)
        .order_by(OfferTemplate.name, OfferTemplate.id)
    )
    return [OfferTemplateOut.model_validate(t) for t in rows]


async def get_template(
    db: AsyncSession, workspace_id: int, template_id: int, *, active_only: bool = True
) -> OfferTemplate:
    conds = [OfferTemplate.id == template_id, OfferTemplate.workspace_id == workspace_id]
    if active_only:
        conds.append(OfferTemplate.is_archived.is_(False))
    row = await db.scalar(select(OfferTemplate).where(*conds))
    if row is None:
        raise NotFoundError("Offer template not found")
    return row


async def create_template(
    db: AsyncSession, workspace_id: int, actor_id: int, body: OfferTemplateIn
) -> OfferTemplateOut:
    row = OfferTemplate(
        workspace_id=workspace_id,
        key=str(uuid.uuid4()),
        version=1,
        name=body.name,
        body=body.body,
        created_by=actor_id,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    await audit(
        db,
        "offer_template.created",
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        entity_type="offer_template",
        entity_id=row.id,
        diff={"name": row.name, "key": row.key},
    )
    return OfferTemplateOut.model_validate(row)


async def update_template(
    db: AsyncSession, workspace_id: int, actor_id: int, template_id: int, body: OfferTemplateIn
) -> OfferTemplateOut:
    """A new version of the template ``template_id`` belongs to (any of its versions may be given)."""
    current = await get_template(db, workspace_id, template_id)
    latest = await db.scalar(
        select(func.max(OfferTemplate.version)).where(
            OfferTemplate.workspace_id == workspace_id, OfferTemplate.key == current.key
        )
    )
    row = OfferTemplate(
        workspace_id=workspace_id,
        key=current.key,
        version=int(latest or 0) + 1,
        name=body.name,
        body=body.body,
        created_by=actor_id,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    await audit(
        db,
        "offer_template.updated",
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        entity_type="offer_template",
        entity_id=row.id,
        diff={"key": row.key, "version": row.version, "name": row.name},
    )
    return OfferTemplateOut.model_validate(row)


async def archive_template(db: AsyncSession, workspace_id: int, actor_id: int, template_id: int) -> None:
    current = await get_template(db, workspace_id, template_id)
    await db.execute(
        update(OfferTemplate)
        .where(OfferTemplate.workspace_id == workspace_id, OfferTemplate.key == current.key)
        .values(is_archived=True)
    )
    await audit(
        db,
        "offer_template.archived",
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        entity_type="offer_template",
        entity_id=current.id,
        diff={"key": current.key, "name": current.name},
    )
