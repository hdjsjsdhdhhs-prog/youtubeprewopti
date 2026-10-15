"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Contact } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef } from "react";

import { InlineError } from "@/components/states";
import { JobStatusBadge } from "@/components/status-badges";
import { Button, Card } from "@/components/ui";
import { useProjectContactStats, useStartContactScan } from "@/lib/api/contacts-hooks";
import { ACTIVE_JOB_STATUSES, qk, useJobs, type Project } from "@/lib/api/hooks";
import { CONTACT_TYPES, CONTACT_TYPE_LABELS } from "@/lib/contacts";
import { formatCount, formatDateTime } from "@/lib/format";

const RUNS_SHOWN = 3;

/** Phase 6: public contacts of the project's channels (ТЗ §18). Discovery already scans every channel it fetches;
 * this block covers channels found earlier and a rescan with a newer extractor. Free: no network, no AI. */
export function ContactsScan({ project: p }: { project: Project }) {
  const qc = useQueryClient();
  const stats = useProjectContactStats(p.id);
  const start = useStartContactScan(p.id);
  const runs = useJobs({ type: "contact_extraction", project_id: p.id, offset: 0 }, RUNS_SHOWN);

  // Refresh the counters (and the lead scores they feed) when a run of this project finishes.
  const activeKey = (runs.data?.items ?? [])
    .filter((j) => ACTIVE_JOB_STATUSES.has(j.status))
    .map((j) => j.id)
    .join(",");
  const previous = useRef(activeKey);
  useEffect(() => {
    if (previous.current && previous.current !== activeKey) {
      void qc.invalidateQueries({ queryKey: qk.contactStats(p.id) });
      void qc.invalidateQueries({ queryKey: qk.leads });
    }
    previous.current = activeKey;
  }, [activeKey, p.id, qc]);

  const s = stats.data;
  const todo = s ? s.channels - s.scanned : 0;
  const archived = p.status === "archived";
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">Контакты каналов</h2>
          <p className="mt-0.5 max-w-2xl text-xs text-zinc-500">
            Публичные контакты (email, Telegram, VK, сайт, соцсети) из описания канала и описаний видео — бесплатно, без
            запросов в сеть и без AI. Поиск каналов собирает их сам; здесь можно добрать ранее найденные каналы.{" "}
            <Link href="/suppression" className="underline">
              Список «Не писать»
            </Link>
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" disabled={archived || start.isPending || todo === 0} onClick={() => start.mutate(false)}>
            <Contact className="size-4" aria-hidden /> {start.isPending ? "Запуск…" : "Собрать контакты"}
          </Button>
          <Button
            variant="ghost"
            disabled={archived || start.isPending || (s?.channels ?? 0) === 0}
            title="Пересобрать у всех каналов проекта, даже у уже обработанных"
            onClick={() => start.mutate(true)}
          >
            Пересобрать все
          </Button>
        </div>
      </div>
      {s ? (
        <>
          <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
            {(
              [
                ["Каналов", s.channels],
                ["Обработано", s.scanned],
                ["С контактами", s.with_contacts],
                ["Не найдены", s.not_found],
              ] as const
            ).map(([k, v]) => (
              <div key={k}>
                <dt className="text-xs text-zinc-500">{k}</dt>
                <dd className="tabular-nums">{formatCount(v)}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-2 text-xs text-zinc-500">
            Каналов с пригодным контактом по типам:{" "}
            {CONTACT_TYPES.filter((t) => s.by_type[t]).length === 0
              ? "—"
              : CONTACT_TYPES.filter((t) => s.by_type[t])
                  .map((t) => `${CONTACT_TYPE_LABELS[t]} ${formatCount(s.by_type[t])}`)
                  .join(" · ")}
          </p>
        </>
      ) : null}
      <InlineError error={stats.error ?? start.error} />
      {start.data ? (
        <p role="status" className="mt-2 text-xs text-zinc-600">
          {start.data.job
            ? `${start.data.created ? "Задача поставлена" : "Такая задача уже идёт"}: ${formatCount(start.data.channels)} каналов.`
            : "Все каналы проекта уже обработаны."}
          {start.data.remaining > 0
            ? ` Ещё ${formatCount(start.data.remaining)} — запустите снова после завершения задачи.`
            : null}
        </p>
      ) : null}
      {(runs.data?.items ?? []).length > 0 ? (
        <ul className="mt-3 divide-y divide-zinc-100 border-t border-zinc-100 text-xs dark:divide-zinc-800 dark:border-zinc-800">
          {runs.data!.items.map((j) => {
            const r = j.result as Partial<Record<string, number>>;
            return (
              <li key={j.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
                <JobStatusBadge status={j.status} />
                <span className="tabular-nums text-zinc-500">
                  {formatDateTime(j.created_at)} · {j.progress_done}/{j.progress_total}
                </span>
                {j.status === "completed" ? (
                  <span>
                    новых контактов {formatCount(r.contacts_new)} · без изменений {formatCount(r.channels_unchanged)}
                  </span>
                ) : null}
                {j.error_human ? <span className="text-red-700 dark:text-red-400">{j.error_human}</span> : null}
              </li>
            );
          })}
        </ul>
      ) : null}
    </Card>
  );
}
