"""Niche > Topic > Subtopic taxonomy (§54) and project niches (§2).

Taxonomy nodes are global (shared vocabulary, like channels); what a workspace *uses* is scoped:
project niches and query tags live in workspace-owned projects.
"""

from __future__ import annotations

from sqlalchemy import delete, literal, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, NotFoundError
from app.domains.identity.service import audit
from app.domains.projects.models import ProjectNiche, TaxonomyLevel, TaxonomyNode
from app.domains.projects.schemas import RejectedLine, TaxonomyBulkResult, TaxonomyNodeOut
from app.domains.projects.service import clean_query, get_project, normalize_query

MAX_NODE_NAME = 200
_CHILD_LEVEL = {None: TaxonomyLevel.NICHE, TaxonomyLevel.NICHE: TaxonomyLevel.TOPIC,
                TaxonomyLevel.TOPIC: TaxonomyLevel.SUBTOPIC}


def slugify(name: str) -> str:
    return normalize_query(name).replace(" ", "-")[:MAX_NODE_NAME]


async def list_nodes(db: AsyncSession) -> list[TaxonomyNodeOut]:
    rows = await db.scalars(select(TaxonomyNode).order_by(TaxonomyNode.level, TaxonomyNode.name))
    return [TaxonomyNodeOut.model_validate(n) for n in rows]


async def get_node(db: AsyncSession, node_id: int) -> TaxonomyNode:
    node = await db.get(TaxonomyNode, node_id)
    if node is None:
        raise NotFoundError("Niche/topic not found")
    return node


async def subtree_ids(db: AsyncSession, node_ids: list[int]) -> list[int]:
    """The nodes and all their descendants (niche → its topics → subtopics)."""
    if not node_ids:
        return []
    tree = select(TaxonomyNode.id).where(TaxonomyNode.id.in_(node_ids)).cte("tree", recursive=True)
    tree = tree.union(select(TaxonomyNode.id).join(tree, TaxonomyNode.parent_id == tree.c.id))
    return list(await db.scalars(select(tree.c.id)))


async def bulk_create(
    db: AsyncSession, workspace_id: int, actor_id: int, parent_id: int | None, lines: list[str]
) -> TaxonomyBulkResult:
    """Create nodes under ``parent_id`` (None = top-level niches). Existing names are reported as duplicates
    and returned too, so the caller can use their ids right away."""
    parent_level = None
    if parent_id is not None:
        parent_level = (await get_node(db, parent_id)).level
    level = _CHILD_LEVEL.get(parent_level)
    if level is None:
        raise AppError("A subtopic cannot have children (levels: niche > topic > subtopic)", code="too_deep")

    rejected: list[RejectedLine] = []
    batch: dict[str, str] = {}
    duplicates = 0
    for idx, raw in enumerate(lines, start=1):
        name = clean_query(raw)
        if not name:
            continue
        if len(name) > MAX_NODE_NAME:
            rejected.append(RejectedLine(line=idx, value=name[:80], reason=f"longer than {MAX_NODE_NAME}"))
            continue
        slug = slugify(name)
        if slug in batch:
            duplicates += 1
            continue
        batch[slug] = name

    created_ids: set[int] = set()
    items: list[TaxonomyNode] = []
    if batch:
        rows = [{"parent_id": parent_id, "level": level, "name": n, "slug": s} for s, n in batch.items()]
        result = await db.scalars(
            insert(TaxonomyNode)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["parent_id", "slug"])
            .returning(TaxonomyNode.id)
        )
        created_ids = set(result.all())
        duplicates += len(batch) - len(created_ids)
        same_parent = (
            TaxonomyNode.parent_id.is_(None) if parent_id is None else TaxonomyNode.parent_id == parent_id
        )
        items = list(
            await db.scalars(
                select(TaxonomyNode)
                .where(same_parent, TaxonomyNode.slug.in_(list(batch)))
                .order_by(TaxonomyNode.name)
            )
        )
    if created_ids:
        await audit(
            db, "taxonomy.created", workspace_id=workspace_id, actor_user_id=actor_id,
            entity_type="taxonomy_node", entity_id=parent_id,
            diff={"created": len(created_ids), "level": level.value},
        )
    return TaxonomyBulkResult(
        created=len(created_ids), duplicates=duplicates, rejected=rejected,
        items=[TaxonomyNodeOut.model_validate(n) for n in items],
    )


async def list_project_niches(db: AsyncSession, workspace_id: int, project_id: int) -> list[TaxonomyNodeOut]:
    await get_project(db, workspace_id, project_id)
    rows = await db.scalars(
        select(TaxonomyNode)
        .join(ProjectNiche, ProjectNiche.taxonomy_node_id == TaxonomyNode.id)
        .where(ProjectNiche.project_id == project_id)
        .order_by(TaxonomyNode.name)
    )
    return [TaxonomyNodeOut.model_validate(n) for n in rows]


async def set_project_niches(
    db: AsyncSession, workspace_id: int, actor_id: int, project_id: int, node_ids: list[int]
) -> list[TaxonomyNodeOut]:
    await get_project(db, workspace_id, project_id)
    wanted = sorted(set(node_ids))
    if wanted:
        found = set(await db.scalars(select(TaxonomyNode.id).where(TaxonomyNode.id.in_(wanted))))
        missing = [n for n in wanted if n not in found]
        if missing:
            raise NotFoundError("Niche/topic not found", details={"missing": missing})
    await db.execute(delete(ProjectNiche).where(ProjectNiche.project_id == project_id))
    if wanted:
        await db.execute(
            insert(ProjectNiche).from_select(
                ["project_id", "taxonomy_node_id"],
                select(literal(project_id), TaxonomyNode.id).where(TaxonomyNode.id.in_(wanted)),
            )
        )
    await audit(
        db, "project.niches_set", workspace_id=workspace_id, actor_user_id=actor_id,
        entity_type="search_project", entity_id=project_id, diff={"taxonomy_node_ids": wanted},
    )
    return await list_project_niches(db, workspace_id, project_id)
