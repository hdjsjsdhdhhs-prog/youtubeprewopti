"""Personal favorites (ТЗ §37, Phase 4): channels and thumbnails (videos)."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import DbSession, Paging, ReadAuth
from app.domains.favorites import service
from app.domains.favorites.models import FavoriteEntity
from app.domains.favorites.schemas import FavoriteList, FavoriteState

router = APIRouter(prefix="/favorites", tags=["favorites"])


@router.get("", response_model=FavoriteList)
async def list_favorites(
    ctx: ReadAuth, db: DbSession, page: Paging, entity_type: FavoriteEntity | None = None
) -> FavoriteList:
    """Favorites of the current user, newest first. Entities no longer visible to the workspace (project
    deleted) are not listed."""
    return await service.list_favorites(
        db, ctx.workspace.id, ctx.user.id, entity_type, limit=page.limit, offset=page.offset
    )


# Favorites are personal, so any member (viewer included) may keep them.
@router.put("/{entity_type}/{entity_id}", response_model=FavoriteState)
async def add_favorite(
    entity_type: FavoriteEntity, entity_id: int, ctx: ReadAuth, db: DbSession
) -> FavoriteState:
    """Idempotent. ``thumbnail`` — ``entity_id`` is the video id (one thumbnail per video)."""
    return await service.add_favorite(db, ctx.workspace.id, ctx.user.id, entity_type, entity_id)


@router.delete("/{entity_type}/{entity_id}", response_model=FavoriteState)
async def remove_favorite(
    entity_type: FavoriteEntity, entity_id: int, ctx: ReadAuth, db: DbSession
) -> FavoriteState:
    """Idempotent."""
    return await service.remove_favorite(db, ctx.workspace.id, ctx.user.id, entity_type, entity_id)
