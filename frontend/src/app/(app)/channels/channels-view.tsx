"use client";

import { createColumnHelper, tableFeatures, useTable } from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ArrowDown, ArrowUp, ExternalLink, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { DemoBadge } from "@/components/status-badges";
import { Button, Input, PageHeader, Select } from "@/components/ui";
import { useChannels, useProjects, type Channel, type ChannelFilters, type ChannelSort } from "@/lib/api/hooks";
import { formatCount, formatDate } from "@/lib/format";
import { useUrlState, type UrlPatch } from "@/lib/url-state";
import { cn } from "@/lib/utils";

import { filtersFromParams, nextSort } from "./channel-filters";

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
  helper.accessor("view_count", { header: "Просмотры", cell: (info) => formatCount(info.getValue()) }),
  helper.accessor("video_count", { header: "Видео", cell: (info) => formatCount(info.getValue()) }),
  helper.accessor("country", { header: "Страна", cell: (info) => info.getValue() ?? "—" }),
  helper.accessor("published_at", { header: "Создан", cell: (info) => formatDate(info.getValue()) }),
]);

/** Column id -> layout + which server sort it maps to. */
const LAYOUT: Record<string, { width: string; numeric?: boolean; sort?: ChannelSort }> = {
  title: { width: "minmax(16rem,1fr)", sort: "title" },
  subscriber_count: { width: "8rem", numeric: true, sort: "subscribers" },
  view_count: { width: "8rem", numeric: true, sort: "views" },
  video_count: { width: "6rem", numeric: true, sort: "videos" },
  country: { width: "5rem" },
  published_at: { width: "8rem", sort: "published" },
};
const GRID = { gridTemplateColumns: Object.values(LAYOUT).map((l) => l.width).join(" ") };

export function ChannelsView() {
  const [params, setParams] = useUrlState();
  const filters = useMemo(() => filtersFromParams(params), [params]);
  const channels = useChannels(filters);
  const rows = useMemo(() => channels.data?.pages.flatMap((p) => p.items) ?? EMPTY, [channels.data]);
  const total = channels.data?.pages[0]?.total ?? 0;
  const hasFilters = ["project_id", "q", "min_subscribers", "max_subscribers", "country"].some((k) => params.has(k));

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

const CLEAR_FILTERS: UrlPatch = { project_id: null, q: null, min_subscribers: null, max_subscribers: null, country: null };

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

  const numberField = (key: "min_subscribers" | "max_subscribers", placeholder: string) => (
    <Input
      key={`${key}-${filters[key] ?? ""}`}
      type="number"
      min={0}
      inputMode="numeric"
      aria-label={placeholder}
      placeholder={placeholder}
      defaultValue={filters[key] ?? ""}
      className="w-36"
      onBlur={(e) => onChange({ [key]: e.currentTarget.value.trim() || null })}
      onKeyDown={(e) => e.key === "Enter" && onChange({ [key]: e.currentTarget.value.trim() || null })}
    />
  );

  return (
    <div className="mb-3 flex flex-wrap items-center gap-2">
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
      {numberField("min_subscribers", "Подписчиков от")}
      {numberField("max_subscribers", "Подписчиков до")}
      <Input
        key={`country-${filters.country ?? ""}`}
        aria-label="Страна"
        placeholder="Страна (RU)"
        maxLength={2}
        defaultValue={filters.country ?? ""}
        className="w-28 uppercase"
        onBlur={(e) => onChange({ country: e.currentTarget.value.trim().toUpperCase() || null })}
        onKeyDown={(e) => e.key === "Enter" && onChange({ country: e.currentTarget.value.trim().toUpperCase() || null })}
      />
      {hasFilters ? (
        <Button variant="ghost" onClick={() => onChange(CLEAR_FILTERS)}>
          <X className="size-4" aria-hidden /> Сбросить
        </Button>
      ) : null}
    </div>
  );
}
