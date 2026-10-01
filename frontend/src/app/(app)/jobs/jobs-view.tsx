"use client";

import { RefreshCw } from "lucide-react";

import { EmptyState, ErrorState, InlineError, LoadingState } from "@/components/states";
import { JOB_STATUS_LABELS, JobStatusBadge } from "@/components/status-badges";
import { Button, PageHeader, Pager, Select } from "@/components/ui";
import { ACTIVE_JOB_STATUSES, JOBS_PAGE, useCancelJob, useJobs, type Job, type JobStatus } from "@/lib/api/hooks";
import { formatDateTime, formatElapsed } from "@/lib/format";
import { enumParam, intParam, useUrlState } from "@/lib/url-state";
import { cn } from "@/lib/utils";

const STATUSES = Object.keys(JOB_STATUS_LABELS) as JobStatus[];

/** Human names for job types (backend JobType); unknown types fall back to the raw value. */
export const JOB_TYPE_LABELS: Record<string, string> = {
  thumbnail_download: "Загрузка превью",
  discovery: "Поиск каналов",
};

export function JobsView() {
  const [params, setParams] = useUrlState();
  const status = enumParam(params, "status", STATUSES);
  const type = params.get("type")?.slice(0, 60) || undefined;
  const offset = intParam(params, "offset") ?? 0;
  const jobs = useJobs({ status, type, offset });
  const cancel = useCancelJob();
  const polling = jobs.data?.items.some((j) => ACTIVE_JOB_STATUSES.has(j.status)) ?? false;

  return (
    <>
      <PageHeader
        title="Задачи"
        subtitle={polling ? "Есть активные задачи — список обновляется автоматически" : "Фоновые задачи рабочего пространства"}
        actions={
          <Button variant="secondary" onClick={() => jobs.refetch()} disabled={jobs.isFetching}>
            <RefreshCw className={cn("size-4", jobs.isFetching && "animate-spin")} aria-hidden /> Обновить
          </Button>
        }
      />
      <div className="mb-3 flex flex-wrap gap-2">
        <Select
          aria-label="Статус"
          value={status ?? ""}
          onChange={(e) => setParams({ status: e.target.value || null, offset: null })}
          className="w-44"
        >
          <option value="">Все статусы</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {JOB_STATUS_LABELS[s]}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Тип"
          value={type ?? ""}
          onChange={(e) => setParams({ type: e.target.value || null, offset: null })}
          className="w-52"
        >
          <option value="">Все типы</option>
          {Object.entries(JOB_TYPE_LABELS).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </Select>
      </div>

      <InlineError error={cancel.error} />

      {jobs.isPending ? (
        <LoadingState />
      ) : jobs.isError && !jobs.data ? (
        <ErrorState error={jobs.error} onRetry={() => jobs.refetch()} />
      ) : jobs.data.items.length === 0 ? (
        <EmptyState
          title={status || type ? "Нет задач под эти фильтры" : "Задач пока нет"}
          hint={status || type ? undefined : "Задачи появятся, когда вы запустите загрузку превью или поиск."}
        />
      ) : (
        <>
          <div className="overflow-x-auto rounded-lg border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
            <table className="w-full text-sm">
              <thead className="bg-zinc-50 text-left text-xs uppercase tracking-wide text-zinc-500 dark:bg-zinc-900">
                <tr>
                  <th className="px-3 py-2 font-medium">#</th>
                  <th className="px-3 py-2 font-medium">Тип</th>
                  <th className="px-3 py-2 font-medium">Статус</th>
                  <th className="w-48 px-3 py-2 font-medium">Прогресс</th>
                  <th className="px-3 py-2 text-right font-medium">Попытки</th>
                  <th className="px-3 py-2 font-medium">Создана</th>
                  <th className="px-3 py-2 font-medium">Длительность</th>
                  <th className="px-3 py-2" aria-label="Действия" />
                </tr>
              </thead>
              <tbody className={cn("divide-y divide-zinc-100 dark:divide-zinc-800", jobs.isPlaceholderData && "opacity-60")}>
                {jobs.data.items.map((j) => (
                  <JobRow
                    key={j.id}
                    job={j}
                    cancelling={cancel.isPending && cancel.variables === j.id}
                    onCancel={() => cancel.mutate(j.id)}
                  />
                ))}
              </tbody>
            </table>
          </div>
          <Pager offset={offset} limit={JOBS_PAGE} total={jobs.data.total} onChange={(o) => setParams({ offset: o || null })} />
        </>
      )}
    </>
  );
}

function JobRow({ job: j, cancelling, onCancel }: { job: Job; cancelling: boolean; onCancel: () => void }) {
  const active = ACTIVE_JOB_STATUSES.has(j.status);
  const pct = j.progress_total > 0 ? Math.min(100, Math.round((j.progress_done / j.progress_total) * 100)) : 0;
  return (
    <>
      <tr className="align-top">
        <td className="px-3 py-2 font-mono text-xs text-zinc-500">{j.id}</td>
        <td className="px-3 py-2">
          {JOB_TYPE_LABELS[j.type] ?? j.type}
          <div className="text-xs text-zinc-500">очередь: {j.queue}</div>
        </td>
        <td className="px-3 py-2">
          <JobStatusBadge status={j.status} />
        </td>
        <td className="px-3 py-2">
          {j.progress_total > 0 ? (
            <div>
              <div
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={j.progress_total}
                aria-valuenow={j.progress_done}
                aria-label="Прогресс"
                className="h-1.5 w-full overflow-hidden rounded bg-zinc-200 dark:bg-zinc-700"
              >
                <div
                  className={cn("h-full", j.status === "failed" ? "bg-red-500" : "bg-blue-500")}
                  style={{ width: `${pct}%` }}
                />
              </div>
              <div className="mt-0.5 text-xs tabular-nums text-zinc-500">
                {j.progress_done} из {j.progress_total}
              </div>
            </div>
          ) : (
            <span className="text-zinc-400">—</span>
          )}
        </td>
        <td className="px-3 py-2 text-right tabular-nums">{j.attempts}</td>
        <td className="px-3 py-2 whitespace-nowrap text-zinc-500">{formatDateTime(j.created_at)}</td>
        <td className="px-3 py-2 whitespace-nowrap text-zinc-500">{formatElapsed(j.started_at, j.finished_at)}</td>
        <td className="px-3 py-2 text-right">
          {active ? (
            <Button size="sm" variant="secondary" disabled={cancelling} onClick={onCancel}>
              {cancelling ? "Отмена…" : "Отменить"}
            </Button>
          ) : null}
        </td>
      </tr>
      {j.error_human ? (
        <tr>
          <td />
          <td colSpan={7} className="px-3 pb-2 text-xs text-red-700 dark:text-red-400">
            {j.error_human} {j.error_code ? <span className="font-mono text-red-500/70">({j.error_code})</span> : null}
          </td>
        </tr>
      ) : null}
    </>
  );
}
