"use client";

import { createColumnHelper, tableFeatures, useTable } from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ArrowDown, ArrowUp, ExternalLink, SlidersHorizontal, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

import { EmptyState, ErrorState, InlineError, LoadingState } from "@/components/states";
import { DemoBadge } from "@/components/status-badges";
import { Button, Card, Input, PageHeader, Select } from "@/components/ui";
import {
  useChannels,
  useProject,
  useProjects,
  useTaxonomy,
  useUpdateProject,
  type Channel,
  type ChannelFilters,
  type ChannelSort,
  type TaxonomyNode,
} from "@/lib/api/hooks";
import { formatCount, formatDate, formatRatio } from "@/lib/format";
import { taxonomyOptions } from "@/lib/taxonomy";
import { useUrlState, type UrlPatch } from "@/lib/url-state";
import { cn } from "@/lib/utils";

import {
  CLEAR_FILTERS,
  FLOAT_FILTERS,
  INT_FILTERS,
  activeFilterCount,
  filterSetFromFilters,
  filtersFromParams,
  nextSort,
  patchFromFilterSet,
} from "./channel-filters";

const features = tableFeatures({});
const helper = createColumnHelper<typeof features, Channel>();
const EMPTY: Channel[] = [];
const ROW_HEIGHT = 52;

const columns = helper.columns([
  helper.accessor("title", {
    header: "Канал",
    cell: (info) => <ChannelCell channel={info.row.original} />,
  }),
  helper.accessor("subscriber_count", {
    header: "Подписчики",
    cell: (info) => (info.row.original.subscribers_hidden ? <span title="Скрыто владельцем">скрыто</span> : formatCount(info.getValue())),
  }),
  helper.accessor((c) => c.metrics?.avg_views ?? null, {
    id: "avg_views",
    header: "Ср. просм.",
    cell: (info) => formatCount(info.getValue()),
  }),
  helper.accessor((c) => c.metrics?.views_to_subs_ratio ?? null, {
    id: "views_ratio",
    header: "Просм./подп.",
    cell: (info) => formatRatio(info.getValue()),
  }),
  helper.accessor((c) => c.metrics?.last_video_at ?? null, {
    id: "last_video",
    header: "Посл. видео",
    cell: (info) => formatDate(info.getValue()),
  }),
  helper.accessor((c) => c.metrics?.videos_30d ?? null, {
    id: "videos_30d",
    header: "Видео/30д",
    cell: (info) => formatCount(info.getValue()),
  }),
  helper.accessor("video_count", { header: "Видео", cell: (info) => formatCount(info.getValue()) }),
  helper.accessor("country", { header: "Страна", cell: (info) => info.getValue() ?? "—" }),
]);

/** Column id -> layout + which server sort it maps to. */
const LAYOUT: Record<string, { width: string; numeric?: boolean; sort?: ChannelSort }> = {
  title: { width: "minmax(16rem,1fr)", sort: "title" },
  subscriber_count: { width: "7.5rem", numeric: true, sort: "subscribers" },
  avg_views: { width: "7.5rem", numeric: true, sort: "avg_views" },
  views_ratio: { width: "7.5rem", numeric: true, sort: "views_ratio" },
  last_video: { width: "8.5rem", sort: "last_video" },
  videos_30d: { width: "6.5rem", numeric: true, sort: "videos_30d" },
  video_count: { width: "5.5rem", numeric: true, sort: "videos" },
  country: { width: "4.5rem" },
};
const GRID = { gridTemplateColumns: Object.values(LAYOUT).map((l) => l.width).join(" ") };

export function ChannelsView() {
  const [params, setParams] = useUrlState();
  const filters = useMemo(() => filtersFromParams(params), [params]);
  const channels = useChannels(filters);
  const rows = useMemo(() => channels.data?.pages.flatMap((p) => p.items) ?? EMPTY, [channels.data]);
  const total = channels.data?.pages[0]?.total ?? 0;
  const hasFilters = Object.keys(CLEAR_FILTERS).some((k) => params.has(k));

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="Каналы"
        subtitle={channels.data ? `Найдено: ${formatCount(total)}` : "Каналы, найденные в проектах рабочего пространства"}
      />
      <FilterBar filters={filters} onChange={setParams} hasFilters={hasFilters} />

      {channels.isPending ? (
        <LoadingState />
      ) : channels.isError && rows.length === 0 ? (
        <ErrorState error={channels.error} onRetry={() => channels.refetch()} />
      ) : rows.length === 0 ? (
        <EmptyState
          title={hasFilters ? "Нет каналов под эти фильтры" : "Каналов пока нет"}
          hint={hasFilters ? "Ослабьте фильтры или сбросьте их." : "Каналы появятся после запуска поиска по запросам проекта."}
          action={
            hasFilters ? (
              <Button variant="secondary" onClick={() => setParams(CLEAR_FILTERS)}>
                Сбросить фильтры
              </Button>
            ) : undefined
          }
        />
      ) : (
        <ChannelsTable
          rows={rows}
          filters={filters}
          dimmed={channels.isPlaceholderData}
          onSort={(col) => setParams(nextSort(filters, col))}
          onReachEnd={() => {
            if (channels.hasNextPage && !channels.isFetchingNextPage && !channels.isError) channels.fetchNextPage();
          }}
          footer={
            channels.isFetchingNextPage
              ? "Загрузка…"
              : channels.isError
                ? "Не удалось загрузить продолжение списка."
                : channels.hasNextPage
                  ? `Показано ${rows.length} из ${total}`
                  : `Все ${rows.length}`
          }
        />
      )}
    </div>
  );
}

function ChannelsTable({
  rows,
  filters,
  dimmed,
  onSort,
  onReachEnd,
  footer,
}: {
  rows: Channel[];
  filters: ChannelFilters;
  dimmed: boolean;
  onSort: (col: ChannelSort) => void;
  onReachEnd: () => void;
  footer: string;
}) {
  const table = useTable({ features, columns, data: rows, getRowId: (c) => String(c.id) });
  const tableRows = table.getRowModel().rows;
  const scrollRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: tableRows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    getItemKey: (i) => tableRows[i].id,
    overscan: 10,
  });
  const items = virtualizer.getVirtualItems();
  const lastIndex = items.at(-1)?.index ?? 0;

  useEffect(() => {
    if (lastIndex >= tableRows.length - 20) onReachEnd();
  }, [lastIndex, tableRows.length, onReachEnd]);

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
      <div ref={scrollRef} role="table" aria-rowcount={tableRows.length} className="min-h-0 flex-1 overflow-auto text-sm">
        <div role="rowgroup" className="sticky top-0 z-10 bg-zinc-50 dark:bg-zinc-900">
          {table.getHeaderGroups().map((group) => (
            <div key={group.id} role="row" className="grid border-b border-zinc-200 dark:border-zinc-800" style={GRID}>
              {group.headers.map((header) => {
                const layout = LAYOUT[header.column.id];
                const sorted = layout.sort && filters.sort === layout.sort ? filters.order : undefined;
                return (
                  <div
                    key={header.id}
                    role="columnheader"
                    aria-sort={sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : undefined}
                    className={cn("px-3 py-2 text-xs font-medium uppercase tracking-wide text-zinc-500", layout.numeric && "text-right")}
                  >
                    {layout.sort ? (
                      <button
                        type="button"
                        onClick={() => onSort(layout.sort!)}
                        className={cn("inline-flex items-center gap-1 uppercase hover:text-zinc-900 dark:hover:text-zinc-100", sorted && "text-zinc-900 dark:text-zinc-100")}
                      >
                        <table.FlexRender header={header} />
                        {sorted === "asc" ? <ArrowUp className="size-3" aria-hidden /> : null}
                        {sorted === "desc" ? <ArrowDown className="size-3" aria-hidden /> : null}
                      </button>
                    ) : (
                      <table.FlexRender header={header} />
                    )}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
        <div role="rowgroup" className={cn("relative", dimmed && "opacity-60")} style={{ height: virtualizer.getTotalSize() }}>
          {items.map((item) => {
            const row = tableRows[item.index];
            return (
              <div
                key={row.id}
                role="row"
                aria-rowindex={item.index + 1}
                data-index={item.index}
                className="absolute left-0 grid w-full items-center border-b border-zinc-100 hover:bg-zinc-50 dark:border-zinc-800 dark:hover:bg-zinc-800/50"
                style={{ ...GRID, height: ROW_HEIGHT, transform: `translateY(${item.start}px)` }}
              >
                {row.getAllCells().map((cell) => (
                  <div
                    key={cell.id}
                    role="cell"
                    className={cn("truncate px-3", LAYOUT[cell.column.id].numeric && "text-right tabular-nums")}
                  >
                    <table.FlexRender cell={cell} />
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      </div>
      <div className="border-t border-zinc-200 px-3 py-1.5 text-xs text-zinc-500 dark:border-zinc-800">{footer}</div>
    </div>
  );
}

function ChannelCell({ channel: c }: { channel: Channel }) {
  return (
    <div className="flex min-w-0 items-center gap-2">
      {c.avatar_url ? (
        // External YouTube avatar; next/image would proxy it through the server for no benefit.
        // eslint-disable-next-line @next/next/no-img-element
        <img src={c.avatar_url} alt="" referrerPolicy="no-referrer" loading="lazy" className="size-8 shrink-0 rounded-full bg-zinc-200" />
      ) : (
        <div className="size-8 shrink-0 rounded-full bg-zinc-200 dark:bg-zinc-700" aria-hidden />
      )}
      <div className="min-w-0">
        <div className="flex items-center gap-1.5">
          <Link href={`/channels/${c.id}`} className="truncate font-medium hover:underline">
            {c.title}
          </Link>
          <DemoBadge show={c.is_demo} />
        </div>
        {c.is_demo ? (
          // Demo channels do not exist on YouTube; an external link would 404.
          <span className="text-xs text-zinc-500">{c.handle ?? c.youtube_channel_id}</span>
        ) : (
          <a
            href={c.url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-0.5 text-xs text-zinc-500 hover:underline"
          >
            {c.handle ?? c.youtube_channel_id} <ExternalLink className="size-3" aria-hidden />
          </a>
        )}
      </div>
    </div>
  );
}

function FilterBar({
  filters,
  onChange,
  hasFilters,
}: {
  filters: ChannelFilters;
  onChange: (patch: UrlPatch) => void;
  hasFilters: boolean;
}) {
  // All statuses, first page — enough for a picker in Phase 1.
  const projects = useProjects({ offset: 0 });
  const [q, setQ] = useState(filters.q ?? "");
  const searchRef = useRef<HTMLInputElement>(null);

  // Keep the box in sync when the URL changes elsewhere (back/forward, "reset").
  const [syncedQ, setSyncedQ] = useState(filters.q);
  if (syncedQ !== filters.q) {
    setSyncedQ(filters.q);
    setQ(filters.q ?? "");
  }

  // Debounce typing into the URL (each URL change is a new query).
  useEffect(() => {
    const value = q.trim();
    if (value === (filters.q ?? "")) return;
    const t = setTimeout(() => onChange({ q: value || null }), 300);
    return () => clearTimeout(t);
  }, [q, filters.q, onChange]);

  // "/" focuses search (§62).
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const t = e.target as HTMLElement;
      if (e.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName)) {
        e.preventDefault();
        searchRef.current?.focus();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const [expanded, setExpanded] = useState(false);
  const extra = activeFilterCount(filters);

  return (
    <div className="mb-3 space-y-2">
    <div className="flex flex-wrap items-center gap-2">
      <Input
        ref={searchRef}
        type="search"
        aria-label="Поиск по названию"
        placeholder="Поиск по названию  ( / )"
        value={q}
        maxLength={200}
        onChange={(e) => setQ(e.target.value)}
        className="w-64"
      />
      <Select
        aria-label="Проект"
        value={filters.project_id ?? ""}
        onChange={(e) => onChange({ project_id: e.target.value || null })}
        className="w-52"
      >
        <option value="">Все проекты</option>
        {projects.data?.items.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </Select>
      <NumberFilter filters={filters} name="min_subscribers" label="Подписчиков от" onChange={onChange} />
      <NumberFilter filters={filters} name="max_subscribers" label="Подписчиков до" onChange={onChange} />
      <Button variant="secondary" aria-expanded={expanded} onClick={() => setExpanded((v) => !v)}>
        <SlidersHorizontal className="size-4" aria-hidden /> Фильтры{extra ? ` (${extra})` : ""}
      </Button>
      {hasFilters ? (
        <Button variant="ghost" onClick={() => onChange(CLEAR_FILTERS)}>
          <X className="size-4" aria-hidden /> Сбросить
        </Button>
      ) : null}
    </div>
    {expanded ? <MoreFilters filters={filters} onChange={onChange} /> : null}
    </div>
  );
}

type NumericKey = (typeof INT_FILTERS)[number] | keyof typeof FLOAT_FILTERS;

/** Uncontrolled number box: commits to the URL on blur / Enter (each URL change is a request). */
function NumberFilter({
  filters,
  name,
  label,
  onChange,
  step,
  className = "w-36",
}: {
  filters: ChannelFilters;
  name: NumericKey;
  label: string;
  onChange: (patch: UrlPatch) => void;
  step?: string;
  className?: string;
}) {
  const value = filters[name];
  const commit = (raw: string) => onChange({ [name]: raw.trim() || null });
  return (
    <Input
      key={`${name}-${value ?? ""}`}
      type="number"
      min={0}
      step={step}
      inputMode={step ? "decimal" : "numeric"}
      aria-label={label}
      placeholder={label}
      title={label}
      defaultValue={value ?? ""}
      className={className}
      onBlur={(e) => commit(e.currentTarget.value)}
      onKeyDown={(e) => e.key === "Enter" && commit(e.currentTarget.value)}
    />
  );
}

function TextFilter({
  filters,
  name,
  label,
  onChange,
  maxLength,
  upper,
}: {
  filters: ChannelFilters;
  name: "country" | "language";
  label: string;
  onChange: (patch: UrlPatch) => void;
  maxLength: number;
  upper?: boolean;
}) {
  const value = filters[name] ?? "";
  const commit = (raw: string) => onChange({ [name]: (upper ? raw.trim().toUpperCase() : raw.trim()) || null });
  return (
    <Input
      key={`${name}-${value}`}
      aria-label={label}
      placeholder={label}
      maxLength={maxLength}
      defaultValue={value}
      className={cn("w-32", upper && "uppercase")}
      onBlur={(e) => commit(e.currentTarget.value)}
      onKeyDown={(e) => e.key === "Enter" && commit(e.currentTarget.value)}
    />
  );
}

const FILTER_GROUPS: { title: string; fields: { name: NumericKey; label: string; step?: string }[] }[] = [
  {
    title: "Просмотры",
    fields: [
      { name: "min_avg_views", label: "Средние от" },
      { name: "max_avg_views", label: "Средние до" },
      { name: "min_median_views", label: "Медиана от" },
      { name: "min_last_video_views", label: "Последнее видео от" },
      { name: "min_avg_views_recent", label: "Последние 10 видео от" },
      { name: "min_views_to_subs", label: "Просм./подп. от", step: "0.01" },
    ],
  },
  {
    title: "Активность",
    fields: [
      { name: "max_days_since_last_upload", label: "Посл. видео не старше, дн." },
      { name: "min_videos_7d", label: "Видео за 7 дн. от" },
      { name: "min_videos_30d", label: "Видео за 30 дн. от" },
      { name: "min_videos_90d", label: "Видео за 90 дн. от" },
      { name: "max_avg_upload_gap_days", label: "Интервал публикаций до, дн.", step: "0.5" },
      { name: "min_upload_consistency", label: "Регулярность от (0–1)", step: "0.05" },
    ],
  },
  {
    title: "Канал",
    fields: [
      { name: "min_videos", label: "Видео на канале от" },
      { name: "max_videos", label: "Видео на канале до" },
    ],
  },
];

function MoreFilters({ filters, onChange }: { filters: ChannelFilters; onChange: (patch: UrlPatch) => void }) {
  const taxonomy = useTaxonomy();
  const nodes = taxonomy.data ?? [];
  const project = useProject(filters.project_id ?? 0, filters.project_id !== undefined);
  const update = useUpdateProject(filters.project_id ?? 0);
  const saved = project.data?.filter_settings ?? {};

  return (
    <Card className="space-y-3">
      {FILTER_GROUPS.map((g) => (
        <fieldset key={g.title} className="flex flex-wrap items-center gap-2">
          <legend className="mb-1 text-xs font-medium uppercase tracking-wide text-zinc-500">{g.title}</legend>
          {g.fields.map((fl) => (
            <NumberFilter key={fl.name} filters={filters} name={fl.name} label={fl.label} step={fl.step}
              onChange={onChange} className="w-48" />
          ))}
        </fieldset>
      ))}
      <fieldset className="flex flex-wrap items-center gap-2">
        <legend className="mb-1 text-xs font-medium uppercase tracking-wide text-zinc-500">Рынок и ниши</legend>
        <TextFilter filters={filters} name="country" label="Страна (RU)" maxLength={2} upper onChange={onChange} />
        <TextFilter filters={filters} name="language" label="Язык (ru)" maxLength={20} onChange={onChange} />
        <NicheSelect label="Ниша: включить" nodes={nodes} value={filters.niche ?? []}
          onChange={(ids) => onChange({ niche: ids.join(",") || null })} />
        <NicheSelect label="Ниша: исключить" nodes={nodes} value={filters.exclude_niche ?? []}
          onChange={(ids) => onChange({ exclude_niche: ids.join(",") || null })} />
      </fieldset>
      <div className="flex flex-wrap items-center gap-2 border-t border-zinc-100 pt-3 text-sm dark:border-zinc-800">
        {filters.project_id !== undefined && project.data ? (
          <>
            <span className="text-xs text-zinc-500">Фильтры проекта «{project.data.name}»:</span>
            <Button size="sm" variant="secondary" disabled={Object.keys(saved).length === 0}
              onClick={() => onChange(patchFromFilterSet(saved))}>
              Применить сохранённые
            </Button>
            <Button size="sm" variant="secondary" disabled={update.isPending}
              onClick={() => update.mutate({ filter_settings: filterSetFromFilters(filters) })}>
              Сохранить текущие в проект
            </Button>
            {update.isSuccess ? <span role="status" className="text-xs text-emerald-700">Сохранено</span> : null}
            <InlineError error={update.error} />
          </>
        ) : (
          <span className="text-xs text-zinc-500">
            Выберите проект, чтобы применить или сохранить его фильтры. Фильтры по метрикам скрывают каналы, у
            которых метрики ещё не посчитаны.
          </span>
        )}
      </div>
    </Card>
  );
}

/** Multi-select of niches/topics; selecting a niche also matches its topics and subtopics (server side). */
function NicheSelect({
  label,
  nodes,
  value,
  onChange,
}: {
  label: string;
  nodes: TaxonomyNode[];
  value: number[];
  onChange: (ids: number[]) => void;
}) {
  const options = useMemo(() => taxonomyOptions(nodes), [nodes]);
  return (
    <Select
      multiple
      aria-label={label}
      title={`${label} (Ctrl/Cmd — несколько)`}
      value={value.map(String)}
      onChange={(e) => onChange(Array.from(e.currentTarget.selectedOptions, (o) => Number(o.value)))}
      className="h-20 w-56 py-1"
    >
      {options.length === 0 ? <option disabled>Ниш нет — создайте на странице «Ниши»</option> : null}
      {options.map((o) => (
        <option key={o.id} value={o.id}>
          {o.label}
        </option>
      ))}
    </Select>
  );
}
