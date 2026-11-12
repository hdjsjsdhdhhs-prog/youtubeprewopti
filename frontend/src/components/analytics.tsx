"use client";

/** Building blocks of the dashboard and the analytics page (Phase 9): period / project filter, outcome cells and
 * the «best …» line. Rates and samples come from the backend; «мало данных» is shown, never hidden. */
import type { ReactNode } from "react";

import { Badge, Select } from "@/components/ui";
import { PERIODS, type PeriodKey } from "@/lib/analytics";
import type { Best, Outcomes } from "@/lib/api/analytics-hooks";
import { useProjects } from "@/lib/api/hooks";
import { formatRate } from "@/lib/campaigns";
import { formatCount } from "@/lib/format";
import { cn } from "@/lib/utils";

export function AnalyticsFilters({
  period,
  projectId,
  onChange,
}: {
  period: PeriodKey;
  projectId?: number;
  onChange: (patch: { period?: PeriodKey; project?: number | null }) => void;
}) {
  const projects = useProjects({ status: "active", offset: 0 });
  return (
    <div className="flex flex-wrap items-center gap-2">
      <div role="group" aria-label="Период" className="inline-flex rounded-md border border-zinc-300 dark:border-zinc-700">
        {PERIODS.map((p) => (
          <button
            key={p.key}
            type="button"
            aria-pressed={p.key === period}
            onClick={() => onChange({ period: p.key })}
            className={cn(
              "px-2.5 py-1 text-xs first:rounded-l-md last:rounded-r-md",
              p.key === period
                ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900"
                : "text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800",
            )}
          >
            {p.label}
          </button>
        ))}
      </div>
      <Select
        aria-label="Проект"
        className="h-7 w-48 text-xs"
        value={projectId ?? ""}
        onChange={(e) => onChange({ project: e.target.value ? Number(e.target.value) : null })}
      >
        <option value="">Все проекты</option>
        {projects.data?.items.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </Select>
    </div>
  );
}

/** Reply rate with the sample size; small samples are greyed and labelled. */
export function RateCell({ outcomes }: { outcomes: Outcomes }) {
  if (outcomes.sent === 0) return <span className="text-zinc-400">—</span>;
  return (
    <span className={cn("tabular-nums", outcomes.small_sample && "text-zinc-400")}>
      {formatRate(outcomes.reply_rate)}
      {outcomes.small_sample ? (
        <span className="ml-1 text-[10px] uppercase" title="Меньше минимальной выборки: доля ничего не значит">
          мало
        </span>
      ) : null}
    </span>
  );
}

export function OutcomeHeader() {
  return (
    <>
      <th className="py-1 text-right font-medium">Отправлено</th>
      <th className="py-1 text-right font-medium">Ответы</th>
      <th className="py-1 text-right font-medium">Положит.</th>
      <th className="py-1 text-right font-medium">Доля ответов</th>
    </>
  );
}

export function OutcomeCells({ outcomes }: { outcomes: Outcomes }) {
  return (
    <>
      <td className="py-1.5 text-right tabular-nums">{formatCount(outcomes.sent)}</td>
      <td className="py-1.5 text-right tabular-nums">{formatCount(outcomes.replied)}</td>
      <td className="py-1.5 text-right tabular-nums">{formatCount(outcomes.positive_reply)}</td>
      <td className="py-1.5 text-right">
        <RateCell outcomes={outcomes} />
      </td>
    </>
  );
}

export function BestLine({ title, best, minSample }: { title: string; best: Best | null; minSample: number }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1 text-sm">
      <span className="text-zinc-500">{title}</span>
      {best ? (
        <span className="text-right">
          <span className="font-medium">{best.name}</span>{" "}
          <span className="text-xs text-zinc-500">
            {formatRate(best.reply_rate)} ответов из {formatCount(best.sent)}
          </span>
        </span>
      ) : (
        <Badge title={`Ни в одной группе не отправлено ${minSample} писем`}>мало данных</Badge>
      )}
    </div>
  );
}

export function Table({ head, children }: { head: ReactNode; children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-xs uppercase tracking-wide text-zinc-500">
          <tr>{head}</tr>
        </thead>
        <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">{children}</tbody>
      </table>
    </div>
  );
}
