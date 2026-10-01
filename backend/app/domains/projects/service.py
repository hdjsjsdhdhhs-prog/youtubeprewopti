"""Search projects and queries. Every function is scoped by ``workspace_id``."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.domains.identity.service import audit
from app.domains.projects.models import (
    ProjectChannel,
    ProjectStatus,
    QueryStatus,
    SearchProject,
    SearchQuery,
    SearchType,
    TaxonomyNode,
)
from app.domains.projects.schemas import (
    MAX_QUERY_LENGTH,
    ProjectCreate,
    ProjectOut,
    ProjectUpdate,
    QueryBulkResult,
    QueryOut,
    QueryUpdate,
    RejectedLine,
)

_WS = re.compile(r"\s+")


def normalize_query(text: str) -> str:
    """Canonical form used for deduplication: NFKC, collapsed whitespace, casefolded."""
    return _WS.sub(" ", unicodedata.normalize("NFKC", text)).strip().casefold()


def clean_query(text: str) -> str:
    """Display form: NFKC + collapsed whitespace (case preserved)."""
    return _WS.sub(" ", unicodedata.normalize("NFKC", text)).strip()


def _with_counts(stmt: Select) -> Select:
    queries = (
        select(func.count())
        .select_from(SearchQuery)
        .where(SearchQuery.project_id == SearchProject.id)
        .correlate(SearchProject)
        .scalar_subquery()
    )
    channels = (
        select(func.count())
        .select_from(ProjectChannel)
        .where(ProjectChannel.project_id == SearchProject.id)
        .correlate(SearchProject)
        .scalar_subquery()
    )
    return stmt.add_columns(queries.label("queries_count"), channels.label("channels_count"))


def _to_out(row: Any) -> ProjectOut:
    project, queries_count, channels_count = row
    out = ProjectOut.model_validate(project)
    out.queries_count, out.channels_count = queries_count, channels_count
    return out


async def get_project(db: AsyncSession, workspace_id: int, project_id: int) -> SearchProject:
    project = await db.scalar(
        select(SearchProject).where(
            SearchProject.id == project_id, SearchProject.workspace_id == workspace_id
        )
    )
    if project is None:
        raise NotFoundError("Project not found")
    return project


async def get_project_out(db: AsyncSession, workspace_id: int, project_id: int) -> ProjectOut:
    await get_project(db, workspace_id, project_id)
    row = (await db.execute(_with_counts(select(SearchProject).where(SearchProject.id == project_id)))).one()
    return _to_out(row)


async def list_projects(
    db: AsyncSession, workspace_id: int, *, status: ProjectStatus | None, limit: int, offset: int
) -> tuple[list[ProjectOut], int]:
    where = [SearchProject.workspace_id == workspace_id]
    if status is not None:
        where.append(SearchProject.status == status)
    total = await db.scalar(select(func.count()).select_from(SearchProject).where(*where)) or 0
    rows = await db.execute(
        _with_counts(select(SearchProject).where(*where))
        .order_by(SearchProject.created_at.desc(), SearchProject.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return [_to_out(r) for r in rows], total


async def _ensure_name_free(
    db: AsyncSession, workspace_id: int, name: str, exclude_id: int | None = None
) -> None:
    stmt = select(SearchProject.id).where(
        SearchProject.workspace_id == workspace_id, SearchProject.name == name
    )
    if exclude_id is not None:
        stmt = stmt.where(SearchProject.id != exclude_id)
    if await db.scalar(stmt) is not None:
        raise ConflictError(f"A project named '{name}' already exists", code="project_name_taken")


async def create_project(
    db: AsyncSession, workspace_id: int, actor_id: int, data: ProjectCreate
) -> ProjectOut:
    await _ensure_name_free(db, workspace_id, data.name)
    values = data.model_dump(exclude_none=True)
    project = SearchProject(workspace_id=workspace_id, **values)
    db.add(project)
    await db.flush()
    await audit(
        db,
        "project.created",
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        entity_type="search_project",
        entity_id=project.id,
        diff={"after": data.model_dump(mode="json")},
    )
    return await get_project_out(db, workspace_id, project.id)


async def update_project(
    db: AsyncSession, workspace_id: int, actor_id: int, project_id: int, data: ProjectUpdate
) -> ProjectOut:
    project = await get_project(db, workspace_id, project_id)
    changes = data.model_dump(exclude_unset=True)
    if changes.get("name") is None:
        changes.pop("name", None)
    if changes.get("status") is None:
        changes.pop("status", None)
    if "name" in changes:
        await _ensure_name_free(db, workspace_id, changes["name"], exclude_id=project.id)
    if changes.get("description", "") is None:
        changes["description"] = ""
    if changes.get("filter_settings", {}) is None:
        changes["filter_settings"] = {}
    for field in ("results_per_query", "search_depth", "videos_to_analyze"):
        if field in changes and changes[field] is None:
            changes.pop(field)  # NOT NULL columns: null means "no change"

    before = {k: getattr(project, k) for k in changes}
    for key, value in changes.items():
        setattr(project, key, value)
    await db.flush()
    if changes:
        await audit(
            db,
            "project.updated",
            workspace_id=workspace_id,
            actor_user_id=actor_id,
            entity_type="search_project",
            entity_id=project.id,
            diff={"before": _jsonable(before), "after": _jsonable(changes)},
        )
    return await get_project_out(db, workspace_id, project.id)


async def delete_project(db: AsyncSession, workspace_id: int, actor_id: int, project_id: int) -> None:
    project = await get_project(db, workspace_id, project_id)
    await audit(
        db,
        "project.deleted",
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        entity_type="search_project",
        entity_id=project.id,
        diff={"name": project.name},
    )
    await db.delete(project)
    await db.flush()


def _jsonable(d: dict[str, Any]) -> dict[str, Any]:
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in d.items()}


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
async def list_queries(
    db: AsyncSession,
    workspace_id: int,
    project_id: int,
    *,
    status: QueryStatus | None,
    limit: int,
    offset: int,
) -> tuple[list[QueryOut], int]:
    await get_project(db, workspace_id, project_id)
    where = [SearchQuery.project_id == project_id]
    if status is not None:
        where.append(SearchQuery.status == status)
    total = await db.scalar(select(func.count()).select_from(SearchQuery).where(*where)) or 0
    rows = await db.scalars(
        select(SearchQuery).where(*where).order_by(SearchQuery.id).limit(limit).offset(offset)
    )
    return [QueryOut.model_validate(q) for q in rows], total


async def _ensure_node(db: AsyncSession, node_id: int | None) -> None:
    if node_id is not None and await db.get(TaxonomyNode, node_id) is None:
        raise NotFoundError("Niche/topic not found")


async def bulk_import_queries(
    db: AsyncSession,
    workspace_id: int,
    actor_id: int,
    project_id: int,
    lines: list[str],
    *,
    taxonomy_node_id: int | None = None,
    search_type: SearchType = SearchType.VIDEO,
) -> QueryBulkResult:
    """Idempotent import: duplicates (within the batch or already in the project) are skipped."""
    await get_project(db, workspace_id, project_id)
    await _ensure_node(db, taxonomy_node_id)
    rejected: list[RejectedLine] = []
    batch: dict[str, str] = {}  # normalized -> display
    duplicates = 0
    for idx, raw in enumerate(lines, start=1):
        text = clean_query(raw)
        if not text:
            continue  # blank lines are ignored silently
        if len(text) > MAX_QUERY_LENGTH:
            rejected.append(RejectedLine(line=idx, value=text[:80], reason=f"longer than {MAX_QUERY_LENGTH}"))
            continue
        norm = normalize_query(text)
        if norm in batch:
            duplicates += 1
            continue
        batch[norm] = text

    created: list[SearchQuery] = []
    if batch:
        result = await db.scalars(
            insert(SearchQuery)
            .values([
                {"project_id": project_id, "text": t, "text_normalized": n,
                 "taxonomy_node_id": taxonomy_node_id, "search_type": search_type}
                for n, t in batch.items()
            ])
            .on_conflict_do_nothing(index_elements=["project_id", "text_normalized"])
            .returning(SearchQuery)
        )
        created = sorted(result.all(), key=lambda q: q.id)
        duplicates += len(batch) - len(created)

    if created:
        await audit(
            db,
            "queries.imported",
            workspace_id=workspace_id,
            actor_user_id=actor_id,
            entity_type="search_project",
            entity_id=project_id,
            diff={"created": len(created), "duplicates": duplicates, "rejected": len(rejected)},
        )
    return QueryBulkResult(
        created=len(created),
        duplicates=duplicates,
        rejected=rejected,
        items=[QueryOut.model_validate(q) for q in created],
    )


async def update_query(
    db: AsyncSession, workspace_id: int, actor_id: int, project_id: int, query_id: int, data: QueryUpdate
) -> QueryOut:
    await get_project(db, workspace_id, project_id)
    query = await db.scalar(
        select(SearchQuery).where(SearchQuery.id == query_id, SearchQuery.project_id == project_id)
    )
    if query is None:
        raise NotFoundError("Query not found")
    changes = data.model_dump(exclude_unset=True)
    if changes.get("search_type", "") is None:
        changes.pop("search_type")
    if "taxonomy_node_id" in changes:
        await _ensure_node(db, changes["taxonomy_node_id"])
    before = {k: getattr(query, k) for k in changes}
    for key, value in changes.items():
        setattr(query, key, value)
    await db.flush()
    if changes:
        await audit(
            db, "query.updated", workspace_id=workspace_id, actor_user_id=actor_id,
            entity_type="search_query", entity_id=query.id, diff={"before": before, "after": changes},
        )
    return QueryOut.model_validate(query)


async def delete_query(
    db: AsyncSession, workspace_id: int, actor_id: int, project_id: int, query_id: int
) -> None:
    await get_project(db, workspace_id, project_id)
    query = await db.scalar(
        select(SearchQuery).where(SearchQuery.id == query_id, SearchQuery.project_id == project_id)
    )
    if query is None:
        raise NotFoundError("Query not found")
    if query.status == QueryStatus.RUNNING:
        raise ConflictError("Query is running and cannot be deleted", code="query_running")
    await audit(
        db,
        "query.deleted",
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        entity_type="search_query",
        entity_id=query.id,
        diff={"text": query.text},
    )
    await db.delete(query)
    await db.flush()
