from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, ForeignKey, Index, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from app.core.db import Base, IdMixin
from app.core.types import pg_enum


class FavoriteEntity(StrEnum):
    CHANNEL = "channel"  # entity_id = channels.id
    THUMBNAIL = "thumbnail"  # the thumbnail of a video: entity_id = videos.id (one thumbnail per video)
    # generated concepts arrive with thumbnail generation (Phase 5)


class Favorite(IdMixin, Base):
    """Per user and workspace; ``entity_id`` is not a foreign key (several entity types), so reads join on
    the visible entities and stale rows are simply not shown."""

    __tablename__ = "favorites"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", "entity_type", "entity_id"),
        Index("ix_favorites_entity", "entity_type", "entity_id"),
    )
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    entity_type: Mapped[FavoriteEntity] = mapped_column(
        pg_enum(FavoriteEntity, "favorite_entity"), nullable=False
    )
    entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
