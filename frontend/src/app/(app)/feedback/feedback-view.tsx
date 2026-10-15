"use client";

import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Badge, Card, PageHeader } from "@/components/ui";
import { SUBSCORE_LABELS, formatScore } from "@/lib/analysis";
import type { ScoreTarget } from "@/lib/api/hooks";
import { useFeedbackSummary } from "@/lib/api/workflow-hooks";
import { formatCount } from "@/lib/format";
import { STATUS_LABELS, formatLeadScore } from "@/lib/leads";

const TARGET_LABELS: Record<ScoreTarget, string> = {
  thumbnail_analysis: "Превью",
  channel_analysis: "Аудит канала",
  lead: "Лид",
};

const FIELD_LABELS: Record<string, string> = {
  ...SUBSCORE_LABELS,
  overall_score: "Общий балл",
  visual_consistency: "Единство стиля",
  outdated_style_score: "Устаревание стиля",
  total: "Lead Score",
  priority_score: "Приоритет",
};

/** ТЗ §46: where the AI over- / underestimates compared with my corrections, and my decisions on leads. */
export function FeedbackView() {
  const summary = useFeedbackSummary();
  return (
    <div className="max-w-4xl">
      <PageHeader
        title="AI vs человек"
        subtitle="Ручные оценки рядом с оценками AI / кода и решения по лидам — основа обратной связи (ТЗ §46)"
      />
      {summary.isPending ? (
        <LoadingState />
      ) : summary.isError ? (
        <ErrorState error={summary.error} onRetry={() => void summary.refetch()} />
      ) : summary.data.events === 0 ? (
        <EmptyState
          title="Обратной связи пока нет"
          hint="Ставьте свои оценки превью и лидов (карандаш рядом с баллом AI) и принимайте решения по лидам."
        />
      ) : (
        <div className="space-y-4">
          <Card>
            <h2 className="text-sm font-semibold">Где AI ошибается</h2>
            <p className="mb-2 text-xs text-zinc-500">
              Текущие ручные оценки против значений AI / кода, которые они заменили. Δ &lt; 0 — AI завышает.
            </p>
            {summary.data.overrides.length === 0 ? (
              <p className="text-sm text-zinc-500">Ручных оценок нет.</p>
            ) : (
              <table className="w-full text-sm">
                <thead className="text-left text-xs uppercase tracking-wide text-zinc-500">
                  <tr>
                    <th className="py-1 font-medium">Что</th>
                    <th className="py-1 text-right font-medium">Оценок</th>
                    <th className="py-1 text-right font-medium">AI, ср.</th>
                    <th className="py-1 text-right font-medium">Я, ср.</th>
                    <th className="py-1 text-right font-medium">Δ</th>
                    <th className="py-1 text-right font-medium">AI завышает / занижает</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
                  {summary.data.overrides.map((o) => {
                    const fmt = o.target_type === "lead" ? formatLeadScore : formatScore;
                    const delta = Number(o.avg_delta);
                    return (
                      <tr key={`${o.target_type}-${o.field}`}>
                        <td className="py-1.5">
                          {TARGET_LABELS[o.target_type]} · {FIELD_LABELS[o.field] ?? o.field}
                        </td>
                        <td className="py-1.5 text-right tabular-nums">{formatCount(o.overrides)}</td>
                        <td className="py-1.5 text-right tabular-nums">{fmt(o.avg_ai)}</td>
                        <td className="py-1.5 text-right tabular-nums">{fmt(o.avg_human)}</td>
                        <td className="py-1.5 text-right">
                          <Badge tone={delta < 0 ? "red" : delta > 0 ? "green" : "neutral"}>
                            {delta > 0 ? "+" : ""}
                            {o.avg_delta}
                          </Badge>
                        </td>
                        <td className="py-1.5 text-right tabular-nums">
                          {o.ai_overestimates} / {o.ai_underestimates}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </Card>
          <Card>
            <h2 className="text-sm font-semibold">Мои решения по лидам</h2>
            <p className="mb-2 text-xs text-zinc-500">
              Ручные смены статуса и средний Lead Score, который код дал этим лидам в момент решения.
            </p>
            {summary.data.lead_decisions.length === 0 ? (
              <p className="text-sm text-zinc-500">Решений пока нет.</p>
            ) : (
              <ul className="space-y-1 text-sm">
                {summary.data.lead_decisions.map((d) => (
                  <li key={d.status} className="flex gap-3">
                    <span className="w-44">{STATUS_LABELS[d.status]}</span>
                    <span className="tabular-nums">{formatCount(d.decisions)} раз</span>
                    <span className="text-zinc-500">средний Lead Score {formatLeadScore(d.avg_lead_score)}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <p className="text-xs text-zinc-500">Всего событий обратной связи: {formatCount(summary.data.events)}</p>
        </div>
      )}
    </div>
  );
}
