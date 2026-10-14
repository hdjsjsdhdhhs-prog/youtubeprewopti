"""Leads (Phase 3.7): automatic creation, deterministic scoring, listing with ТЗ §9 filters, Ideal Lead
Profiles and the lead / priority weights.

* **Creation.** Every channel of the workspace's projects gets a lead (``DISCOVERED``). Once at least one of
  its thumbnails has a current AI audit the lead moves to ``ANALYZED`` (automatically only from
  ``DISCOVERED``; later statuses belong to the human workflow, Phase 4). Both are written to
  ``lead_status_history`` with ``actor_user_id`` NULL.
* **Scoring** (``scoring.py``) runs in ``sync_leads``: after discovery / thumbnail / channel audit jobs of a
  project, after any change of weights or of the Ideal Lead Profile, and on demand
  (``POST /api/leads/sync``). A new ``lead_scores`` row is written only when the result differs from the
  current one.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, and_, delete, exists, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.core.state_machine import LEAD_MACHINE, LeadStatus
from app.domains.analysis import scoring as thumb_scoring
from app.domains.analysis.models import AILevel, ChannelAnalysis, ScoringKind, ThumbnailAnalysis
from app.domains.identity.service import audit
from app.domains.leads import scoring
from app.domains.leads.models import IdealLeadProfile, Lead, LeadScore, LeadStatusHistory
from app.domains.leads.schemas import (
    IdealLeadProfileIn,
    IdealLeadProfileOut,
    IdealLeadProfilePatch,
    IdealLeadProfileSaved,
    LeadDetail,
    LeadFilters,
    LeadList,
    LeadOut,
    LeadProfileOut,
    LeadProfileSaved,
    LeadScoreHistoryItem,
    LeadScoreOut,
    LeadSort,
    LeadSummary,
    LeadWeights,
    PriorityProfileOut,
    PriorityProfileSaved,
    PriorityWeights,
    RescoreResult,
    StatusChangeOut,
)
from app.domains.media.models import Thumbnail
from app.domains.projects.models import (
    ChannelDiscovery,
    ProjectChannel,
    SearchProject,
    SearchQuery,
    TaxonomyNode,
)
from app.domains.projects.service import get_project
from app.domains.youtube.models import Channel, ChannelMetrics, ChannelStatsSnapshot, Video
from app.domains.youtube.schemas import ChannelFilterSet, ChannelMetricsOut, ChannelOut, SortOrder
from app.domains.youtube.service import channel_filter_conditions

BATCH = 500
GROWTH_LOOKBACK_DAYS = 90
GROWTH_MIN_SPAN_DAYS = 7
SCORE_HISTORY_SHOWN = 20
REASON_CREATED = "auto: канал найден в проекте"
REASON_ANALYZED = "auto: превью канала проанализированы"


# ---------------------------------------------------------------------------
# Weights (scoring_profiles kind=lead / priority)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ActiveLeadWeights:
    version: int  # 0 = built-in defaults
    weights: LeadWeights


@dataclass(frozen=True)
class ActivePriorityWeights:
    version: int
    weights: PriorityWeights


async def active_lead_weights(db: AsyncSession, workspace_id: int) -> ActiveLeadWeights:
    row = await thumb_scoring.active_profile(db, workspace_id, ScoringKind.LEAD)
    if row is None:
        return ActiveLeadWeights(0, LeadWeights())
    return ActiveLeadWeights(row.version, LeadWeights.model_validate(row.weights))


async def active_priority_weights(db: AsyncSession, workspace_id: int) -> ActivePriorityWeights:
    row = await thumb_scoring.active_profile(db, workspace_id, ScoringKind.PRIORITY)
    if row is None:
        return ActivePriorityWeights(0, PriorityWeights())
    return ActivePriorityWeights(row.version, PriorityWeights.model_validate(row.weights))


def lead_profile_out(a: ActiveLeadWeights) -> LeadProfileOut:
    return LeadProfileOut(
        kind=ScoringKind.LEAD, version=a.version, is_default=a.version == 0, weights=a.weights
    )


def priority_profile_out(a: ActivePriorityWeights) -> PriorityProfileOut:
    return PriorityProfileOut(
        kind=ScoringKind.PRIORITY, version=a.version, is_default=a.version == 0, weights=a.weights
    )


async def set_lead_weights(
    db: AsyncSession, workspace_id: int, actor_id: int, weights: LeadWeights
) -> LeadProfileSaved:
    before = await active_lead_weights(db, workspace_id)
    row = await thumb_scoring.save_profile_version(
        db,
        workspace_id,
        actor_id,
        ScoringKind.LEAD,
        weights.model_dump(mode="json"),
        before.weights.model_dump(mode="json"),
    )
    rescored = await sync_leads(db, workspace_id)
    return LeadProfileSaved(
        **lead_profile_out(ActiveLeadWeights(row.version, weights)).model_dump(), rescored=rescored
    )


async def set_priority_weights(
    db: AsyncSession, workspace_id: int, actor_id: int, weights: PriorityWeights
) -> PriorityProfileSaved:
    before = await active_priority_weights(db, workspace_id)
    row = await thumb_scoring.save_profile_version(
        db,
        workspace_id,
        actor_id,
        ScoringKind.PRIORITY,
        weights.model_dump(mode="json"),
        before.weights.model_dump(mode="json"),
    )
    rescored = await sync_leads(db, workspace_id)
    return PriorityProfileSaved(
        **priority_profile_out(ActivePriorityWeights(row.version, weights)).model_dump(), rescored=rescored
    )


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------
async def _ancestors(db: AsyncSession) -> dict[int, int | None]:
    return {i: p for i, p in (await db.execute(select(TaxonomyNode.id, TaxonomyNode.parent_id))).tuples()}


def _with_ancestors(nodes: Iterable[int], parents: dict[int, int | None]) -> frozenset[int]:
    out: set[int] = set()
    for node in nodes:
        cur: int | None = node
        while cur is not None and cur not in out:
            out.add(cur)
            cur = parents.get(cur)
    return frozenset(out)


def _subscriber_growth(points: list[tuple[date, int]]) -> float | None:
    """Relative subscriber change per 30 days between the oldest and newest snapshot of the lookback."""
    if len(points) < 2:
        return None
    (d0, s0), (d1, s1) = points[0], points[-1]
    span = (d1 - d0).days
    if span < GROWTH_MIN_SPAN_DAYS or s0 <= 0:
        return None
    return round((s1 / s0 - 1) * 30 / span, 4)


async def gather_inputs(
    db: AsyncSession,
    workspace_id: int,
    channel_ids: Sequence[int],
    thumbnail_weights: dict[str, float],
    now: datetime,
) -> dict[int, scoring.LeadInputs]:
    ids = list(channel_ids)
    if not ids:
        return {}
    base = await db.execute(
        select(Channel, ChannelMetrics)
        .outerjoin(ChannelMetrics, ChannelMetrics.channel_id == Channel.id)
        .where(Channel.id.in_(ids))
    )
    overall = thumb_scoring.overall_sql(thumbnail_weights)
    thumbs = {
        cid: (int(n), avg, redesign)
        for cid, n, avg, redesign in (
            await db.execute(
                select(
                    Video.channel_id,
                    func.count(),
                    func.round(func.avg(overall), 2),
                    func.round(func.avg(ThumbnailAnalysis.redesign_potential), 2),
                )
                .join(Thumbnail, Thumbnail.video_id == Video.id)
                .join(
                    ThumbnailAnalysis,
                    and_(
                        ThumbnailAnalysis.image_asset_id == Thumbnail.image_asset_id,
                        ThumbnailAnalysis.is_current,
                    ),
                )
                .where(Video.channel_id.in_(ids), Video.is_short.is_not(True))
                .group_by(Video.channel_id)
            )
        ).tuples()
    }
    opportunity = {
        cid: level.value
        for cid, level in (
            await db.execute(
                select(ChannelAnalysis.channel_id, ChannelAnalysis.visual_opportunity).where(
                    ChannelAnalysis.channel_id.in_(ids), ChannelAnalysis.is_current
                )
            )
        ).tuples()
    }
    snapshots: dict[int, list[tuple[date, int]]] = {}
    since = (now - timedelta(days=GROWTH_LOOKBACK_DAYS)).date()
    for cid, day, subs in (
        await db.execute(
            select(
                ChannelStatsSnapshot.channel_id,
                ChannelStatsSnapshot.captured_on,
                ChannelStatsSnapshot.subscriber_count,
            )
            .where(
                ChannelStatsSnapshot.channel_id.in_(ids),
                ChannelStatsSnapshot.captured_on >= since,
                ChannelStatsSnapshot.subscriber_count.is_not(None),
            )
            .order_by(ChannelStatsSnapshot.channel_id, ChannelStatsSnapshot.captured_on)
        )
    ).tuples():
        if subs is not None:
            snapshots.setdefault(cid, []).append((day, int(subs)))
    niches: dict[int, set[int]] = {}
    for cid, node in (
        await db.execute(
            select(ChannelDiscovery.channel_id, SearchQuery.taxonomy_node_id)
            .join(SearchQuery, SearchQuery.id == ChannelDiscovery.search_query_id)
            .join(SearchProject, SearchProject.id == ChannelDiscovery.project_id)
            .where(
                ChannelDiscovery.channel_id.in_(ids),
                SearchProject.workspace_id == workspace_id,
                SearchQuery.taxonomy_node_id.is_not(None),
            )
            .distinct()
        )
    ).tuples():
        if node is not None:
            niches.setdefault(cid, set()).add(node)
    parents = await _ancestors(db) if niches else {}

    out: dict[int, scoring.LeadInputs] = {}
    for channel, m in base.tuples():
        n, avg, redesign = thumbs.get(channel.id, (0, None, None))
        out[channel.id] = scoring.LeadInputs(
            subscribers=channel.subscriber_count,
            subscribers_hidden=channel.subscribers_hidden,
            country=channel.country,
            language=channel.default_language,
            has_metrics=m is not None,
            avg_views=m.avg_views if m else None,
            median_views=m.median_views if m else None,
            last_video_at=m.last_video_at if m else None,
            videos_30d=m.videos_30d if m else None,
            views_trend=m.views_trend if m else None,
            subscriber_growth_30d=_subscriber_growth(snapshots.get(channel.id, [])),
            thumbnails_audited=n,
            avg_thumbnail_score=Decimal(avg) if avg is not None else None,
            avg_redesign_potential=Decimal(redesign) if redesign is not None else None,
            visual_opportunity=opportunity.get(channel.id),
            niche_ids=_with_ancestors(niches.get(channel.id, ()), parents),
        )
    return out


# ---------------------------------------------------------------------------
# Sync: create, advance, score
# ---------------------------------------------------------------------------
async def default_ideal_profile(db: AsyncSession, workspace_id: int) -> IdealLeadProfile | None:
    return await db.scalar(
        select(IdealLeadProfile).where(
            IdealLeadProfile.workspace_id == workspace_id, IdealLeadProfile.is_default
        )
    )


async def _create_missing(db: AsyncSession, workspace_id: int, project_id: int | None) -> int:
    source = (
        select(
            literal(workspace_id).label("workspace_id"),
            ProjectChannel.channel_id,
            func.min(ProjectChannel.project_id).label("created_from_project_id"),
        )
        .join(SearchProject, SearchProject.id == ProjectChannel.project_id)
        .where(SearchProject.workspace_id == workspace_id)
        .group_by(ProjectChannel.channel_id)
    )
    if project_id is not None:
        source = source.where(ProjectChannel.project_id == project_id)
    created = (
        (
            await db.execute(
                insert(Lead)
                .from_select(["workspace_id", "channel_id", "created_from_project_id"], source)
                .on_conflict_do_nothing(index_elements=["workspace_id", "channel_id"])
                .returning(Lead.id)
            )
        )
        .scalars()
        .all()
    )
    if created:
        await db.execute(
            insert(LeadStatusHistory).values(
                [
                    {
                        "lead_id": lid,
                        "from_status": None,
                        "to_status": LeadStatus.DISCOVERED,
                        "reason": REASON_CREATED,
                    }
                    for lid in created
                ]
            )
        )
    return len(created)


def _score_values(b: scoring.ScoreBreakdown, inputs: scoring.LeadInputs) -> dict[str, Any]:
    return {
        "total": b.total,
        "priority_score": b.priority,
        "data_coverage": b.data_coverage,
        **b.points,
        "avg_thumbnail_score": inputs.avg_thumbnail_score,
        "components": b.components,
    }


_COMPARED = (
    "total",
    "priority_score",
    "data_coverage",
    *scoring.COMPONENTS,
    "avg_thumbnail_score",
    "components",
    "lead_profile_version",
    "priority_profile_version",
    "thumbnail_profile_version",
    "ideal_lead_profile_id",
)


def _same(current: LeadScore | None, values: dict[str, Any]) -> bool:
    """Same result as the current row => no new history row."""
    return current is not None and all(getattr(current, k) == values[k] for k in _COMPARED)


async def sync_leads(
    db: AsyncSession, workspace_id: int, project_id: int | None = None, *, now: datetime | None = None
) -> RescoreResult:
    """Create missing leads for the workspace's (or one project's) channels, advance DISCOVERED → ANALYZED
    and (re)score them. Deterministic, no AI; the caller commits."""
    now = now or datetime.now(UTC)
    created = await _create_missing(db, workspace_id, project_id)
    thumb = await thumb_scoring.active_thumbnail_weights(db, workspace_id)
    lead_w = await active_lead_weights(db, workspace_id)
    prio_w = await active_priority_weights(db, workspace_id)
    ilp_row = await default_ideal_profile(db, workspace_id)
    ilp = scoring.IdealLeadCriteria.model_validate(ilp_row.criteria) if ilp_row is not None else None
    niche_values = {int(k): float(v) for k, v in lead_w.weights.niche_values.items()}
    versions = {
        "lead_profile_version": lead_w.version,
        "priority_profile_version": prio_w.version,
        "thumbnail_profile_version": thumb.version,
        "ideal_lead_profile_id": ilp_row.id if ilp_row else None,
    }

    leads_q = select(Lead).where(Lead.workspace_id == workspace_id)
    if project_id is not None:
        leads_q = leads_q.where(
            exists(
                select(ProjectChannel.channel_id).where(
                    ProjectChannel.channel_id == Lead.channel_id, ProjectChannel.project_id == project_id
                )
            )
        )
    leads = list(await db.scalars(leads_q.order_by(Lead.id)))
    analyzed = changed = unchanged = 0
    for start in range(0, len(leads), BATCH):
        batch = leads[start : start + BATCH]
        inputs = await gather_inputs(
            db, workspace_id, [lead.channel_id for lead in batch], thumb.weights, now
        )
        current = {
            s.lead_id: s
            for s in await db.scalars(
                select(LeadScore).where(
                    LeadScore.lead_id.in_([lead.id for lead in batch]), LeadScore.is_current
                )
            )
        }
        for lead in batch:
            i = inputs.get(lead.channel_id)
            if i is None:  # channel deleted meanwhile
                continue
            if lead.status == LeadStatus.DISCOVERED and i.thumbnails_audited > 0:
                await _transition(db, lead, LeadStatus.ANALYZED, None, REASON_ANALYZED, now)
                analyzed += 1
            comps = scoring.compute_components(i, ilp, niche_values, now)
            b = scoring.score(comps, lead_w.weights.component_weights(), prio_w.weights.component_weights())
            values = _score_values(b, i) | versions
            if _same(current.get(lead.id), values):
                unchanged += 1
                continue
            if lead.id in current:
                await db.execute(
                    update(LeadScore)
                    .where(LeadScore.lead_id == lead.id, LeadScore.is_current)
                    .values(is_current=False)
                )
            db.add(LeadScore(lead_id=lead.id, is_current=True, **values))
            changed += 1
        await db.flush()
    return RescoreResult(
        leads_created=created, leads_analyzed=analyzed, scores_changed=changed, scores_unchanged=unchanged
    )


async def _transition(
    db: AsyncSession, lead: Lead, target: LeadStatus, actor_id: int | None, reason: str, now: datetime
) -> None:
    LEAD_MACHINE.assert_can(lead.status, target)
    db.add(
        LeadStatusHistory(
            lead_id=lead.id,
            from_status=lead.status,
            to_status=target,
            actor_user_id=actor_id,
            reason=reason,
        )
    )
    lead.status = target
    lead.status_changed_at = now


async def sync_project_leads(
    db: AsyncSession, workspace_id: int | None, project_id: int | None
) -> dict[str, Any]:
    """Hook for project jobs (discovery, thumbnail / channel audit): result for the job's ``result``."""
    if workspace_id is None or project_id is None:
        return {"skipped": "no workspace or project"}
    return (await sync_leads(db, workspace_id, project_id)).model_dump()


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def activity_condition(level: scoring.ActivityLevel, now: datetime) -> ColumnElement[bool]:
    """«Activity at least ``level``» in SQL (twin of ``scoring.activity_level``)."""
    m = ChannelMetrics
    if level == scoring.ActivityLevel.HIGH:
        return and_(
            m.last_video_at >= now - timedelta(days=scoring.HIGH_ACTIVITY_DAYS),
            m.videos_30d >= scoring.UPLOADS_FOR_FULL_FREQUENCY,
        )
    if level == scoring.ActivityLevel.MEDIUM:
        return m.last_video_at >= now - timedelta(days=scoring.MEDIUM_ACTIVITY_DAYS)
    return m.last_video_at.is_not(None)


def _channel_out(channel: Channel, metrics: ChannelMetrics | None) -> ChannelOut:
    out = ChannelOut.model_validate(channel)
    out.metrics = ChannelMetricsOut.model_validate(metrics) if metrics is not None else None
    return out


def _lead_out(
    lead: Lead,
    channel: Channel,
    metrics: ChannelMetrics | None,
    score: LeadScore | None,
    opportunity: AILevel | None,
    now: datetime,
) -> LeadOut:
    return LeadOut(
        id=lead.id,
        status=lead.status,
        status_changed_at=lead.status_changed_at,
        created_at=lead.created_at,
        created_from_project_id=lead.created_from_project_id,
        channel=_channel_out(channel, metrics),
        activity_level=scoring.activity_level(
            metrics.last_video_at if metrics else None, metrics.videos_30d if metrics else None, now
        ),
        visual_opportunity=opportunity.value if opportunity is not None else None,
        score=LeadScoreOut.model_validate(score) if score is not None else None,
    )


def _in_project(project_id: int) -> ColumnElement[bool]:
    return exists(
        select(ProjectChannel.channel_id).where(
            ProjectChannel.channel_id == Lead.channel_id, ProjectChannel.project_id == project_id
        )
    )


def _lead_select() -> Any:
    return (
        select(Lead, Channel, ChannelMetrics, LeadScore, ChannelAnalysis.visual_opportunity)
        .join(Channel, Channel.id == Lead.channel_id)
        .outerjoin(ChannelMetrics, ChannelMetrics.channel_id == Channel.id)
        .outerjoin(LeadScore, and_(LeadScore.lead_id == Lead.id, LeadScore.is_current))
        .outerjoin(
            ChannelAnalysis, and_(ChannelAnalysis.channel_id == Channel.id, ChannelAnalysis.is_current)
        )
    )


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def list_leads(
    db: AsyncSession,
    workspace_id: int,
    f: LeadFilters,
    *,
    sort: LeadSort,
    order: SortOrder,
    limit: int,
    offset: int,
) -> LeadList:
    now = datetime.now(UTC)
    scope: list[ColumnElement[bool]] = [Lead.workspace_id == workspace_id]
    if f.project_id is not None:
        await get_project(db, workspace_id, f.project_id)
        scope.append(_in_project(f.project_id))
    where = list(scope)
    if f.channel_id is not None:
        where.append(Lead.channel_id == f.channel_id)
    if f.status:
        where.append(Lead.status.in_(f.status))
    if f.q:
        pattern = f"%{_escape_like(f.q.strip())}%"
        where.append(
            or_(Channel.title.ilike(pattern, escape="\\"), Channel.handle.ilike(pattern, escape="\\"))
        )
    for column, lo, hi in (
        (LeadScore.avg_thumbnail_score, f.min_thumbnail_score, f.max_thumbnail_score),
        (LeadScore.total, f.min_lead_score, f.max_lead_score),
        (LeadScore.priority_score, f.min_priority, None),
    ):
        if lo is not None:
            where.append(column >= lo)
        if hi is not None:
            where.append(column <= hi)
    if f.activity is not None:
        where.append(activity_condition(f.activity, now))
    if f.visual_opportunity is not None:
        where.append(ChannelAnalysis.visual_opportunity == AILevel(f.visual_opportunity))
    where.extend(
        await channel_filter_conditions(
            db,
            workspace_id,
            f.project_id,
            ChannelFilterSet(
                min_subscribers=f.min_subscribers,
                max_subscribers=f.max_subscribers,
                min_avg_views=f.min_avg_views,
                max_days_since_last_upload=f.max_days_since_last_upload,
                niche=f.niche,
            ),
        )
    )
    filtered = _lead_select().where(*where)
    total = (
        await db.scalar(select(func.count()).select_from(filtered.with_only_columns(Lead.id).subquery())) or 0
    )
    key: Any = {
        LeadSort.LEAD_SCORE: LeadScore.total,
        LeadSort.PRIORITY: LeadScore.priority_score,
        LeadSort.THUMBNAIL_SCORE: LeadScore.avg_thumbnail_score,
        LeadSort.THUMBNAIL_OPPORTUNITY: LeadScore.thumbnail_opportunity,
        LeadSort.VISUAL_OPPORTUNITY: ChannelAnalysis.visual_opportunity,
        LeadSort.VIEWS: ChannelMetrics.avg_views,
        LeadSort.SUBSCRIBERS: Channel.subscriber_count,
        LeadSort.ACTIVITY: LeadScore.activity,
        LeadSort.CONTACTABILITY: LeadScore.contactability,
        LeadSort.LAST_VIDEO: ChannelMetrics.last_video_at,
        LeadSort.CREATED: Lead.created_at,
    }[sort]
    ordered = key.asc().nulls_last() if order == SortOrder.ASC else key.desc().nulls_last()
    rows = await db.execute(filtered.order_by(ordered, Lead.id).limit(limit).offset(offset))
    items = [_lead_out(lead, ch, m, s, opp, now) for lead, ch, m, s, opp in rows.tuples()]

    scoped = (
        select(Lead.status, LeadScore.total)
        .outerjoin(LeadScore, and_(LeadScore.lead_id == Lead.id, LeadScore.is_current))
        .where(*scope)
        .subquery()
    )
    head = (
        await db.execute(
            select(func.count(), func.count(scoped.c.total), func.round(func.avg(scoped.c.total), 2))
        )
    ).one()
    by_status = {
        LeadStatus(s).value: int(n)
        for s, n in (
            await db.execute(select(scoped.c.status, func.count()).group_by(scoped.c.status))
        ).tuples()
    }
    return LeadList(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        summary=LeadSummary(
            leads=int(head[0]),
            scored=int(head[1]),
            by_status=by_status,
            avg_lead_score=Decimal(head[2]) if head[2] is not None else None,
        ),
        lead_profile=lead_profile_out(await active_lead_weights(db, workspace_id)),
        priority_profile=priority_profile_out(await active_priority_weights(db, workspace_id)),
    )


async def get_lead(db: AsyncSession, workspace_id: int, lead_id: int) -> LeadDetail:
    now = datetime.now(UTC)
    row = (
        await db.execute(_lead_select().where(Lead.id == lead_id, Lead.workspace_id == workspace_id))
    ).first()
    if row is None:
        raise NotFoundError("Lead not found")
    lead, channel, metrics, score, opp = row
    history = await db.scalars(
        select(LeadStatusHistory)
        .where(LeadStatusHistory.lead_id == lead.id)
        .order_by(LeadStatusHistory.created_at.desc(), LeadStatusHistory.id.desc())
    )
    scores = await db.scalars(
        select(LeadScore)
        .where(LeadScore.lead_id == lead.id)
        .order_by(LeadScore.created_at.desc(), LeadScore.id.desc())
        .limit(SCORE_HISTORY_SHOWN)
    )
    ilp = None
    if score is not None and score.ideal_lead_profile_id is not None:
        ilp_row = await db.get(IdealLeadProfile, score.ideal_lead_profile_id)
        ilp = IdealLeadProfileOut.model_validate(ilp_row) if ilp_row is not None else None
    base = _lead_out(lead, channel, metrics, score, opp, now)
    return LeadDetail(
        **base.model_dump(),
        history=[StatusChangeOut.model_validate(h) for h in history],
        score_history=[LeadScoreHistoryItem.model_validate(s) for s in scores],
        ideal_lead_profile=ilp,
    )


# ---------------------------------------------------------------------------
# Ideal Lead Profiles
# ---------------------------------------------------------------------------
async def list_ideal_profiles(db: AsyncSession, workspace_id: int) -> list[IdealLeadProfileOut]:
    rows = await db.scalars(
        select(IdealLeadProfile)
        .where(IdealLeadProfile.workspace_id == workspace_id)
        .order_by(IdealLeadProfile.is_default.desc(), IdealLeadProfile.name)
    )
    return [IdealLeadProfileOut.model_validate(r) for r in rows]


async def _get_ideal(db: AsyncSession, workspace_id: int, profile_id: int) -> IdealLeadProfile:
    row = await db.scalar(
        select(IdealLeadProfile).where(
            IdealLeadProfile.id == profile_id, IdealLeadProfile.workspace_id == workspace_id
        )
    )
    if row is None:
        raise NotFoundError("Ideal lead profile not found")
    return row


async def _name_taken(db: AsyncSession, workspace_id: int, name: str, except_id: int | None = None) -> None:
    conds = [IdealLeadProfile.workspace_id == workspace_id, IdealLeadProfile.name == name]
    if except_id is not None:
        conds.append(IdealLeadProfile.id != except_id)
    if await db.scalar(select(exists().where(*conds))):
        raise ConflictError("An ideal lead profile with this name already exists", code="name_taken")


async def _clear_default(db: AsyncSession, workspace_id: int, except_id: int | None = None) -> None:
    conds = [IdealLeadProfile.workspace_id == workspace_id, IdealLeadProfile.is_default]
    if except_id is not None:
        conds.append(IdealLeadProfile.id != except_id)
    await db.execute(update(IdealLeadProfile).where(*conds).values(is_default=False))


async def create_ideal_profile(
    db: AsyncSession, workspace_id: int, actor_id: int, body: IdealLeadProfileIn
) -> IdealLeadProfileSaved:
    await _name_taken(db, workspace_id, body.name)
    if body.is_default:
        await _clear_default(db, workspace_id)
    row = IdealLeadProfile(
        workspace_id=workspace_id,
        name=body.name,
        criteria=body.criteria.model_dump(mode="json"),
        is_default=body.is_default,
        created_by=actor_id,
    )
    db.add(row)
    await db.flush()
    await audit(
        db,
        "ideal_lead_profile.created",
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        entity_type="ideal_lead_profile",
        entity_id=row.id,
        diff={"name": row.name, "criteria": row.criteria, "is_default": row.is_default},
    )
    rescored = await sync_leads(db, workspace_id)
    await db.refresh(row)
    return IdealLeadProfileSaved(**IdealLeadProfileOut.model_validate(row).model_dump(), rescored=rescored)


async def update_ideal_profile(
    db: AsyncSession, workspace_id: int, actor_id: int, profile_id: int, body: IdealLeadProfilePatch
) -> IdealLeadProfileSaved:
    row = await _get_ideal(db, workspace_id, profile_id)
    before = {"name": row.name, "criteria": row.criteria, "is_default": row.is_default}
    if body.name is not None and body.name != row.name:
        await _name_taken(db, workspace_id, body.name, row.id)
        row.name = body.name
    if body.criteria is not None:
        row.criteria = body.criteria.model_dump(mode="json")
    if body.is_default is True and not row.is_default:
        await _clear_default(db, workspace_id, row.id)
        await db.flush()
        row.is_default = True
    elif body.is_default is False:
        row.is_default = False
    await db.flush()
    await audit(
        db,
        "ideal_lead_profile.updated",
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        entity_type="ideal_lead_profile",
        entity_id=row.id,
        diff={
            "before": before,
            "after": {"name": row.name, "criteria": row.criteria, "is_default": row.is_default},
        },
    )
    rescored = await sync_leads(db, workspace_id)
    await db.refresh(row)
    return IdealLeadProfileSaved(**IdealLeadProfileOut.model_validate(row).model_dump(), rescored=rescored)


async def delete_ideal_profile(
    db: AsyncSession, workspace_id: int, actor_id: int, profile_id: int
) -> RescoreResult:
    row = await _get_ideal(db, workspace_id, profile_id)
    await db.execute(delete(IdealLeadProfile).where(IdealLeadProfile.id == row.id))
    await audit(
        db,
        "ideal_lead_profile.deleted",
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        entity_type="ideal_lead_profile",
        entity_id=profile_id,
        diff={"name": row.name},
    )
    return await sync_leads(db, workspace_id)
