"use client";

import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Archive, ArchiveRestore, Search, Trash2, Tv } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { EmptyState, ErrorState, InlineError, LoadingState } from "@/components/states";
import { DemoBadge, JobStatusBadge, ProjectStatusBadge, QueryStatusBadge } from "@/components/status-badges";
import { Badge, Button, Card, Label, PageHeader, Pager, Select, Textarea } from "@/components/ui";
import {
  ACTIVE_JOB_STATUSES,
  QUERIES_PAGE,
  qk,
  useDeleteProject,
  useDeleteQuery,
  useImportQueries,
  useJobs,
  useProject,
  useProjectNiches,
  useProjectQueries,
  useSetProjectNiches,
  useStartDiscovery,
  useTaxonomy,
  useUpdateProject,
  useYoutubeQuota,
  type DiscoveryStartResult,
  type Job,
  type Project,
  type QueryBulkResult,
  type SearchType,
} from "@/lib/api/hooks";
import { formatCount, formatDate, formatDateTime } from "@/lib/format";
import { countQueryLines } from "@/lib/forms";
import { LEVEL_LABELS, taxonomyOptions, taxonomyPath } from "@/lib/taxonomy";
import { cn } from "@/lib/utils";

import { AnalysisPrefilter, ThumbnailIngestion } from "./thumbnail-analysis";

export default function ProjectDetailPage() {
  const { id: rawId } = useParams<{ id: string }>();
  const id = Number(rawId);
  if (!Number.isInteger(id) || id <= 0) return <EmptyState title="Проект не найден" />;
  return <ProjectDetail id={id} />;
}

function ProjectDetail({ id }: { id: number }) {
  const project = useProject(id);

  if (project.isPending) return <LoadingState />;
  if (project.isError) {
    return (
      <>
        <BackLink />
        <ErrorState error={project.error} onRetry={() => project.refetch()} />
      </>
    );
  }
  const p = project.data;
  return (
    <>
      <BackLink />
      <PageHeader
        title={
          <span className="flex items-center gap-2">
            {p.name} <ProjectStatusBadge status={p.status} /> <DemoBadge show={p.is_demo} />
          </span>
        }
        subtitle={p.description || undefined}
        actions={<ProjectActions project={p} />}
      />
      <Settings project={p} />
      <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <Discovery project={p} />
        <ProjectNiches projectId={id} disabled={p.status === "archived"} />
      </div>
      <div className="mt-4 space-y-4">
        <ThumbnailIngestion project={p} />
        <AnalysisPrefilter project={p} />
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <QueriesList projectId={id} />
        <ImportQueries projectId={id} disabled={p.status === "archived"} />
      </div>
    </>
  );
}

const DISCOVERY_RUNS_SHOWN = 5;

function Discovery({ project: p }: { project: Project }) {
  const qc = useQueryClient();
  const quota = useYoutubeQuota();
  const start = useStartDiscovery(p.id);
  const runs = useJobs({ type: "discovery", project_id: p.id, offset: 0 }, DISCOVERY_RUNS_SHOWN);
  const [includeDone, setIncludeDone] = useState(false);

  // When a run finishes, the queries, the project counters and the channel list change.
  const activeIds = (runs.data?.items ?? []).filter((j) => ACTIVE_JOB_STATUSES.has(j.status)).map((j) => j.id);
  const activeKey = activeIds.join(",");
  const previous = useRef(activeKey);
  useEffect(() => {
    if (previous.current && previous.current !== activeKey) {
      void qc.invalidateQueries({ queryKey: qk.projects });
      void qc.invalidateQueries({ queryKey: qk.channels });
      void qc.invalidateQueries({ queryKey: qk.quota });
    }
    previous.current = activeKey;
  }, [activeKey, qc]);

  const q = quota.data;
  const notConfigured = quota.isSuccess && !q?.provider;
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">Поиск каналов (YouTube)</h2>
          <p className="mt-0.5 text-xs text-zinc-500">
            {notConfigured ? (
              <>YouTube API не настроен: задайте <code>YTL_YOUTUBE_API_KEY</code> в конфигурации backend.</>
            ) : q ? (
              <>
                Квота на {q.quota_day ? formatDate(q.quota_day) : "сегодня"} (сброс в полночь по Тихоокеанскому
                времени): использовано <b>{formatCount(q.used)}</b> из {formatCount(q.limit)}, осталось{" "}
                <b>{formatCount(q.remaining)}</b>. {q.mode === "mock" ? <Badge tone="violet">mock — демо-данные</Badge> : null}
              </>
            ) : (
              "Загрузка квоты…"
            )}
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <Button
            disabled={notConfigured || p.status === "archived" || start.isPending}
            onClick={() => start.mutate({ include_done: includeDone })}
          >
            <Search className="size-4" aria-hidden /> {start.isPending ? "Запуск…" : "Найти каналы"}
          </Button>
          <label className="flex items-center gap-1.5 text-xs text-zinc-500">
            <input type="checkbox" checked={includeDone} onChange={(e) => setIncludeDone(e.target.checked)} />
            повторить и выполненные запросы
          </label>
        </div>
      </div>
      <InlineError error={start.error} />
      {start.data ? <StartSummary result={start.data} /> : null}
      <RecentRuns runs={runs.data?.items ?? []} />
    </Card>
  );
}

function StartSummary({ result: r }: { result: DiscoveryStartResult }) {
  if (!r.job) {
    return (
      <p role="status" className="mt-2 text-xs text-zinc-500">
        Нет запросов для поиска: все выполнены (отметьте «повторить…») или список пуст.
      </p>
    );
  }
  const overBudget = r.quota_search_units > r.quota.remaining;
  return (
    <p role="status" className={cn("mt-2 text-xs", overBudget ? "text-amber-700 dark:text-amber-400" : "text-zinc-600")}>
      {r.created ? "Поиск поставлен в очередь" : "Такой поиск уже выполняется"}: {r.queries} запрос(ов), квота — поиск{" "}
      {formatCount(r.quota_search_units)}, всего до {formatCount(r.quota_max_units)} единиц.
      {overBudget
        ? " Сегодняшней квоты не хватит на все запросы: поиск остановится, оставшиеся запросы можно запустить после сброса."
        : null}
    </p>
  );
}

function RecentRuns({ runs }: { runs: Job[] }) {
  if (runs.length === 0) return <p className="mt-3 text-xs text-zinc-500">Поиск ещё не запускался.</p>;
  return (
    <ul className="mt-3 divide-y divide-zinc-100 border-t border-zinc-100 text-xs dark:divide-zinc-800 dark:border-zinc-800">
      {runs.map((j) => {
        const r = j.result as Partial<Record<string, number>>;
        return (
          <li key={j.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
            <JobStatusBadge status={j.status} />
            <span className="tabular-nums text-zinc-500">
              {formatDateTime(j.created_at)} · запросы {j.progress_done}/{j.progress_total}
            </span>
            {j.status === "completed" ? (
              <span>
                каналов: <b>{formatCount(r.channels_found)}</b> (новых в проекте {formatCount(r.channels_new_in_project)}) ·
                квота {formatCount(r.quota_units_spent)}
                {r.queries_failed ? <span className="text-red-600"> · ошибок: {r.queries_failed}</span> : null}
              </span>
            ) : null}
            {j.error_human ? <span className="text-red-700 dark:text-red-400">{j.error_human}</span> : null}
          </li>
        );
      })}
      <li className="py-2">
        <Link href="/jobs?type=discovery" className="text-zinc-500 underline">
          Все задачи поиска
        </Link>
      </li>
    </ul>
  );
}

function ProjectNiches({ projectId, disabled }: { projectId: number; disabled: boolean }) {
  const niches = useProjectNiches(projectId);
  const taxonomy = useTaxonomy();
  const save = useSetProjectNiches(projectId);
  const options = useMemo(() => taxonomyOptions(taxonomy.data ?? []), [taxonomy.data]);
  const [editing, setEditing] = useState<number[] | null>(null);
  const current = niches.data ?? [];

  return (
    <Card>
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold">Ниши проекта</h2>
        <Link href="/niches" className="text-xs text-zinc-500 underline">
          Справочник ниш
        </Link>
      </div>
      {editing === null ? (
        <>
          {current.length === 0 ? (
            <p className="text-xs text-zinc-500">Ниши не выбраны.</p>
          ) : (
            <div className="flex flex-wrap gap-1">
              {current.map((n) => (
                <Badge key={n.id} tone="blue" title={LEVEL_LABELS[n.level]}>
                  {taxonomyPath(taxonomy.data ?? [], n.id) ?? n.name}
                </Badge>
              ))}
            </div>
          )}
          <Button size="sm" variant="secondary" className="mt-2" disabled={disabled}
            onClick={() => setEditing(current.map((n) => n.id))}>
            Изменить
          </Button>
        </>
      ) : (
        <form
          className="space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate(editing, { onSuccess: () => setEditing(null) });
          }}
        >
          <Select
            multiple
            aria-label="Ниши проекта"
            value={editing.map(String)}
            onChange={(e) => setEditing(Array.from(e.currentTarget.selectedOptions, (o) => Number(o.value)))}
            className="h-40 py-1"
          >
            {options.map((o) => (
              <option key={o.id} value={o.id}>
                {o.label}
              </option>
            ))}
          </Select>
          <p className="text-xs text-zinc-500">Ctrl/Cmd — выбрать несколько. Новые ниши — в «Справочнике ниш».</p>
          <div className="flex gap-2">
            <Button size="sm" type="submit" disabled={save.isPending}>
              Сохранить
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(null)}>
              Отмена
            </Button>
          </div>
          <InlineError error={save.error} />
        </form>
      )}
    </Card>
  );
}

function BackLink() {
  return (
    <Link href="/projects" className="mb-3 inline-flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100">
      <ArrowLeft className="size-4" aria-hidden /> Проекты
    </Link>
  );
}

function ProjectActions({ project: p }: { project: Project }) {
  const router = useRouter();
  const update = useUpdateProject(p.id);
  const remove = useDeleteProject(p.id);
  const archived = p.status === "archived";

  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex gap-2">
        <Link
          href={`/channels?project_id=${p.id}`}
          className="inline-flex h-9 items-center gap-1.5 rounded-md border border-zinc-300 bg-white px-3.5 text-sm font-medium hover:bg-zinc-100 dark:border-zinc-700 dark:bg-zinc-900 dark:hover:bg-zinc-800"
        >
          <Tv className="size-4" aria-hidden /> Каналы ({formatCount(p.channels_count)})
        </Link>
        <Button
          variant="secondary"
          disabled={update.isPending}
          onClick={() => update.mutate({ status: archived ? "active" : "archived" })}
        >
          {archived ? <ArchiveRestore className="size-4" aria-hidden /> : <Archive className="size-4" aria-hidden />}
          {archived ? "Вернуть из архива" : "В архив"}
        </Button>
        <Button
          variant="danger"
          disabled={remove.isPending}
          onClick={() => {
            if (window.confirm(`Удалить проект «${p.name}» вместе с запросами? Это действие необратимо.`)) {
              remove.mutate(undefined, { onSuccess: () => router.replace("/projects") });
            }
          }}
        >
          <Trash2 className="size-4" aria-hidden /> Удалить
        </Button>
      </div>
      <InlineError error={update.error ?? remove.error} />
    </div>
  );
}

function Settings({ project: p }: { project: Project }) {
  const rows: [string, string][] = [
    ["Язык", p.language ?? "—"],
    ["Регион", p.region_code ?? "—"],
    ["Результатов на запрос", String(p.results_per_query)],
    ["Глубина поиска", String(p.search_depth)],
    ["Видео для анализа", String(p.videos_to_analyze)],
    ["Опубликованы после", formatDate(p.published_after)],
    ["Создан", formatDateTime(p.created_at)],
    ["Изменён", formatDateTime(p.updated_at)],
  ];
  return (
    <Card>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
        {rows.map(([k, v]) => (
          <div key={k}>
            <dt className="text-xs text-zinc-500">{k}</dt>
            <dd className="tabular-nums">{v}</dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}

function QueriesList({ projectId }: { projectId: number }) {
  const [offset, setOffset] = useState(0);
  const queries = useProjectQueries(projectId, offset);
  const remove = useDeleteQuery(projectId);
  const taxonomy = useTaxonomy();

  return (
    <Card className="p-0">
      <h2 className="border-b border-zinc-200 px-4 py-3 text-sm font-semibold dark:border-zinc-800">
        Поисковые запросы {queries.data ? <span className="font-normal text-zinc-500">({queries.data.total})</span> : null}
      </h2>
      <div className="p-4">
        {queries.isPending ? (
          <LoadingState />
        ) : queries.isError ? (
          <ErrorState error={queries.error} onRetry={() => queries.refetch()} />
        ) : queries.data.items.length === 0 ? (
          <EmptyState title="Запросов нет" hint="Вставьте запросы в форму справа — по одному на строку." />
        ) : (
          <>
            <InlineError error={remove.error} />
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wide text-zinc-500">
                <tr>
                  <th className="py-1.5 font-medium">Запрос</th>
                  <th className="py-1.5 font-medium">Ниша</th>
                  <th className="py-1.5 font-medium">Статус</th>
                  <th className="py-1.5 text-right font-medium">Результаты</th>
                  <th className="py-1.5 font-medium">Последний запуск</th>
                  <th className="py-1.5" aria-label="Действия" />
                </tr>
              </thead>
              <tbody className={cn("divide-y divide-zinc-100 dark:divide-zinc-800", queries.isPlaceholderData && "opacity-60")}>
                {queries.data.items.map((q) => (
                  <tr key={q.id}>
                    <td className="py-1.5 pr-2">
                      {q.text}{" "}
                      {q.search_type === "channel" ? (
                        <Badge tone="neutral" title="Поиск каналов по тематике, а не видео">
                          каналы
                        </Badge>
                      ) : null}
                    </td>
                    <td className="py-1.5 pr-2 text-xs text-zinc-500">
                      {taxonomyPath(taxonomy.data ?? [], q.taxonomy_node_id) ?? "—"}
                    </td>
                    <td className="py-1.5">
                      <QueryStatusBadge status={q.status} />
                    </td>
                    <td className="py-1.5 text-right tabular-nums">{formatCount(q.results_count)}</td>
                    <td className="py-1.5 pl-3 text-zinc-500">{formatDateTime(q.last_run_at)}</td>
                    <td className="py-1.5 text-right">
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-label={`Удалить запрос «${q.text}»`}
                        disabled={remove.isPending && remove.variables === q.id}
                        onClick={() => remove.mutate(q.id)}
                      >
                        <Trash2 className="size-3.5" aria-hidden />
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pager offset={offset} limit={QUERIES_PAGE} total={queries.data.total} onChange={setOffset} />
          </>
        )}
      </div>
    </Card>
  );
}

function ImportQueries({ projectId, disabled }: { projectId: number; disabled: boolean }) {
  const [text, setText] = useState("");
  const [nodeId, setNodeId] = useState("");
  const [searchType, setSearchType] = useState<SearchType>("video");
  const importQueries = useImportQueries(projectId);
  const taxonomy = useTaxonomy();
  const options = useMemo(() => taxonomyOptions(taxonomy.data ?? []), [taxonomy.data]);
  const lines = countQueryLines(text);

  return (
    <Card>
      <h2 className="mb-2 text-sm font-semibold">Добавить запросы</h2>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (lines > 0) {
            importQueries.mutate(
              { text, search_type: searchType, taxonomy_node_id: nodeId ? Number(nodeId) : null },
              { onSuccess: () => setText("") },
            );
          }
        }}
        className="space-y-2"
      >
        <div className="grid grid-cols-2 gap-2">
          <div>
            <Label htmlFor="import-niche">Ниша / тема</Label>
            <Select id="import-niche" value={nodeId} disabled={disabled} onChange={(e) => setNodeId(e.target.value)}>
              <option value="">— без ниши —</option>
              {options.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.label}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label htmlFor="import-type">Искать</Label>
            <Select
              id="import-type"
              value={searchType}
              disabled={disabled}
              onChange={(e) => setSearchType(e.target.value as SearchType)}
            >
              <option value="video">видео по ключевым словам</option>
              <option value="channel">каналы по тематике</option>
            </Select>
          </div>
        </div>
        <Label htmlFor="bulk-queries">По одному на строку (можно вставить столбец из таблицы)</Label>
        <Textarea
          id="bulk-queries"
          rows={8}
          value={text}
          disabled={disabled}
          onChange={(e) => setText(e.target.value)}
          placeholder={"обзор смартфонов\nрецепты выпечки\n…"}
        />
        <div className="flex items-center justify-between">
          <span className="text-xs text-zinc-500">{lines} строк</span>
          <Button type="submit" disabled={disabled || lines === 0 || importQueries.isPending}>
            {importQueries.isPending ? "Импорт…" : "Импортировать"}
          </Button>
        </div>
        {disabled ? <p className="text-xs text-zinc-500">Проект в архиве — верните его, чтобы добавлять запросы.</p> : null}
        <InlineError error={importQueries.error} />
        {importQueries.data ? <ImportSummary result={importQueries.data} /> : null}
      </form>
    </Card>
  );
}

function ImportSummary({ result }: { result: QueryBulkResult }) {
  return (
    <div role="status" className="rounded-md bg-zinc-50 p-2 text-xs dark:bg-zinc-800/60">
      <p>
        Добавлено: <b>{result.created}</b> · дубликатов: <b>{result.duplicates}</b> · отклонено:{" "}
        <b>{result.rejected.length}</b>
      </p>
      {result.rejected.length > 0 ? (
        <ul className="mt-1 max-h-32 list-inside list-disc overflow-auto text-red-700 dark:text-red-400">
          {result.rejected.map((r) => (
            <li key={r.line}>
              строка {r.line}: «{r.value}» — {r.reason}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
