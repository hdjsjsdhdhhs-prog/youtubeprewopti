"""Analytics (Phase 9, ТЗ §87: conversion, A/B, campaigns, niches, cost, AI feedback; §39 Dashboard, §46,
§51, §75). Read-only: aggregates are computed on request from the tables of the other domains (ADR-0001
allows read-only queries here); nothing is stored, so changing weights or recording an event shows at once.

What a period means (dates inclusive, UTC):

* **leads** (dashboard tiles, funnel, niches, score ranges) — the cohort *found* in the period, followed to
  its current state («of the channels found in October, how many got an answer by now»);
* **messages** (campaigns, A/B) — sent in the period;
* **AI spend** — calls made in the period;
* **decisions** (AI feedback) — made in the period.

Comparisons never claim a cause (ТЗ §28): a group needs ``MIN_SAMPLE`` sent before it is ranked, and the
A/B leader is only named when the gap is beyond chance (two-proportion z-test).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, Numeric, and_, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, NotFoundError
from app.core.state_machine import LeadStatus
from app.domains.ai.budget import SPENT
from app.domains.ai.models import AICall, AICallStatus
from app.domains.ai.registry import AITask
from app.domains.analytics import stats
from app.domains.analytics.schemas import (
    ABDimension,
    ABGroup,
    ABReport,
    AIFeedbackReport,
    Best,
    CampaignReport,
    CampaignRow,
    CostReport,
    CostRow,
    CostSummary,
    FunnelStep,
    GenerationFeedbackRow,
    LeadScoreBand,
    NicheReport,
    NicheRow,
    OfferFeedbackRow,
    Outcomes,
    Overview,
    PeriodOut,
    ScoreRangeReport,
    ScoreRangeRow,
    Tiles,
)
from app.domains.campaigns.models import Campaign, CampaignLead, CampaignLeadStatus
from app.domains.campaigns.service import MIN_SAMPLE
from app.domains.contacts.service import has_usable_contact
from app.domains.feedback import service as feedback
from app.domains.feedback.models import FeedbackEvent, FeedbackKind
from app.domains.generation.models import GeneratedThumbnail, GenerationRequest, VariantStatus
from app.domains.leads.models import Lead, LeadScore, LeadStatusHistory
from app.domains.leads.tags import HIGH_POTENTIAL_SCORE, WEAK_THUMBNAIL_SCORE
from app.domains.offers.models import STYLES, Offer, OfferStatus, OfferStyle, OfferVariant
from app.domains.offers.service import label_of
from app.domains.outreach.models import Message, MessageEvent, MessageEventType, MessageStatus
from app.domains.projects.models import (
    ChannelDiscovery,
    ProjectChannel,
    SearchProject,
    SearchQuery,
    TaxonomyNode,
)

ZERO = Decimal(0)
SCORE_Q = Decimal("0.01")
ANALYSIS_TASKS = frozenset(
    {AITask.THUMBNAIL_ANALYSIS, AITask.CHANNEL_ANALYSIS, AITask.CLASSIFICATION, AITask.LEAD_EXPLANATION}
)
FAILED_CALLS = (AICallStatus.ERROR, AICallStatus.INVALID_OUTPUT, AICallStatus.REFUSED)
APPROVED_OFFERS = (OfferStatus.APPROVED, OfferStatus.SCHEDULED, OfferStatus.SENT)
SAMPLE_STATUSES = (VariantStatus.READY, VariantStatus.APPROVED, VariantStatus.REJECTED, VariantStatus.FINAL)
SELECTING_STATUSES = frozenset({LeadStatus.SHORTLISTED.value, LeadStatus.SELECTED.value})
NO_NICHE = "Без ниши"
RANKING_NOTE = (
    "Сравнение по доле ответов среди лидов, которым написали. Пока в группе меньше {n} отправленных, она не "
    "ранжируется: разница может быть случайной. Это корреляция, а не причина — на ответ влияют и текст, и "
    "время, и сам канал."
)
AB_NOTE = (
    "Это счётчики по отправленным письмам, а не выводы. Лидер называется, только если у двух лучших групп "
    "не меньше {n} отправленных и разница в доле ответов больше случайной (p < 0,05). Варианты отличаются "
    "стилем целиком: вклад темы, текста и CTA по отдельности по этим данным не разделить. «Открыто» — только "
    "отмеченное вручную: пиксель отслеживания не используется."
)


# ---------------------------------------------------------------------------
# Period and scope
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Scope:
    workspace_id: int
    date_from: date | None = None
    date_to: date | None = None
    project_id: int | None = None

    @property
    def out(self) -> PeriodOut:
        return PeriodOut(date_from=self.date_from, date_to=self.date_to, project_id=self.project_id)

    def between(self, column: Any) -> list[ColumnElement[bool]]:
        conds: list[ColumnElement[bool]] = []
        if self.date_from is not None:
            conds.append(column >= datetime.combine(self.date_from, time.min, UTC))
        if self.date_to is not None:
            conds.append(column < datetime.combine(self.date_to + timedelta(days=1), time.min, UTC))
        return conds


async def make_scope(
    db: AsyncSession,
    workspace_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
    project_id: int | None = None,
) -> Scope:
    if date_from is not None and date_to is not None and date_from > date_to:
        raise AppError("date_from must not be after date_to", code="invalid_period")
    if project_id is not None:
        found = await db.scalar(
            select(SearchProject.id).where(
                SearchProject.id == project_id, SearchProject.workspace_id == workspace_id
            )
        )
        if found is None:
            raise NotFoundError("Project not found")
    return Scope(workspace_id, date_from, date_to, project_id)


def _lead_scope(s: Scope) -> list[ColumnElement[bool]]:
    conds: list[ColumnElement[bool]] = [Lead.workspace_id == s.workspace_id]
    if s.project_id is not None:
        members = select(ProjectChannel.channel_id).where(ProjectChannel.project_id == s.project_id)
        conds.append(Lead.channel_id.in_(members))
    return conds


def _outcomes(c: stats.Counts) -> Outcomes:
    return Outcomes(
        sent=c.sent, delivered=c.delivered, opened=c.opened, replied=c.replied,
        positive_reply=c.positive_reply, negative_reply=c.negative_reply, bounced=c.bounced,
        opt_out=c.opt_out, no_reply=c.no_reply, reply_rate=stats.rate(c.replied, c.sent),
        positive_rate=stats.rate(c.positive_reply, c.sent), small_sample=c.sent < MIN_SAMPLE,
    )


def _best(rows: Iterable[tuple[str, str, Outcomes]]) -> Best | None:
    rows = list(rows)
    key = stats.best_of([(k, o.replied, o.sent) for k, _, o in rows], MIN_SAMPLE)
    for k, name, o in rows:
        if k == key:
            return Best(key=k, name=name, sent=o.sent, reply_rate=o.reply_rate)
    return None


def _avg(values: Sequence[Decimal]) -> Decimal | None:
    return (sum(values, ZERO) / len(values)).quantize(SCORE_Q) if values else None


async def _events_of(db: AsyncSession, message_ids: Sequence[int]) -> dict[int, set[MessageEventType]]:
    out: dict[int, set[MessageEventType]] = {m: set() for m in message_ids}
    if not out:
        return out
    for mid, t in (
        await db.execute(
            select(MessageEvent.message_id, MessageEvent.type).where(MessageEvent.message_id.in_(list(out)))
        )
    ).tuples():
        out[mid].add(t)
    return out


# ---------------------------------------------------------------------------
# Lead facts: one row per lead of the cohort
# ---------------------------------------------------------------------------
@dataclass
class LeadFact:
    id: int
    channel_id: int
    total: Decimal | None
    avg_thumbnail_score: Decimal | None
    has_contact: bool
    statuses: set[LeadStatus] = field(default_factory=set)
    events: set[MessageEventType] | None = None  # union over its sent messages; None = never sent
    sample: bool = False
    level: int = 0


async def _lead_facts(db: AsyncSession, s: Scope) -> dict[int, LeadFact]:
    where = [*_lead_scope(s), *s.between(Lead.created_at)]
    ids = select(Lead.id).where(*where)
    rows = await db.execute(
        select(
            Lead.id, Lead.channel_id, Lead.status, LeadScore.total, LeadScore.avg_thumbnail_score,
            has_usable_contact(s.workspace_id, Lead.channel_id),
        )
        .outerjoin(LeadScore, and_(LeadScore.lead_id == Lead.id, LeadScore.is_current))
        .where(*where)
    )
    facts: dict[int, LeadFact] = {}
    for lid, cid, status, total, avg, contact in rows.tuples():
        facts[lid] = LeadFact(lid, cid, total, avg, bool(contact), {status})
    if not facts:
        return facts
    for lid, st in (
        await db.execute(
            select(LeadStatusHistory.lead_id, LeadStatusHistory.to_status)
            .where(LeadStatusHistory.lead_id.in_(ids))
            .distinct()
        )
    ).tuples():
        facts[lid].statuses.add(st)
    sent = (
        await db.execute(
            select(Message.id, Message.lead_id).where(
                Message.lead_id.in_(ids), Message.status == MessageStatus.SENT
            )
        )
    ).tuples().all()
    events = await _events_of(db, [m for m, _ in sent])
    for mid, lid in sent:
        fact = facts[lid]
        fact.events = (fact.events or set()) | events[mid]
    for lid in await db.scalars(
        select(GenerationRequest.lead_id)
        .join(GeneratedThumbnail, GeneratedThumbnail.generation_request_id == GenerationRequest.id)
        .where(GenerationRequest.lead_id.in_(ids), GeneratedThumbnail.status.in_(SAMPLE_STATUSES))
        .distinct()
    ):
        facts[lid].sample = True
    for fact in facts.values():
        fact.level = stats.lead_level(fact.statuses, fact.events)
    return facts


def _lead_outcomes(facts: Iterable[LeadFact]) -> Outcomes:
    return _outcomes(stats.count_outcomes(f.events for f in facts if f.events is not None))


def _funnel(facts: Sequence[LeadFact]) -> list[FunnelStep]:
    out: list[FunnelStep] = []
    start = len(facts)
    previous: int | None = None
    for level, key, label in stats.FUNNEL:
        n = sum(1 for f in facts if f.level >= level)
        out.append(
            FunnelStep(
                key=key, label=label, leads=n,
                rate_from_previous=stats.rate(n, previous) if previous is not None else None,
                rate_from_start=stats.rate(n, start),
            )
        )
        previous = n
    return out


# ---------------------------------------------------------------------------
# Costs (ТЗ §75)
# ---------------------------------------------------------------------------
def _cost_where(s: Scope) -> list[ColumnElement[bool]]:
    conds: list[ColumnElement[bool]] = [AICall.workspace_id == s.workspace_id, *s.between(AICall.created_at)]
    if s.project_id is not None:
        conds.append(AICall.project_id == s.project_id)
    return conds


def _cost_columns() -> tuple[Any, ...]:
    return (
        func.count(AICall.id),
        func.count(AICall.id).filter(AICall.status.in_(FAILED_CALLS)),
        func.count(AICall.id).filter(
            and_(AICall.actual_cost_usd.is_(None), AICall.estimated_cost_usd.is_(None))
        ),
        SPENT,
        func.coalesce(func.sum(AICall.input_tokens), 0),
        func.coalesce(func.sum(AICall.output_tokens), 0),
        func.coalesce(func.sum(AICall.image_inputs), 0),
        func.coalesce(func.sum(AICall.image_outputs), 0),
        func.avg(AICall.duration_ms),
    )


def _cost_row(key: str, provider: str | None, r: Sequence[Any]) -> CostRow:
    calls, failed, unpriced, cost, tin, tout, iin, iout, dur = r
    return CostRow(
        key=key, provider=provider, calls=int(calls), failed_calls=int(failed), unpriced_calls=int(unpriced),
        cost_usd=Decimal(cost), input_tokens=int(tin), output_tokens=int(tout), image_inputs=int(iin),
        image_outputs=int(iout), avg_duration_ms=int(dur) if dur is not None else None,
    )


async def _cost_by_task(db: AsyncSession, s: Scope) -> list[CostRow]:
    rows = await db.execute(
        select(AICall.task, *_cost_columns())
        .where(*_cost_where(s))
        .group_by(AICall.task)
        .order_by(AICall.task)
    )
    return [_cost_row(r[0], None, r[1:]) for r in rows.all()]


def _cost_summary(by_task: Sequence[CostRow], facts: Sequence[LeadFact]) -> CostSummary:
    groups = {"analysis": ZERO, "generation": ZERO, "offers": ZERO, "other": ZERO}
    for row in by_task:
        if row.key in ANALYSIS_TASKS:
            groups["analysis"] += row.cost_usd
        elif row.key == AITask.THUMBNAIL_GENERATION:
            groups["generation"] += row.cost_usd
        elif row.key == AITask.OFFER_GENERATION:
            groups["offers"] += row.cost_usd
        else:
            groups["other"] += row.cost_usd
    total = sum(groups.values(), ZERO)

    def count(level: int) -> int:
        return sum(1 for f in facts if f.level >= level)

    return CostSummary(
        total_usd=total, analysis_usd=groups["analysis"], generation_usd=groups["generation"],
        offers_usd=groups["offers"], other_usd=groups["other"], calls=sum(r.calls for r in by_task),
        unpriced_calls=sum(r.unpriced_calls for r in by_task), per_lead_usd=stats.per_unit(total, len(facts)),
        per_analyzed_lead_usd=stats.per_unit(total, count(stats.ANALYZED)),
        per_sent_usd=stats.per_unit(total, count(stats.SENT)),
        per_reply_usd=stats.per_unit(total, count(stats.REPLIED)),
        per_positive_usd=stats.per_unit(total, count(stats.POSITIVE)),
    )


async def cost_report(db: AsyncSession, s: Scope) -> CostReport:
    by_task = await _cost_by_task(db, s)
    facts = list((await _lead_facts(db, s)).values())
    by_model = [
        _cost_row(r[0], r[1], r[2:])
        for r in (
            await db.execute(
                select(AICall.model_key, AICall.provider, *_cost_columns())
                .where(*_cost_where(s))
                .group_by(AICall.model_key, AICall.provider)
                .order_by(SPENT.desc(), AICall.model_key)
            )
        ).all()
    ]
    return CostReport(period=s.out, summary=_cost_summary(by_task, facts), by_task=by_task, by_model=by_model)


# ---------------------------------------------------------------------------
# Niches (ТЗ §51)
# ---------------------------------------------------------------------------
async def _lead_niches(
    db: AsyncSession, s: Scope, facts: dict[int, LeadFact]
) -> tuple[dict[int, set[int]], dict[int, str]]:
    """(lead id -> top-level niches of the queries that found its channel in the workspace / project,
    niche id -> name). A topic / subtopic counts for its niche (ТЗ §54)."""
    channel_ids = {f.channel_id for f in facts.values()}
    if not channel_ids:
        return {}, {}
    conds = [SearchProject.workspace_id == s.workspace_id, SearchQuery.taxonomy_node_id.is_not(None)]
    if s.project_id is not None:
        conds.append(ChannelDiscovery.project_id == s.project_id)
    found = (
        await db.execute(
            select(ChannelDiscovery.channel_id, SearchQuery.taxonomy_node_id)
            .join(SearchQuery, SearchQuery.id == ChannelDiscovery.search_query_id)
            .join(SearchProject, SearchProject.id == ChannelDiscovery.project_id)
            .where(*conds, ChannelDiscovery.channel_id.in_(list(channel_ids)))
            .distinct()
        )
    ).tuples().all()
    if not found:
        return {lid: set() for lid in facts}, {}
    # the taxonomy is small and global (niche > topic > subtopic)
    parent: dict[int, int | None] = {}
    names: dict[int, str] = {}
    for nid, pid, name in (
        await db.execute(select(TaxonomyNode.id, TaxonomyNode.parent_id, TaxonomyNode.name))
    ).tuples():
        parent[nid], names[nid] = pid, name

    def root(nid: int) -> int:
        for _ in range(10):  # depth is 3; the bound only guards against a broken tree
            up = parent.get(nid)
            if up is None:
                break
            nid = up
        return nid

    by_channel: dict[int, set[int]] = defaultdict(set)
    for cid, node in found:
        if node is not None:
            by_channel[cid].add(root(node))
    return {lid: by_channel.get(f.channel_id, set()) for lid, f in facts.items()}, names


def _niche_row(niche_id: int | None, name: str, facts: Sequence[LeadFact]) -> NicheRow:
    scores = [f.avg_thumbnail_score for f in facts if f.avg_thumbnail_score is not None]
    return NicheRow(
        niche_id=niche_id, name=name, leads=len(facts),
        analyzed=sum(1 for f in facts if f.level >= stats.ANALYZED),
        selected=sum(1 for f in facts if f.level >= stats.SELECTED),
        avg_thumbnail_score=_avg(scores),
        weak_thumbnails=sum(1 for v in scores if v < WEAK_THUMBNAIL_SCORE),
        high_opportunity=sum(1 for f in facts if f.total is not None and f.total >= HIGH_POTENTIAL_SCORE),
        with_contacts=sum(1 for f in facts if f.has_contact),
        outcomes=_lead_outcomes(facts),
    )


async def _niche_rows(db: AsyncSession, s: Scope, facts: dict[int, LeadFact]) -> list[NicheRow]:
    niches, names = await _lead_niches(db, s, facts)
    groups: dict[int | None, list[LeadFact]] = defaultdict(list)
    for lid, fact in facts.items():
        keys: list[int | None] = list(niches.get(lid, ())) or [None]
        for nid in keys:
            groups[nid].append(fact)
    rows = [
        _niche_row(nid, names.get(nid, NO_NICHE) if nid is not None else NO_NICHE, fs)
        for nid, fs in groups.items()
    ]
    rows.sort(key=lambda r: (r.niche_id is None, -r.leads, r.name))
    return rows


def _best_niche(rows: Sequence[NicheRow]) -> Best | None:
    return _best((str(r.niche_id), r.name, r.outcomes) for r in rows if r.niche_id is not None)


async def niche_report(db: AsyncSession, s: Scope) -> NicheReport:
    rows = await _niche_rows(db, s, await _lead_facts(db, s))
    return NicheReport(
        period=s.out, items=rows, best=_best_niche(rows), min_sample=MIN_SAMPLE,
        note="Лид учитывается в каждой нише, по запросам которой нашёлся его канал. "
        + RANKING_NOTE.format(n=MIN_SAMPLE),
    )


# ---------------------------------------------------------------------------
# Thumbnail score ranges (ТЗ §39)
# ---------------------------------------------------------------------------
def _score_rows(facts: Sequence[LeadFact]) -> list[ScoreRangeRow]:
    groups: dict[str, list[LeadFact]] = defaultdict(list)
    for fact in facts:
        b = stats.bucket_of(fact.avg_thumbnail_score, stats.THUMBNAIL_BUCKETS)
        groups[b.key if b is not None else "none"].append(fact)
    out: list[ScoreRangeRow] = []
    for b in (*stats.THUMBNAIL_BUCKETS, None):
        fs = groups.get(b.key if b is not None else "none", [])
        if b is None and not fs:
            continue
        selected = sum(1 for f in fs if f.level >= stats.SELECTED)
        out.append(
            ScoreRangeRow(
                key=b.key if b else "none", label=b.label if b else "Нет аудита превью",
                low=b.low if b else None, high=b.high if b else None, leads=len(fs), selected=selected,
                selection_rate=stats.rate(selected, len(fs)), outcomes=_lead_outcomes(fs),
            )
        )
    return out


def _best_range(rows: Sequence[ScoreRangeRow]) -> Best | None:
    return _best((r.key, r.label, r.outcomes) for r in rows if r.key != "none")


async def score_range_report(db: AsyncSession, s: Scope) -> ScoreRangeReport:
    rows = _score_rows(list((await _lead_facts(db, s)).values()))
    return ScoreRangeReport(
        period=s.out, items=rows, best=_best_range(rows), min_sample=MIN_SAMPLE,
        note="Средний балл превью канала (1–10, ручные оценки учитываются). "
        + RANKING_NOTE.format(n=MIN_SAMPLE),
    )


# ---------------------------------------------------------------------------
# Campaigns (ТЗ §29)
# ---------------------------------------------------------------------------
def _sent_where(s: Scope) -> list[ColumnElement[bool]]:
    conds: list[ColumnElement[bool]] = [
        Message.workspace_id == s.workspace_id, Message.status == MessageStatus.SENT,
        *s.between(Message.sent_at),
    ]
    if s.project_id is not None:
        conds.append(Message.lead_id.in_(select(Lead.id).where(*_lead_scope(s))))
    return conds


async def _campaign_rows(db: AsyncSession, s: Scope) -> list[CampaignRow]:
    where: list[ColumnElement[bool]] = [Campaign.workspace_id == s.workspace_id]
    if s.project_id is not None:
        where.append(Campaign.project_id == s.project_id)
    campaigns = list(await db.scalars(select(Campaign).where(*where).order_by(Campaign.created_at.desc())))
    if not campaigns:
        return []
    cids = [c.id for c in campaigns]
    not_removed = and_(CampaignLead.campaign_id.in_(cids), CampaignLead.status != CampaignLeadStatus.REMOVED)
    leads = dict(
        (await db.execute(
            select(CampaignLead.campaign_id, func.count())
            .where(not_removed)
            .group_by(CampaignLead.campaign_id)
        )).tuples().all()
    )
    approved = dict(
        (await db.execute(
            select(CampaignLead.campaign_id, func.count())
            .join(Offer, Offer.id == CampaignLead.offer_id)
            .where(not_removed, Offer.status.in_(APPROVED_OFFERS))
            .group_by(CampaignLead.campaign_id)
        )).tuples().all()
    )
    costs = dict(
        (await db.execute(
            select(CampaignLead.campaign_id, func.coalesce(func.sum(OfferVariant.cost_usd), 0))
            .join(OfferVariant, OfferVariant.offer_id == CampaignLead.offer_id)
            .where(CampaignLead.campaign_id.in_(cids))
            .group_by(CampaignLead.campaign_id)
        )).tuples().all()
    )
    sent = (
        await db.execute(
            select(Message.id, Message.campaign_id).where(*_sent_where(s), Message.campaign_id.in_(cids))
        )
    ).tuples().all()
    events = await _events_of(db, [m for m, _ in sent])
    per: dict[int, list[set[MessageEventType]]] = defaultdict(list)
    for mid, cid in sent:
        if cid is not None:
            per[cid].append(events[mid])
    out: list[CampaignRow] = []
    for c in campaigns:
        o = _outcomes(stats.count_outcomes(per.get(c.id, [])))
        cost = Decimal(costs.get(c.id) or 0)
        out.append(
            CampaignRow(
                id=c.id, name=c.name, status=c.status, leads=int(leads.get(c.id, 0)),
                approved=int(approved.get(c.id, 0)), cost_usd=cost,
                cost_per_reply_usd=stats.per_unit(cost, o.replied), outcomes=o,
            )
        )
    return out


def _best_campaign(rows: Sequence[CampaignRow]) -> Best | None:
    return _best((str(r.id), r.name, r.outcomes) for r in rows)


async def campaign_report(db: AsyncSession, s: Scope) -> CampaignReport:
    rows = await _campaign_rows(db, s)
    return CampaignReport(
        period=s.out, items=rows, best=_best_campaign(rows), min_sample=MIN_SAMPLE,
        note="Письма — по дате отправки, стоимость — написанные для кампании офферы. "
        + RANKING_NOTE.format(n=MIN_SAMPLE),
    )


# ---------------------------------------------------------------------------
# A/B across all sent messages (ТЗ §28)
# ---------------------------------------------------------------------------
AB_LABELS = {
    "with": "С вложением (образец превью)",
    "without": "Без вложения",
    "email": "Email",
    "telegram": "Telegram",
    "vk": "VK",
    "manual": "Вручную",
    "unknown": "Неизвестно",
}


async def ab_report(
    db: AsyncSession, s: Scope, dimension: ABDimension = "style", campaign_id: int | None = None
) -> ABReport:
    where = _sent_where(s)
    if campaign_id is not None:
        if await db.scalar(
            select(Campaign.id).where(Campaign.id == campaign_id, Campaign.workspace_id == s.workspace_id)
        ) is None:
            raise NotFoundError("Campaign not found")
        where.append(Message.campaign_id == campaign_id)
    rows = (
        await db.execute(
            select(Message.id, OfferVariant.style, Message.attachment_asset_id, Message.channel)
            .outerjoin(OfferVariant, OfferVariant.id == Message.offer_variant_id)
            .where(*where)
        )
    ).tuples().all()
    events = await _events_of(db, [r[0] for r in rows])
    groups: dict[str, list[set[MessageEventType]]] = defaultdict(list)
    for mid, style, attachment, channel in rows:
        if dimension == "style":
            key = style.value if style is not None else "unknown"
        elif dimension == "attachment":
            key = "with" if attachment is not None else "without"
        else:
            key = channel.value
        groups[key].append(events[mid])
    if dimension == "style":  # every style is shown, also without sends
        order = [st.value for st in STYLES] + (["unknown"] if "unknown" in groups else [])
    elif dimension == "attachment":
        order = ["with", "without"]
    else:
        order = sorted(groups)
    out: list[ABGroup] = []
    for key in order:
        group_style = OfferStyle(key) if dimension == "style" and key != "unknown" else None
        label = f"{label_of(group_style)} — {key}" if group_style else AB_LABELS.get(key, key)
        outcomes = _outcomes(stats.count_outcomes(groups[key]))
        out.append(ABGroup(key=key, label=label, style=group_style, outcomes=outcomes))
    cmp = stats.compare(
        [(g.key, g.outcomes.replied, g.outcomes.sent) for g in out if g.key != "unknown"], MIN_SAMPLE
    )
    return ABReport(
        period=s.out, dimension=dimension, campaign_id=campaign_id, groups=out, verdict=cmp.verdict,
        leader=cmp.leader, p_value=cmp.p_value, min_sample=MIN_SAMPLE, note=AB_NOTE.format(n=MIN_SAMPLE),
    )


def _best_variant(report: ABReport) -> Best | None:
    return _best((g.key, g.label, g.outcomes) for g in report.groups if g.key != "unknown")


# ---------------------------------------------------------------------------
# AI vs human (ТЗ §46)
# ---------------------------------------------------------------------------
async def _generation_rows(db: AsyncSession, s: Scope, column: Any) -> list[GenerationFeedbackRow]:
    g = GeneratedThumbnail
    where: list[ColumnElement[bool]] = [
        GenerationRequest.workspace_id == s.workspace_id,
        g.status.in_(SAMPLE_STATUSES),
        *s.between(g.created_at),
    ]
    if s.project_id is not None:
        where.append(GenerationRequest.project_id == s.project_id)
    rows = await db.execute(
        select(
            column, func.count(),
            func.count().filter(g.status.in_((VariantStatus.APPROVED, VariantStatus.FINAL))),
            func.count().filter(g.status == VariantStatus.FINAL),
            func.count().filter(g.status == VariantStatus.REJECTED),
            func.count().filter(g.status == VariantStatus.READY),
        )
        .join(GenerationRequest, GenerationRequest.id == g.generation_request_id)
        .where(*where)
        .group_by(column)
        .order_by(func.count().desc(), column)
    )
    return [
        GenerationFeedbackRow(
            key=key, generated=int(n), approved=int(a), final=int(fin), rejected=int(r), undecided=int(u),
            approval_rate=stats.rate(int(a), int(a) + int(r)),
        )
        for key, n, a, fin, r, u in rows.tuples()
    ]


def _event_where(s: Scope, kind: FeedbackKind) -> list[ColumnElement[bool]]:
    return [
        FeedbackEvent.workspace_id == s.workspace_id,
        FeedbackEvent.kind == kind,
        *s.between(FeedbackEvent.created_at),
    ]


async def _offer_rows(db: AsyncSession, s: Scope) -> list[OfferFeedbackRow]:
    e = FeedbackEvent
    style = e.payload["style"].astext
    decision = e.payload["decision"].astext
    pscore = cast(e.payload["personalization_score"].astext, Numeric)
    project = [Offer.project_id == s.project_id] if s.project_id is not None else []
    dec = await db.execute(
        select(
            style,
            func.count().filter(decision == "approve"),
            func.count().filter(decision == "reject"),
            func.count().filter(decision == "skip"),
            func.avg(pscore).filter(decision == "approve"),
            func.avg(pscore).filter(decision == "reject"),
        )
        .join(Offer, Offer.id == e.target_id)
        .where(*_event_where(s, FeedbackKind.OFFER_DECISION), *project)
        .group_by(style)
    )
    edits = {
        st: (int(n), int(o))
        for st, n, o in (
            await db.execute(
                select(style, func.count(), func.count(func.distinct(e.target_id)))
                .join(Offer, Offer.id == e.target_id)
                .where(*_event_where(s, FeedbackKind.OFFER_EDIT), *project)
                .group_by(style)
            )
        ).tuples()
    }
    by_style: dict[str | None, OfferFeedbackRow] = {}
    for st, a, r, sk, avg_a, avg_r in dec.tuples():
        known = OfferStyle(st) if st in {x.value for x in STYLES} else None
        n_edits, n_offers = edits.get(st, (0, 0))
        by_style[st] = OfferFeedbackRow(
            style=known, label=label_of(known) if known else "?", approve=int(a), reject=int(r), skip=int(sk),
            approval_rate=stats.rate(int(a), int(a) + int(r)),
            avg_personalization_approved=Decimal(avg_a).quantize(SCORE_Q) if avg_a is not None else None,
            avg_personalization_rejected=Decimal(avg_r).quantize(SCORE_Q) if avg_r is not None else None,
            edits=n_edits, edited_offers=n_offers,
        )
    for st, (n_edits, n_offers) in edits.items():
        if st not in by_style:
            known = OfferStyle(st) if st in {x.value for x in STYLES} else None
            by_style[st] = OfferFeedbackRow(
                style=known, label=label_of(known) if known else "?", approve=0, reject=0, skip=0,
                approval_rate=None, avg_personalization_approved=None, avg_personalization_rejected=None,
                edits=n_edits, edited_offers=n_offers,
            )
    return sorted(by_style.values(), key=lambda r: r.label)


async def _lead_bands(db: AsyncSession, s: Scope) -> list[LeadScoreBand]:
    e = FeedbackEvent
    where = _event_where(s, FeedbackKind.LEAD_STATUS)
    if s.project_id is not None:
        where.append(e.target_id.in_(select(Lead.id).where(*_lead_scope(s))))
    counts: dict[str, list[int]] = {b.key: [0, 0] for b in stats.LEAD_SCORE_BANDS}
    for to, score in (
        await db.execute(select(e.payload["to"].astext, e.payload["lead_score"].astext).where(*where))
    ).tuples():
        if score is None or to is None:
            continue
        b = stats.bucket_of(Decimal(score), stats.LEAD_SCORE_BANDS)
        if b is None:
            continue
        if to in SELECTING_STATUSES:
            counts[b.key][0] += 1
        elif to == LeadStatus.REJECTED.value:
            counts[b.key][1] += 1
    return [
        LeadScoreBand(
            key=b.key, label=b.label, low=b.low, high=b.high, selected=counts[b.key][0],
            rejected=counts[b.key][1], selection_rate=stats.rate(counts[b.key][0], sum(counts[b.key])),
        )
        for b in stats.LEAD_SCORE_BANDS
    ]


async def ai_feedback_report(db: AsyncSession, s: Scope) -> AIFeedbackReport:
    return AIFeedbackReport(
        period=s.out,
        generation_by_strategy=await _generation_rows(db, s, GeneratedThumbnail.strategy),
        generation_by_model=await _generation_rows(db, s, GeneratedThumbnail.model_key),
        offers_by_style=await _offer_rows(db, s),
        lead_decisions_by_score=await _lead_bands(db, s),
        scores=await feedback.summary(db, s.workspace_id),
    )


# ---------------------------------------------------------------------------
# Overview / Dashboard (ТЗ §39)
# ---------------------------------------------------------------------------
async def overview(db: AsyncSession, s: Scope) -> Overview:
    facts_map = await _lead_facts(db, s)
    facts = list(facts_map.values())
    total = await db.scalar(select(func.count()).select_from(Lead).where(*_lead_scope(s))) or 0
    pending_where = [Offer.workspace_id == s.workspace_id, Offer.status == OfferStatus.PENDING_APPROVAL]
    if s.project_id is not None:
        pending_where.append(Offer.lead_id.in_(select(Lead.id).where(*_lead_scope(s))))
    pending = await db.scalar(select(func.count()).select_from(Offer).where(*pending_where)) or 0

    def reached(level: int) -> int:
        return sum(1 for f in facts if f.level >= level)

    sent, replies, positive = reached(stats.SENT), reached(stats.REPLIED), reached(stats.POSITIVE)
    tiles = Tiles(
        total_channels=int(total), new_channels=len(facts), analyzed=reached(stats.ANALYZED),
        selected=reached(stats.SELECTED), generated_samples=sum(1 for f in facts if f.sample),
        offers_pending_approval=int(pending), sent=sent, replies=replies, positive_replies=positive,
        reply_rate=stats.rate(replies, sent), conversion=stats.rate(positive, sent),
    )
    costs = _cost_summary(await _cost_by_task(db, s), facts)
    notes = [
        "Лиды — найденные за период и их текущее состояние; расходы — вызовы AI за период; кампании и "
        "варианты — письма, отправленные за период.",
        f"«Лучшие» — по доле ответов среди групп, где отправлено не меньше {MIN_SAMPLE}; "
        "иначе «мало данных».",
    ]
    if costs.unpriced_calls:
        notes.append(f"У {costs.unpriced_calls} вызовов AI нет цены: расходы — нижняя граница.")
    return Overview(
        period=s.out, tiles=tiles, funnel=_funnel(facts), costs=costs,
        best_niche=_best_niche(await _niche_rows(db, s, facts_map)),
        best_score_range=_best_range(_score_rows(facts)),
        best_campaign=_best_campaign(await _campaign_rows(db, s)),
        best_offer_variant=_best_variant(await ab_report(db, s, "style")),
        min_sample=MIN_SAMPLE, notes=notes,
    )
