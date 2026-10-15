"""Tags of the workspace (ТЗ §36, Phase 4). Assignment to leads — ``POST /api/leads/bulk``."""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.api.deps import DbSession, ReadAuth, WriteAuth
from app.domains.leads import tags
from app.domains.leads.schemas import TagIn, TagOut, TagPatch

router = APIRouter(prefix="/tags", tags=["leads"])


@router.get("", response_model=list[TagOut])
async def list_tags(ctx: ReadAuth, db: DbSession) -> list[TagOut]:
    """System tags first (with the rule that assigns them), then user tags by name; ``leads`` = leads of
    the workspace with the tag."""
    return await tags.list_tags(db, ctx.workspace.id)


@router.post("", response_model=TagOut, status_code=status.HTTP_201_CREATED)
async def create_tag(body: TagIn, ctx: WriteAuth, db: DbSession) -> TagOut:
    """Names are unique per workspace ignoring case and repeated spaces (409 ``tag_exists``); system tag
    names are reserved (409 ``tag_reserved``)."""
    return await tags.create_tag(db, ctx.workspace.id, ctx.user.id, body)


@router.patch("/{tag_id}", response_model=TagOut)
async def update_tag(tag_id: int, body: TagPatch, ctx: WriteAuth, db: DbSession) -> TagOut:
    """Rename / recolor. System tags: color only (409 ``system_tag`` on rename)."""
    return await tags.update_tag(db, ctx.workspace.id, ctx.user.id, tag_id, body)


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(tag_id: int, ctx: WriteAuth, db: DbSession) -> Response:
    """Removes the tag from all leads. System tags cannot be deleted (409 ``system_tag``)."""
    await tags.delete_tag(db, ctx.workspace.id, ctx.user.id, tag_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
