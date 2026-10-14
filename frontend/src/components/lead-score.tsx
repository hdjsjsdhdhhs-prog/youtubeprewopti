"use client";

import { Badge } from "@/components/ui";
import type { LeadScore } from "@/lib/api/hooks";
import { formatDateTime } from "@/lib/format";
import {
  COMPONENT_HINTS,
  COMPONENT_LABELS,
  formatLeadScore,
  formatPoints,
  leadScoreTone,
  pointsWidth,
  type ComponentKey,
} from "@/lib/leads";
import { cn } from "@/lib/utils";

export function LeadScoreBadge({ score, label = "Lead" }: { score: string | null | undefined; label?: string }) {
  return (
    <Badge tone={leadScoreTone(score)} title={`${label} Score, 0–100 (считает код по весам профиля)`}>
      <span className="tabular-nums">{formatLeadScore(score)}</span>
    </Badge>
  );
}

/** Explainable Lead Score (ТЗ §8): every component as «points / max» with the reason the code gave. */
export function LeadScoreBreakdown({ score }: { score: LeadScore }) {
  return (
    <div className="space-y-2">
      <p className="text-sm">
        <b className="text-lg tabular-nums">{formatLeadScore(score.total)}</b>
        <span className="text-zinc-500">/100 Lead Score</span>
        <span className="mx-2 text-zinc-300">·</span>
        приоритет <b className="tabular-nums">{formatLeadScore(score.priority_score)}</b>
        <span className="mx-2 text-zinc-300">·</span>
        <span className="text-zinc-500" title="Доля максимума Lead Score, по которой есть данные">
          данных: {formatLeadScore(score.data_coverage)} %
        </span>
      </p>
      <ul className="space-y-1.5" aria-label="Компоненты Lead Score">
        {score.components.map((c) => {
          const key = c.key as ComponentKey;
          const label = COMPONENT_LABELS[key] ?? c.label;
          const off = Number(c.max) === 0;
          return (
            <li key={c.key} className={cn("grid grid-cols-[12rem_6rem_1fr] items-start gap-3 text-sm", off && "opacity-60")}>
              <span title={COMPONENT_HINTS[key]}>{label}</span>
              <span className="tabular-nums">
                {off ? <span className="text-xs text-zinc-500">вес 0</span> : formatPoints(c.points, c.max)}
                <span
                  className="mt-1 block h-1.5 overflow-hidden rounded bg-zinc-100 dark:bg-zinc-800"
                  aria-hidden
                >
                  <span
                    className={cn("block h-full", c.has_data ? "bg-emerald-500" : "bg-zinc-300")}
                    style={{ width: `${pointsWidth(c.points, c.max)}%` }}
                  />
                </span>
              </span>
              <span className={cn("text-xs", c.has_data ? "text-zinc-600 dark:text-zinc-300" : "text-zinc-500 italic")}>
                {c.reason}
              </span>
            </li>
          );
        })}
      </ul>
      <p className="text-xs text-zinc-500">
        Рассчитано {formatDateTime(score.created_at)} · веса Lead v{score.lead_profile_version}, приоритета v
        {score.priority_profile_version}, превью v{score.thumbnail_profile_version} (v0 — по умолчанию). Без AI.
      </p>
    </div>
  );
}
