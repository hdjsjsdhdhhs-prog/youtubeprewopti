from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import DbSession, ReadAuth, WriteAuth
from app.domains.projects import taxonomy
from app.domains.projects.schemas import TaxonomyBulkImport, TaxonomyBulkResult, TaxonomyNodeOut

router = APIRouter(prefix="/taxonomy", tags=["taxonomy"])


@router.get("", response_model=list[TaxonomyNodeOut])
async def list_taxonomy(ctx: ReadAuth, db: DbSession) -> list[TaxonomyNodeOut]:
    """All niche / topic / subtopic nodes (flat; build the tree from ``parent_id``)."""
    return await taxonomy.list_nodes(db)


@router.post("/bulk", response_model=TaxonomyBulkResult)
async def bulk_create(body: TaxonomyBulkImport, ctx: WriteAuth, db: DbSession) -> TaxonomyBulkResult:
    """Create nodes (one name per line) under ``parent_id``; existing names are returned, not duplicated."""
    return await taxonomy.bulk_create(
        db, ctx.workspace.id, ctx.user.id, body.parent_id, body.text.splitlines()
    )
