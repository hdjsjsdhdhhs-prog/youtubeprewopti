from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.api.deps import DbSession, Paging, ReadAuth, WriteAuth
from app.api.schemas import Page
from app.domains.projects import service, taxonomy
from app.domains.projects.models import ProjectStatus, QueryStatus
from app.domains.projects.schemas import (
    ProjectCreate,
    ProjectNichesUpdate,
    ProjectOut,
    ProjectUpdate,
    QueryBulkImport,
    QueryBulkResult,
    QueryOut,
    QueryUpdate,
    TaxonomyNodeOut,
)

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=Page[ProjectOut])
async def list_projects(
    ctx: ReadAuth,
    db: DbSession,
    page: Paging,
    status_: Annotated[ProjectStatus | None, Query(alias="status")] = None,
) -> Page[ProjectOut]:
    items, total = await service.list_projects(
        db, ctx.workspace.id, status=status_, limit=page.limit, offset=page.offset
    )
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(body: ProjectCreate, ctx: WriteAuth, db: DbSession) -> ProjectOut:
    return await service.create_project(db, ctx.workspace.id, ctx.user.id, body)


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: int, ctx: ReadAuth, db: DbSession) -> ProjectOut:
    return await service.get_project_out(db, ctx.workspace.id, project_id)


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(project_id: int, body: ProjectUpdate, ctx: WriteAuth, db: DbSession) -> ProjectOut:
    return await service.update_project(db, ctx.workspace.id, ctx.user.id, project_id, body)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: int, ctx: WriteAuth, db: DbSession) -> Response:
    await service.delete_project(db, ctx.workspace.id, ctx.user.id, project_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{project_id}/queries", response_model=Page[QueryOut])
async def list_queries(
    project_id: int,
    ctx: ReadAuth,
    db: DbSession,
    page: Paging,
    status_: Annotated[QueryStatus | None, Query(alias="status")] = None,
) -> Page[QueryOut]:
    items, total = await service.list_queries(
        db, ctx.workspace.id, project_id, status=status_, limit=page.limit, offset=page.offset
    )
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("/{project_id}/queries/bulk", response_model=QueryBulkResult)
async def bulk_import_queries(
    project_id: int, body: QueryBulkImport, ctx: WriteAuth, db: DbSession
) -> QueryBulkResult:
    return await service.bulk_import_queries(
        db, ctx.workspace.id, ctx.user.id, project_id, body.lines(),
        taxonomy_node_id=body.taxonomy_node_id, search_type=body.search_type,
    )


@router.patch("/{project_id}/queries/{query_id}", response_model=QueryOut)
async def update_query(
    project_id: int, query_id: int, body: QueryUpdate, ctx: WriteAuth, db: DbSession
) -> QueryOut:
    return await service.update_query(db, ctx.workspace.id, ctx.user.id, project_id, query_id, body)


@router.get("/{project_id}/niches", response_model=list[TaxonomyNodeOut])
async def list_project_niches(project_id: int, ctx: ReadAuth, db: DbSession) -> list[TaxonomyNodeOut]:
    return await taxonomy.list_project_niches(db, ctx.workspace.id, project_id)


@router.put("/{project_id}/niches", response_model=list[TaxonomyNodeOut])
async def set_project_niches(
    project_id: int, body: ProjectNichesUpdate, ctx: WriteAuth, db: DbSession
) -> list[TaxonomyNodeOut]:
    """Replace the project's niche list (§2)."""
    return await taxonomy.set_project_niches(
        db, ctx.workspace.id, ctx.user.id, project_id, body.taxonomy_node_ids
    )


@router.delete("/{project_id}/queries/{query_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_query(project_id: int, query_id: int, ctx: WriteAuth, db: DbSession) -> Response:
    await service.delete_query(db, ctx.workspace.id, ctx.user.id, project_id, query_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
