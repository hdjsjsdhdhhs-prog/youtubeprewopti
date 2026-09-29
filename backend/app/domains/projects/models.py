from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from app.core.db import Base, IdMixin, TimestampMixin
from app.core.types import pg_enum


class TaxonomyLevel(StrEnum):
    NICHE = "niche"
    TOPIC = "topic"
    SUBTOPIC = "subtopic"


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class QueryStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class DiscoveryMethod(StrEnum):
    KEYWORD_SEARCH = "keyword_search"
    CHANNEL_SEARCH = "channel_search"
    RELATED_VIDEO = "related_video"
    MANUAL_IMPORT = "manual_import"
    MONITORING = "monitoring"
    DEMO = "demo"


class TaxonomyNode(IdMixin, TimestampMixin, Base):
    """Niche > Topic > Subtopic hierarchy (§54). Global across workspaces."""

    __tablename__ = "taxonomy_nodes"
    __table_args__ = (
        UniqueConstraint("parent_id", "slug", postgresql_nulls_not_distinct=True),
    )
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("taxonomy_nodes.id", ondelete="CASCADE"))
    level: Mapped[TaxonomyLevel] = mapped_column(pg_enum(TaxonomyLevel, "taxonomy_level"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(200), nullable=False)


class SearchProject(IdMixin, TimestampMixin, Base):
    __tablename__ = "search_projects"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name"),
        CheckConstraint("results_per_query BETWEEN 1 AND 500", name="results_per_query_range"),
        CheckConstraint("search_depth BETWEEN 1 AND 10", name="search_depth_range"),
        CheckConstraint("videos_to_analyze BETWEEN 1 AND 50", name="videos_to_analyze_range"),
    )
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    language: Mapped[str | None] = mapped_column(String(10))
    region_code: Mapped[str | None] = mapped_column(String(2))
    results_per_query: Mapped[int] = mapped_column(Integer, nullable=False, default=50, server_default="50")
    search_depth: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    published_after: Mapped[date | None] = mapped_column(Date)
    videos_to_analyze: Mapped[int] = mapped_column(Integer, nullable=False, default=12, server_default="12")
    filter_settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    status: Mapped[ProjectStatus] = mapped_column(
        pg_enum(ProjectStatus, "project_status"), nullable=False, default=ProjectStatus.ACTIVE,
        server_default=ProjectStatus.ACTIVE.value,
    )
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")


class ProjectNiche(Base):
    __tablename__ = "project_niches"
    project_id: Mapped[int] = mapped_column(
        ForeignKey("search_projects.id", ondelete="CASCADE"), primary_key=True
    )
    taxonomy_node_id: Mapped[int] = mapped_column(
        ForeignKey("taxonomy_nodes.id", ondelete="CASCADE"), primary_key=True
    )


class SearchQuery(IdMixin, TimestampMixin, Base):
    __tablename__ = "search_queries"
    __table_args__ = (UniqueConstraint("project_id", "text_normalized"),)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("search_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    text: Mapped[str] = mapped_column(String(300), nullable=False)
    text_normalized: Mapped[str] = mapped_column(String(300), nullable=False)
    taxonomy_node_id: Mapped[int | None] = mapped_column(ForeignKey("taxonomy_nodes.id", ondelete="SET NULL"))
    status: Mapped[QueryStatus] = mapped_column(
        pg_enum(QueryStatus, "query_status"), nullable=False, default=QueryStatus.PENDING,
        server_default=QueryStatus.PENDING.value,
    )
    last_run_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    results_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class ProjectChannel(Base):
    """Membership of a (global) channel in a project (§49)."""

    __tablename__ = "project_channels"
    project_id: Mapped[int] = mapped_column(
        ForeignKey("search_projects.id", ondelete="CASCADE"), primary_key=True
    )
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    first_discovered_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    last_discovered_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    discovery_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")


class ChannelDiscovery(IdMixin, Base):
    """Where/when/how a channel was found (§2, §50)."""

    __tablename__ = "channel_discoveries"
    __table_args__ = (
        Index(
            "uq_channel_discoveries_source",
            "project_id", "channel_id", "search_query_id", "source_video_id", "method",
            unique=True, postgresql_nulls_not_distinct=True,
        ),
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("search_projects.id", ondelete="CASCADE"), nullable=False
    )
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    search_query_id: Mapped[int | None] = mapped_column(ForeignKey("search_queries.id", ondelete="SET NULL"))
    source_video_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("videos.id", ondelete="SET NULL")
    )
    method: Mapped[DiscoveryMethod] = mapped_column(
        pg_enum(DiscoveryMethod, "discovery_method"), nullable=False
    )
    discovered_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
