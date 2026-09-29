"use client";

import { ArrowLeft, Archive, ArchiveRestore, Trash2, Tv } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";

import { EmptyState, ErrorState, InlineError, LoadingState } from "@/components/states";
import { DemoBadge, ProjectStatusBadge, QueryStatusBadge } from "@/components/status-badges";
import { Button, Card, Label, PageHeader, Pager, Textarea } from "@/components/ui";
import {
  QUERIES_PAGE,
  useDeleteProject,
  useDeleteQuery,
  useImportQueries,
  useProject,
  useProjectQueries,
  useUpdateProject,
  type Project,
  type QueryBulkResult,
} from "@/lib/api/hooks";
import { formatCount, formatDate, formatDateTime } from "@/lib/format";
import { countQueryLines } from "@/lib/forms";
import { cn } from "@/lib/utils";

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
        <QueriesList projectId={id} />
        <ImportQueries projectId={id} disabled={p.status === "archived"} />
      </div>
    </>
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
                  <th className="py-1.5 font-medium">Статус</th>
                  <th className="py-1.5 text-right font-medium">Результаты</th>
                  <th className="py-1.5 font-medium">Последний запуск</th>
                  <th className="py-1.5" aria-label="Действия" />
                </tr>
              </thead>
              <tbody className={cn("divide-y divide-zinc-100 dark:divide-zinc-800", queries.isPlaceholderData && "opacity-60")}>
                {queries.data.items.map((q) => (
                  <tr key={q.id}>
                    <td className="py-1.5 pr-2">{q.text}</td>
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
  const importQueries = useImportQueries(projectId);
  const lines = countQueryLines(text);

  return (
    <Card>
      <h2 className="mb-2 text-sm font-semibold">Добавить запросы</h2>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (lines > 0) importQueries.mutate(text, { onSuccess: () => setText("") });
        }}
        className="space-y-2"
      >
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
