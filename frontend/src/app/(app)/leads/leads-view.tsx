"use client";

import { ArrowDown, ArrowUp, ChevronDown, ChevronRight, RefreshCw, Settings2, X } from "lucide-react";
import Link from "next/link";
import { Fragment, useCallback, useEffect, useMemo, useState } from "react";

import { LeadScoreBadge, LeadScoreBreakdown } from "@/components/lead-score";
import { FavoriteStar, HumanScore, LeadStatusActions, LeadTagsEditor, TagChips } from "@/components/lead-workflow";
import { EmptyState, ErrorState, InlineError, LoadingState } from "@/components/states";
import { DemoBadge } from "@/components/status-badges";
import { Badge, Button, Card, Input, PageHeader, Pager, Select } from "@/components/ui";
import { LEVEL_LABELS, formatScore, opportunityTone, scoreTone } from "@/lib/analysis";
import { LEADS_PAGE, useLeads, useProjects, useSyncLeads, useTaxonomy, type Lead } from "@/lib/api/hooks";
import { useBulkLeads, useTags } from "@/lib/api/workflow-hooks";
import {
  CONTACT_TYPES,
  CONTACT_TYPE_LABELS,
  contactabilityTone,
  formatContactScore,
} from "@/lib/contacts";
import { formatCount, formatDate } from "@/lib/format";
import {
  ACTIVITY_LABELS,
  ACTIVITY_LEVELS,
  CLEAR_LEAD_FILTERS,
  LEAD_SORTS,
  defaultLeadOrder,
  formatLeadScore,
  LEAD_STATUSES,
  SORT_LABELS,
  STATUS_LABELS,
  hasLeadFilters,
  leadQueryFromParams,
  nextLeadSort,
  type LeadQuery,
} from "@/lib/leads";
import { taxonomyOptions } from "@/lib/taxonomy";
import { useUrlState, type UrlPatch } from "@/lib/url-state";
import { cn } from "@/lib/utils";
import {
  BULK_STATUS_LABELS,
  EMPTY_SELECTION,
  MANUAL_STATUSES,
  MAX_BULK_LEADS,
  filtersFromQuery,
  humanValue,
  isSelected,
  selectedCount,
  selectionBody,
  toggleOne,
  togglePage,
  type Selection,
} from "@/lib/workflow";

import { ScoringSettings } from "./scoring-settings";

export function LeadsView() {
  const [params, setParams] = useUrlState();
  const query = useMemo(() => leadQueryFromParams(params), [params]);
  const leads = useLeads(query);
  const sync = useSyncLeads();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const filtered = hasLeadFilters(query);
  // The selection belongs to one set of filters: changing them (not the page or the sort) clears it.
  const filterKey = JSON.stringify(filtersFromQuery(query));
  const [selection, setSelection] = useState<Selection>(EMPTY_SELECTION);
  const [selectionKey, setSelectionKey] = useState(filterKey);
  if (selectionKey !== filterKey) {
    setSelectionKey(filterKey);
    setSelection(EMPTY_SELECTION);
  }
  // Any filter change starts from the first page.
  const change = useCallback(
    (patch: UrlPatch) => setParams({ ...patch, offset: "offset" in patch ? patch.offset : null }),
    [setParams],
  );
  const s = leads.data?.summary;

  return (
    <div>
      <PageHeader
        title="Лиды"
        subtitle={
          s
            ? `Всего ${formatCount(s.leads)} · с баллом ${formatCount(s.scored)} · средний Lead Score ${
                s.avg_lead_score === null ? "—" : Math.round(Number(s.avg_lead_score))
              }`
            : "Каналы проектов как потенциальные клиенты: объяснимый Lead Score без AI"
        }
        actions={
          <>
            <Button variant="secondary" aria-expanded={settingsOpen} onClick={() => setSettingsOpen((v) => !v)}>
              <Settings2 className="size-4" aria-hidden /> Веса и профиль лида
            </Button>
            <Button
              variant="secondary"
              disabled={sync.isPending}
              title="Создать лиды для новых каналов и пересчитать баллы (бесплатно, без AI)"
              onClick={() => sync.mutate(query.project_id)}
            >
              <RefreshCw className={cn("size-4", sync.isPending && "animate-spin")} aria-hidden /> Пересчитать
            </Button>
          </>
        }
      />
      {sync.data ? (
        <p role="status" className="mb-2 text-xs text-zinc-500">
          Новых лидов: {sync.data.leads_created} · проанализировано: {sync.data.leads_analyzed} · баллы изменились:{" "}
          {sync.data.scores_changed}, без изменений: {sync.data.scores_unchanged}
        </p>
      ) : null}
      <InlineError error={sync.error} />
      {settingsOpen ? <ScoringSettings /> : null}

      <Filters query={query} onChange={change} filtered={filtered} />
      {s && Object.keys(s.by_status).length > 0 ? (
        <div className="mb-3 flex flex-wrap items-center gap-1.5 text-xs">
          {LEAD_STATUSES.filter((st) => s.by_status[st]).map((st) => (
            <button
              key={st}
              type="button"
              onClick={() => change({ status: query.status?.length === 1 && query.status[0] === st ? null : st })}
              className={cn(
                "rounded border px-1.5 py-0.5",
                query.status?.includes(st)
                  ? "border-zinc-900 bg-zinc-900 text-white dark:border-zinc-100 dark:bg-zinc-100 dark:text-zinc-900"
                  : "border-zinc-200 hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800",
              )}
            >
              {STATUS_LABELS[st]}: {s.by_status[st]}
            </button>
          ))}
        </div>
      ) : null}

      {leads.isPending ? (
        <LoadingState />
      ) : leads.isError ? (
        <ErrorState error={leads.error} onRetry={() => leads.refetch()} />
      ) : leads.data.items.length === 0 ? (
        <EmptyState
          title={filtered ? "Нет лидов под эти фильтры" : "Лидов пока нет"}
          hint={
            filtered
              ? "Ослабьте фильтры или сбросьте их."
              : "Лиды создаются автоматически после поиска каналов и анализа превью. Если каналы уже есть — нажмите «Пересчитать»."
          }
          action={
            filtered ? (
              <Button variant="secondary" onClick={() => setParams(CLEAR_LEAD_FILTERS)}>
                Сбросить фильтры
              </Button>
            ) : undefined
          }
        />
      ) : (
        <>
          <BulkBar
            selection={selection}
            total={leads.data.total}
            query={query}
            onSelectionChange={setSelection}
          />
          <LeadsTable
            leads={leads.data.items}
            query={query}
            dimmed={leads.isPlaceholderData}
            onSort={(p) => setParams(p)}
            selection={selection}
            onSelectionChange={setSelection}
          />
          <Pager
            offset={query.offset}
            limit={LEADS_PAGE}
            total={leads.data.total}
            onChange={(offset) => setParams({ offset: offset || null })}
          />
        </>
      )}
    </div>
  );
}

/** Selection summary and bulk actions (ТЗ §11): chosen rows or «all filtered» minus unticked rows. */
function BulkBar({
  selection,
  total,
  query,
  onSelectionChange,
}: {
  selection: Selection;
  total: number;
  query: LeadQuery;
  onSelectionChange: (s: Selection) => void;
}) {
  const bulk = useBulkLeads();
  const tags = useTags();
  const userTags = (tags.data ?? []).filter((t) => !t.is_system);
  const [reason, setReason] = useState("");
  const count = selectedCount(selection, total);
  const tooMany = count > MAX_BULK_LEADS;
  const run = (action: Parameters<typeof bulk.mutate>[0]["action"]) =>
    bulk.mutate({ selection: selectionBody(selection, query), action });

  return (
    <div
      className={cn(
        "mb-2 flex flex-wrap items-center gap-2 rounded-lg border px-3 py-2 text-sm",
        count > 0
          ? "border-blue-200 bg-blue-50/60 dark:border-blue-900 dark:bg-blue-950/30"
          : "border-zinc-200 dark:border-zinc-800",
      )}
    >
      <span className="tabular-nums">
        {count > 0 ? (
          <>
            Выбрано: <b>{count}</b>
            {selection.mode === "all" ? " (все отфильтрованные)" : null}
          </>
        ) : (
          <span className="text-zinc-500">Отметьте лиды или выберите все отфильтрованные</span>
        )}
      </span>
      {selection.mode === "ids" && total > 0 ? (
        <Button size="sm" variant="ghost" onClick={() => onSelectionChange({ mode: "all", excluded: new Set() })}>
          Выбрать все отфильтрованные ({total})
        </Button>
      ) : null}
      {count > 0 ? (
        <Button size="sm" variant="ghost" onClick={() => onSelectionChange(EMPTY_SELECTION)}>
          <X className="size-3.5" aria-hidden /> Снять выбор
        </Button>
      ) : null}
      {count > 0 ? (
        <>
          <span className="mx-1 h-5 w-px bg-zinc-300 dark:bg-zinc-700" aria-hidden />
          {MANUAL_STATUSES.filter((s) => s !== "rejected").map((s) => (
            <Button
              key={s}
              size="sm"
              variant={s === "selected" ? "primary" : "secondary"}
              disabled={bulk.isPending || tooMany}
              onClick={() => run({ type: "set_status", status: s, reason: "" })}
            >
              {BULK_STATUS_LABELS[s]}
            </Button>
          ))}
          <Input
            aria-label="Причина отклонения для выбранных"
            placeholder="причина отклонения"
            value={reason}
            maxLength={500}
            onChange={(e) => setReason(e.target.value)}
            className="h-7 w-44 text-xs"
          />
          <Button
            size="sm"
            variant="danger"
            disabled={bulk.isPending || tooMany}
            onClick={() => run({ type: "set_status", status: "rejected", reason: reason.trim() })}
          >
            {BULK_STATUS_LABELS.rejected}
          </Button>
          <Select
            aria-label="Тег для выбранных"
            value=""
            disabled={bulk.isPending || tooMany || userTags.length === 0}
            onChange={(e) => {
              const [type, id] = e.target.value.split(":");
              if (id) run({ type: type as "add_tag" | "remove_tag", tag_id: Number(id) });
            }}
            className="h-7 w-44 py-0 text-xs"
          >
            <option value="">{userTags.length ? "Тег…" : "Нет своих тегов"}</option>
            {userTags.map((t) => (
              <option key={`a${t.id}`} value={`add_tag:${t.id}`}>
                + {t.name}
              </option>
            ))}
            {userTags.map((t) => (
              <option key={`r${t.id}`} value={`remove_tag:${t.id}`}>
                − {t.name}
              </option>
            ))}
          </Select>
          <Button size="sm" variant="secondary" disabled={bulk.isPending || tooMany} onClick={() => run({ type: "favorite" })}>
            ★ В избранное
          </Button>
          <Button size="sm" variant="ghost" disabled={bulk.isPending || tooMany} onClick={() => run({ type: "unfavorite" })}>
            ☆ Из избранного
          </Button>
        </>
      ) : null}
      {tooMany ? (
        <span className="text-xs text-red-600">Не больше {MAX_BULK_LEADS} лидов за раз — сузьте фильтры.</span>
      ) : null}
      {bulk.data ? (
        <span role="status" className="text-xs text-zinc-600 dark:text-zinc-400">
          Изменено: {bulk.data.changed} · без изменений: {bulk.data.unchanged}
          {bulk.data.failed ? (
            <span className="text-red-600" title={bulk.data.failures.map((f) => `#${f.lead_id}: ${f.error}`).join("\n")}>
              {" "}
              · не применимо: {bulk.data.failed} (переход статуса не разрешён)
            </span>
          ) : null}
        </span>
      ) : null}
      <InlineError error={bulk.error} />
    </div>
  );
}

const COLUMNS: { label: string; sort?: LeadQuery["sort"]; numeric?: boolean }[] = [
  { label: "" },
  { label: "" },
  { label: "Канал" },
  { label: "Статус" },
  { label: "Lead", sort: "lead_score", numeric: true },
  { label: "Приоритет", sort: "priority", numeric: true },
  { label: "Превью", sort: "thumbnail_score", numeric: true },
  { label: "Возможность", sort: "visual_opportunity" },
  { label: "Подписчики", sort: "subscribers", numeric: true },
  { label: "Ср. просм.", sort: "views", numeric: true },
  { label: "Активность", sort: "activity" },
  { label: "Контакты", sort: "contactability", numeric: true },
  { label: "Посл. видео", sort: "last_video" },
];

function LeadsTable({
  leads,
  query,
  dimmed,
  onSort,
  selection,
  onSelectionChange,
}: {
  leads: Lead[];
  query: LeadQuery;
  dimmed: boolean;
  onSort: (patch: UrlPatch) => void;
  selection: Selection;
  onSelectionChange: (s: Selection) => void;
}) {
  const pageIds = leads.map((l) => l.id);
  const pageOn = pageIds.filter((id) => isSelected(selection, id)).length;
  const [open, setOpen] = useState<Set<number>>(new Set());
  const toggle = (id: number) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <div className="overflow-x-auto rounded-lg border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
      <table className={cn("w-full text-sm", dimmed && "opacity-60")}>
        <thead className="bg-zinc-50 text-left text-xs uppercase tracking-wide text-zinc-500 dark:bg-zinc-900">
          <tr>
            {COLUMNS.map((c, i) => {
              const sorted = c.sort && query.sort === c.sort ? query.order : undefined;
              return (
                <th
                  key={i}
                  scope="col"
                  aria-sort={sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : undefined}
                  className={cn("px-3 py-2 font-medium", c.numeric && "text-right", i === 0 && "w-8 px-2")}
                >
                  {i === 0 ? (
                    <input
                      type="checkbox"
                      aria-label="Выбрать все лиды на странице"
                      checked={pageIds.length > 0 && pageOn === pageIds.length}
                      ref={(el) => {
                        if (el) el.indeterminate = pageOn > 0 && pageOn < pageIds.length;
                      }}
                      onChange={() => onSelectionChange(togglePage(selection, pageIds))}
                      className="size-4 align-middle"
                    />
                  ) : c.sort ? (
                    <button
                      type="button"
                      onClick={() => onSort(nextLeadSort(query, c.sort!))}
                      className={cn("inline-flex items-center gap-1 uppercase hover:text-zinc-900 dark:hover:text-zinc-100", sorted && "text-zinc-900 dark:text-zinc-100")}
                    >
                      {c.label}
                      {sorted === "asc" ? <ArrowUp className="size-3" aria-hidden /> : null}
                      {sorted === "desc" ? <ArrowDown className="size-3" aria-hidden /> : null}
                    </button>
                  ) : (
                    c.label
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
          {leads.map((lead) => (
            <Fragment key={lead.id}>
              <LeadRow
                lead={lead}
                expanded={open.has(lead.id)}
                onToggle={() => toggle(lead.id)}
                selected={isSelected(selection, lead.id)}
                onSelect={() => onSelectionChange(toggleOne(selection, lead.id))}
              />
              {open.has(lead.id) ? (
                <tr>
                  <td colSpan={COLUMNS.length} className="bg-zinc-50/60 px-4 py-3 dark:bg-zinc-800/30">
                    <LeadDetails lead={lead} />
                  </td>
                </tr>
              ) : null}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Expanded row: manual decision, tags, human scores (ТЗ §47) and the explainable breakdown. */
function LeadDetails({ lead }: { lead: Lead }) {
  const s = lead.score;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-start gap-x-8 gap-y-2">
        <div>
          <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-zinc-500">Решение</p>
          <LeadStatusActions lead={lead} />
          {lead.rejected_reason ? (
            <p className="mt-1 text-xs text-zinc-500">Причина отклонения: {lead.rejected_reason}</p>
          ) : null}
        </div>
        <div>
          <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-zinc-500">Теги</p>
          <LeadTagsEditor lead={lead} />
        </div>
        {s ? (
          <div className="space-y-1">
            <p className="text-xs font-semibold uppercase tracking-wide text-zinc-500">Моя оценка</p>
            <HumanScore
              target="lead"
              targetId={lead.id}
              field="total"
              aiValue={s.total}
              overrides={lead.overrides}
              min={0}
              max={100}
              label="Lead Score"
              format={formatLeadScore}
            />
            <HumanScore
              target="lead"
              targetId={lead.id}
              field="priority_score"
              aiValue={s.priority_score}
              overrides={lead.overrides}
              min={0}
              max={100}
              label="Приоритет"
              format={formatLeadScore}
            />
          </div>
        ) : null}
      </div>
      {s ? (
        <LeadScoreBreakdown score={s} />
      ) : (
        <p className="text-sm text-zinc-500">Балл ещё не посчитан — нажмите «Пересчитать».</p>
      )}
    </div>
  );
}

/** The value that sorts and filters: the human one where set (ТЗ §47), marked with «я». */
function EffectiveScore({ lead, field, label }: { lead: Lead; field: "total" | "priority_score"; label?: string }) {
  const human = humanValue(lead.overrides, field);
  if (!human) return <LeadScoreBadge score={lead.score?.[field]} label={label} />;
  return (
    <span title={`Ручная оценка; AI / код: ${formatLeadScore(lead.score?.[field])}`} className="inline-flex items-center gap-1">
      <span className="text-[10px] text-zinc-500">я</span>
      <LeadScoreBadge score={human.human_value} label={label} />
    </span>
  );
}

function LeadRow({
  lead,
  expanded,
  onToggle,
  selected,
  onSelect,
}: {
  lead: Lead;
  expanded: boolean;
  onToggle: () => void;
  selected: boolean;
  onSelect: () => void;
}) {
  const c = lead.channel;
  const s = lead.score;
  return (
    <tr className={cn("hover:bg-zinc-50 dark:hover:bg-zinc-800/50", selected && "bg-blue-50/50 dark:bg-blue-950/20")}>
      <td className="w-8 px-2">
        <input
          type="checkbox"
          aria-label={`Выбрать лид «${c.title}»`}
          checked={selected}
          onChange={onSelect}
          className="size-4 align-middle"
        />
      </td>
      <td className="w-8 px-2">
        <button
          type="button"
          aria-expanded={expanded}
          aria-label={expanded ? "Скрыть разбор балла" : "Показать разбор балла"}
          onClick={onToggle}
          className="rounded p-1 text-zinc-500 hover:bg-zinc-200 dark:hover:bg-zinc-700"
        >
          {expanded ? <ChevronDown className="size-4" aria-hidden /> : <ChevronRight className="size-4" aria-hidden />}
        </button>
      </td>
      <td className="max-w-80 px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <FavoriteStar type="channel" id={c.id} on={lead.is_favorite} label={c.title} />
          {c.avatar_url ? (
            // External YouTube avatar; next/image would proxy it through the server for no benefit.
            // eslint-disable-next-line @next/next/no-img-element
            <img src={c.avatar_url} alt="" referrerPolicy="no-referrer" loading="lazy" className="size-8 shrink-0 rounded-full bg-zinc-200" />
          ) : (
            <div className="size-8 shrink-0 rounded-full bg-zinc-200 dark:bg-zinc-700" aria-hidden />
          )}
          <div className="min-w-0">
            <div className="flex min-w-0 items-center gap-2">
              <Link href={`/channels/${c.id}`} className="truncate font-medium hover:underline" title={c.title}>
                {c.title}
              </Link>
              <DemoBadge show={c.is_demo} />
            </div>
            <TagChips tags={lead.tags} max={3} />
          </div>
        </div>
      </td>
      <td className="px-3 py-2 whitespace-nowrap" title={lead.rejected_reason ?? undefined}>
        {STATUS_LABELS[lead.status]}
      </td>
      <td className="px-3 py-2 text-right">
        <EffectiveScore lead={lead} field="total" />
      </td>
      <td className="px-3 py-2 text-right">
        <EffectiveScore lead={lead} field="priority_score" label="Priority" />
      </td>
      <td className="px-3 py-2 text-right tabular-nums">
        {s?.avg_thumbnail_score ? <Badge tone={scoreTone(s.avg_thumbnail_score)}>{formatScore(s.avg_thumbnail_score)}</Badge> : "—"}
      </td>
      <td className="px-3 py-2">
        {lead.visual_opportunity ? (
          <Badge tone={opportunityTone(lead.visual_opportunity)}>{LEVEL_LABELS[lead.visual_opportunity]}</Badge>
        ) : (
          "—"
        )}
      </td>
      <td className="px-3 py-2 text-right tabular-nums">{c.subscribers_hidden ? "скрыто" : formatCount(c.subscriber_count)}</td>
      <td className="px-3 py-2 text-right tabular-nums">{formatCount(c.metrics?.avg_views)}</td>
      <td className="px-3 py-2">{lead.activity_level ? ACTIVITY_LABELS[lead.activity_level] : "—"}</td>
      <td className="px-3 py-2 text-right tabular-nums">
        {s?.contactability_score === null || s?.contactability_score === undefined ? (
          <span title="Контакты канала ещё не собирались">—</span>
        ) : (
          <Badge tone={contactabilityTone(s.contactability_score)} title="Contactability Score (0–100)">
            {formatContactScore(s.contactability_score)}
          </Badge>
        )}
      </td>
      <td className="px-3 py-2 whitespace-nowrap">{formatDate(c.metrics?.last_video_at)}</td>
    </tr>
  );
}

type NumberKey =
  | "min_thumbnail_score"
  | "max_thumbnail_score"
  | "min_lead_score"
  | "max_lead_score"
  | "min_priority"
  | "min_contactability"
  | "min_subscribers"
  | "max_subscribers"
  | "min_avg_views"
  | "max_days_since_last_upload";

const NUMBER_FILTERS: { name: NumberKey; label: string; step?: string; min: number; max?: number }[] = [
  { name: "min_thumbnail_score", label: "Превью от (1–10)", step: "0.1", min: 1, max: 10 },
  { name: "max_thumbnail_score", label: "Превью до (1–10)", step: "0.1", min: 1, max: 10 },
  { name: "min_lead_score", label: "Lead Score от", min: 0, max: 100 },
  { name: "max_lead_score", label: "Lead Score до", min: 0, max: 100 },
  { name: "min_priority", label: "Приоритет от", min: 0, max: 100 },
  { name: "min_contactability", label: "Контактность от (0–100)", min: 0, max: 100 },
  { name: "min_subscribers", label: "Подписчиков от", min: 0 },
  { name: "max_subscribers", label: "Подписчиков до", min: 0 },
  { name: "min_avg_views", label: "Ср. просмотров от", min: 0 },
  { name: "max_days_since_last_upload", label: "Посл. видео ≤ дн.", min: 0 },
];

function Filters({ query, onChange, filtered }: { query: LeadQuery; onChange: (p: UrlPatch) => void; filtered: boolean }) {
  const projects = useProjects({ offset: 0 });
  const taxonomy = useTaxonomy();
  const tags = useTags();
  const niches = useMemo(() => taxonomyOptions(taxonomy.data ?? []), [taxonomy.data]);
  const [q, setQ] = useState(query.q ?? "");
  const [syncedQ, setSyncedQ] = useState(query.q);
  if (syncedQ !== query.q) {
    setSyncedQ(query.q);
    setQ(query.q ?? "");
  }
  useEffect(() => {
    const value = q.trim();
    if (value === (query.q ?? "")) return;
    const t = setTimeout(() => onChange({ q: value || null }), 300);
    return () => clearTimeout(t);
  }, [q, query.q, onChange]);

  return (
    <Card className="mb-3 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          type="search"
          aria-label="Поиск по названию"
          placeholder="Поиск по названию"
          value={q}
          maxLength={200}
          onChange={(e) => setQ(e.target.value)}
          className="w-56"
        />
        <Select aria-label="Проект" value={query.project_id ?? ""} onChange={(e) => onChange({ project_id: e.target.value || null })} className="w-48">
          <option value="">Все проекты</option>
          {projects.data?.items.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Статус"
          value={query.status?.length === 1 ? query.status[0] : ""}
          onChange={(e) => onChange({ status: e.target.value || null })}
          className="w-44"
        >
          <option value="">Все статусы</option>
          {LEAD_STATUSES.map((st) => (
            <option key={st} value={st}>
              {STATUS_LABELS[st]}
            </option>
          ))}
        </Select>
        <Select aria-label="Активность не ниже" value={query.activity ?? ""} onChange={(e) => onChange({ activity: e.target.value || null })} className="w-44">
          <option value="">Активность: любая</option>
          {ACTIVITY_LEVELS.map((a) => (
            <option key={a} value={a}>
              не ниже: {ACTIVITY_LABELS[a]}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Визуальная возможность канала"
          value={query.visual_opportunity ?? ""}
          onChange={(e) => onChange({ visual_opportunity: e.target.value || null })}
          className="w-48"
        >
          <option value="">Возможность: любая</option>
          {(["high", "medium", "low"] as const).map((l) => (
            <option key={l} value={l}>
              возможность: {LEVEL_LABELS[l]}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Сортировка"
          value={query.sort}
          onChange={(e) => {
            const sort = e.target.value as LeadQuery["sort"];
            onChange({ sort, order: defaultLeadOrder(sort) });
          }}
          className="w-52"
        >
          {LEAD_SORTS.map((s) => (
            <option key={s} value={s}>
              ↕ {SORT_LABELS[s]}
            </option>
          ))}
        </Select>
        <Button variant="ghost" size="sm" onClick={() => onChange({ order: query.order === "asc" ? "desc" : "asc" })}>
          {query.order === "asc" ? <ArrowUp className="size-4" aria-hidden /> : <ArrowDown className="size-4" aria-hidden />}
          {query.order === "asc" ? "по возрастанию" : "по убыванию"}
        </Button>
        {filtered ? (
          <Button variant="ghost" size="sm" onClick={() => onChange(CLEAR_LEAD_FILTERS)}>
            <X className="size-4" aria-hidden /> Сбросить
          </Button>
        ) : null}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {NUMBER_FILTERS.map((f) => (
          <NumberFilter key={f.name} query={query} {...f} onChange={onChange} />
        ))}
        <Select
          multiple
          aria-label="Ниши"
          title="Ниши (Ctrl/Cmd — несколько); ниша включает свои темы"
          value={(query.niche ?? []).map(String)}
          onChange={(e) =>
            onChange({ niche: Array.from(e.currentTarget.selectedOptions, (o) => o.value).join(",") || null })
          }
          className="h-16 w-52 py-1"
        >
          {niches.length === 0 ? <option disabled>Ниш нет</option> : null}
          {niches.map((o) => (
            <option key={o.id} value={o.id}>
              {o.label}
            </option>
          ))}
        </Select>
        <Select
          multiple
          aria-label="Теги"
          title="Теги (Ctrl/Cmd — несколько): лид с любым из них"
          value={(query.tag ?? []).map(String)}
          onChange={(e) =>
            onChange({ tag: Array.from(e.currentTarget.selectedOptions, (o) => o.value).join(",") || null })
          }
          className="h-16 w-44 py-1"
        >
          {(tags.data ?? []).length === 0 ? <option disabled>Тегов нет</option> : null}
          {(tags.data ?? []).map((t) => (
            <option key={t.id} value={t.id}>
              {t.name} ({t.leads})
            </option>
          ))}
        </Select>
        <Select
          multiple
          aria-label="Типы контактов"
          title="Есть пригодный контакт любого из выбранных типов (Ctrl/Cmd — несколько)"
          value={query.contact_type ?? []}
          onChange={(e) =>
            onChange({ contact_type: Array.from(e.currentTarget.selectedOptions, (o) => o.value).join(",") || null })
          }
          className="h-16 w-44 py-1"
        >
          {CONTACT_TYPES.map((t) => (
            <option key={t} value={t}>
              {CONTACT_TYPE_LABELS[t]}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Избранное"
          value={query.favorite === undefined ? "" : String(query.favorite)}
          onChange={(e) => onChange({ favorite: e.target.value || null })}
          className="w-44"
        >
          <option value="">Избранное: все</option>
          <option value="true">★ только избранные</option>
          <option value="false">кроме избранных</option>
        </Select>
      </div>
    </Card>
  );
}

/** Uncontrolled number box: commits to the URL on blur / Enter (each URL change is a request). */
function NumberFilter({
  query,
  name,
  label,
  step,
  min,
  max,
  onChange,
}: {
  query: LeadQuery;
  name: NumberKey;
  label: string;
  step?: string;
  min: number;
  max?: number;
  onChange: (p: UrlPatch) => void;
}) {
  const value = query[name];
  const commit = (raw: string) => onChange({ [name]: raw.trim() || null });
  return (
    <Input
      key={`${name}-${value ?? ""}`}
      type="number"
      min={min}
      max={max}
      step={step}
      inputMode={step ? "decimal" : "numeric"}
      aria-label={label}
      placeholder={label}
      title={label}
      defaultValue={value ?? ""}
      className="w-40"
      onBlur={(e) => commit(e.currentTarget.value)}
      onKeyDown={(e) => e.key === "Enter" && commit(e.currentTarget.value)}
    />
  );
}
