/** Analytics (Phase 9): period presets, URL ↔ query, labels and small display helpers. Every number comes from
 * the backend; nothing here computes a rate on its own except the funnel bar widths. */
import type { Schemas } from "@/lib/api/client";

import { enumParam, intParam } from "./url-state";

export type PeriodKey = "7d" | "30d" | "90d" | "all";
export type AnalyticsQuery = { date_from?: string; date_to?: string; project_id?: number };
export type ABDimension = Schemas["ABReport"]["dimension"];
export type Verdict = Schemas["ABReport"]["verdict"];

export const PERIODS: readonly { key: PeriodKey; label: string; days: number | null }[] = [
  { key: "7d", label: "7 дней", days: 7 },
  { key: "30d", label: "30 дней", days: 30 },
  { key: "90d", label: "90 дней", days: 90 },
  { key: "all", label: "Всё время", days: null },
];
export const PERIOD_KEYS = PERIODS.map((p) => p.key);
export const DEFAULT_PERIOD: PeriodKey = "30d";

function isoDay(d: Date): string {
  return d.toISOString().slice(0, 10);
}

/** The last N days including today (UTC, as the backend counts); «all» = no bounds. */
export function periodRange(key: PeriodKey, today = new Date()): Pick<AnalyticsQuery, "date_from" | "date_to"> {
  const days = PERIODS.find((p) => p.key === key)?.days ?? null;
  if (days === null) return {};
  const from = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - (days - 1)));
  return { date_from: isoDay(from), date_to: isoDay(today) };
}

/** ``?period=&project=`` → the period key and the API query. */
export function analyticsQueryFromParams(
  params: URLSearchParams,
  today = new Date(),
): { period: PeriodKey; query: AnalyticsQuery } {
  const period = enumParam(params, "period", PERIOD_KEYS) ?? DEFAULT_PERIOD;
  const project_id = intParam(params, "project");
  return { period, query: { ...periodRange(period, today), ...(project_id ? { project_id } : {}) } };
}

export const TASK_LABELS: Record<string, string> = {
  thumbnail_analysis: "Аудит превью",
  channel_analysis: "Аудит канала",
  classification: "Классификация",
  lead_explanation: "Объяснение лида",
  offer_generation: "Тексты офферов",
  thumbnail_generation: "Генерация превью",
};

export function taskLabel(key: string): string {
  return TASK_LABELS[key] ?? key;
}

export const AB_DIMENSIONS: readonly { key: ABDimension; label: string }[] = [
  { key: "style", label: "Стиль оффера" },
  { key: "attachment", label: "Вложение" },
  { key: "channel", label: "Канал" },
];

export const VERDICT_LABELS: Record<Verdict, string> = {
  insufficient_data: "Мало данных",
  no_difference: "Разница в пределах случайности",
  leader: "Есть лидер",
};

export function verdictTone(v: Verdict): "neutral" | "amber" | "green" {
  return v === "leader" ? "green" : v === "no_difference" ? "amber" : "neutral";
}

/** What the A/B verdict means in one sentence (the leader's label is given by the caller). */
export function verdictText(r: { verdict: Verdict; min_sample: number; p_value: string | null }, leaderLabel?: string): string {
  if (r.verdict === "insufficient_data") {
    return `Нужно хотя бы две группы, где отправлено не меньше ${r.min_sample}: выводов пока нет.`;
  }
  const p = r.p_value !== null ? ` (p = ${Number(r.p_value).toLocaleString("ru-RU")})` : "";
  if (r.verdict === "no_difference") {
    return `Впереди «${leaderLabel ?? "?"}», но разница с ближайшей группой может быть случайной${p}.`;
  }
  return `«${leaderLabel ?? "?"}» отвечают чаще, и разница больше случайной${p}. Это корреляция, а не доказанная причина.`;
}

/** Bar width of a funnel step relative to the first one, 0–100 (a non-zero step is at least 2 % to stay visible). */
export function funnelWidth(leads: number, start: number): number {
  if (start <= 0 || leads <= 0) return 0;
  return Math.max(2, Math.min(100, Math.round((leads / start) * 100)));
}

/** "0.0750" (USD) → "$0.075"; shows "—" for unknown. */
export function formatUnitCost(v: string | null | undefined): string {
  if (v === null || v === undefined) return "—";
  const n = Number(v);
  return `$${n.toLocaleString("en-US", { maximumFractionDigits: n >= 1 ? 2 : 4, minimumFractionDigits: n >= 1 ? 2 : 0 })}`;
}
