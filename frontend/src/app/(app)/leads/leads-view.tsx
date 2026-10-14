"use client";

import { ArrowDown, ArrowUp, ChevronDown, ChevronRight, RefreshCw, Settings2, X } from "lucide-react";
import Link from "next/link";
import { Fragment, useCallback, useEffect, useMemo, useState } from "react";

import { LeadScoreBadge, LeadScoreBreakdown } from "@/components/lead-score";
import { EmptyState, ErrorState, InlineError, LoadingState } from "@/components/states";
import { DemoBadge } from "@/components/status-badges";
import { Badge, Button, Card, Input, PageHeader, Pager, Select } from "@/components/ui";
import { LEVEL_LABELS, formatScore, opportunityTone, scoreTone } from "@/lib/analysis";
import { LEADS_PAGE, useLeads, useProjects, useSyncLeads, useTaxonomy, type Lead } from "@/lib/api/hooks";
import { formatCount, formatDate } from "@/lib/format";
import {
  ACTIVITY_LABELS,
  ACTIVITY_LEVELS,
  CLEAR_LEAD_FILTERS,
  LEAD_SORTS,
  defaultLeadOrder,
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

import { ScoringSettings } from "./scoring-settings";

export function LeadsView() {
  const [params, setParams] = useUrlState();
  const query = useMemo(() => leadQueryFromParams(params), [params]);
  const leads = useLeads(query);
  const sync = useSyncLeads();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const filtered = hasLeadFilters(query);
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
          <LeadsTable leads={leads.data.items} query={query} dimmed={leads.isPlaceholderData} onSort={(p) => setParams(p)} />
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

const COLUMNS: { label: string; sort?: LeadQuery["sort"]; numeric?: boolean }[] = [
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
  { label: "Посл. видео", sort: "last_video" },
];

function LeadsTable({
  leads,
  query,
  dimmed,
  onSort,
}: {
  leads: Lead[];
  query: LeadQuery;
  dimmed: boolean;
  onSort: (patch: UrlPatch) => void;
}) {
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
                  className={cn("px-3 py-2 font-medium", c.numeric && "text-right")}
                >
                  {c.sort ? (
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
              <LeadRow lead={lead} expanded={open.has(lead.id)} onToggle={() => toggle(lead.id)} />
              {open.has(lead.id) ? (
                <tr>
                  <td colSpan={COLUMNS.length} className="bg-zinc-50/60 px-4 py-3 dark:bg-zinc-800/30">
                    {lead.score ? (
                      <LeadScoreBreakdown score={lead.score} />
                    ) : (
                      <p className="text-sm text-zinc-500">Балл ещё не посчитан — нажмите «Пересчитать».</p>
                    )}
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

function LeadRow({ lead, expanded, onToggle }: { lead: Lead; expanded: boolean; onToggle: () => void }) {
  const c = lead.channel;
  const s = lead.score;
  return (
    <tr className="hover:bg-zinc-50 dark:hover:bg-zinc-800/50">
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
      <td className="max-w-72 px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          {c.avatar_url ? (
            // External YouTube avatar; next/image would proxy it through the server for no benefit.
            // eslint-disable-next-line @next/next/no-img-element
            <img src={c.avatar_url} alt="" referrerPolicy="no-referrer" loading="lazy" className="size-8 shrink-0 rounded-full bg-zinc-200" />
          ) : (
            <div className="size-8 shrink-0 rounded-full bg-zinc-200 dark:bg-zinc-700" aria-hidden />
          )}
          <Link href={`/channels/${c.id}`} className="truncate font-medium hover:underline" title={c.title}>
            {c.title}
          </Link>
          <DemoBadge show={c.is_demo} />
        </div>
      </td>
      <td className="px-3 py-2 whitespace-nowrap">{STATUS_LABELS[lead.status]}</td>
      <td className="px-3 py-2 text-right">
        <LeadScoreBadge score={s?.total} />
      </td>
      <td className="px-3 py-2 text-right">
        <LeadScoreBadge score={s?.priority_score} label="Priority" />
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
  { name: "min_subscribers", label: "Подписчиков от", min: 0 },
  { name: "max_subscribers", label: "Подписчиков до", min: 0 },
  { name: "min_avg_views", label: "Ср. просмотров от", min: 0 },
  { name: "max_days_since_last_upload", label: "Посл. видео ≤ дн.", min: 0 },
];

function Filters({ query, onChange, filtered }: { query: LeadQuery; onChange: (p: UrlPatch) => void; filtered: boolean }) {
  const projects = useProjects({ offset: 0 });
  const taxonomy = useTaxonomy();
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
