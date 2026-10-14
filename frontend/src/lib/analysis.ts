/** Labels and helpers for the AI thumbnail audit (Phase 3.5). Codes mirror backend/app/domains/analysis/audit.py;
 * the Record types make `tsc` fail when the backend adds a code or a subscore without a label here. */
import type { Schemas } from "@/lib/api/client";

export type Level = "low" | "medium" | "high";
export type ProblemCode = Schemas["ProblemCode"];
export type Subscore = keyof Schemas["ThumbnailWeights"];

export const SUBSCORE_LABELS: Record<Subscore, string> = {
  composition_score: "Композиция",
  contrast_score: "Контраст",
  text_score: "Текст",
  hierarchy_score: "Иерархия",
  subject_score: "Главный объект",
  color_score: "Цвет",
  mobile_readability: "Читаемость на телефоне",
  topic_relevance: "Соответствие теме",
  visual_impact: "Визуальный эффект",
  professionalism: "Профессионализм",
  redesign_potential: "Потенциал редизайна",
};

/** Display order = backend order. */
export const SUBSCORES = Object.keys(SUBSCORE_LABELS) as Subscore[];

export const PROBLEM_LABELS: Record<ProblemCode, string> = {
  weak_hierarchy: "Слабая иерархия",
  no_focal_point: "Нет точки фокуса",
  low_subject_separation: "Объект сливается с фоном",
  weak_subject: "Слабый главный объект",
  small_text: "Мелкий текст",
  too_much_text: "Слишком много текста",
  unreadable_text: "Нечитаемый текст",
  low_contrast: "Низкий контраст",
  dull_colors: "Тусклые цвета",
  clashing_colors: "Конфликт цветов",
  cluttered: "Перегружено",
  poor_composition: "Слабая композиция",
  poor_mobile_readability: "Плохо читается на телефоне",
  weak_emotion: "Слабая эмоция",
  low_image_quality: "Низкое качество изображения",
  off_topic: "Не соответствует теме",
  generic_look: "Шаблонный вид",
  outdated_style: "Устаревший стиль",
  other: "Другое",
};

export const PROBLEM_CODES = Object.keys(PROBLEM_LABELS) as ProblemCode[];

export function problemLabel(code: string): string {
  return (PROBLEM_LABELS as Record<string, string>)[code] ?? code;
}

export const LEVEL_LABELS: Record<Level, string> = { low: "низкий", medium: "средний", high: "высокий" };

/** Badge tone of an overall/sub score (1–10, higher = better). */
export function scoreTone(score: number | string): "red" | "amber" | "green" {
  const s = typeof score === "string" ? Number(score) : score;
  if (s < 5) return "red";
  if (s < 7) return "amber";
  return "green";
}

/** Tone of the improvement opportunity: high opportunity = a good lead, hence green. */
export function opportunityTone(level: Level): "green" | "amber" | "neutral" {
  return level === "high" ? "green" : level === "medium" ? "amber" : "neutral";
}

/** Tone of a "higher = worse" channel score (outdated style): the inverse of `scoreTone`. */
export function inverseScoreTone(score: number | string): "red" | "amber" | "green" {
  const s = typeof score === "string" ? Number(score) : score;
  if (s >= 7) return "red";
  if (s >= 5) return "amber";
  return "green";
}

/** Weak / medium / strong shares of a channel's thumbnails (API: percentages as decimal strings). Widths are
 * clamped so a rounding sum of 100,1 % never overflows the bar. */
export function bandWidths(a: { pct_weak: string; pct_medium: string; pct_strong: string }): [number, number, number] {
  const clamp = (v: string) => Math.min(100, Math.max(0, Number(v) || 0));
  const weak = clamp(a.pct_weak);
  const medium = Math.min(clamp(a.pct_medium), 100 - weak);
  return [weak, medium, Math.min(clamp(a.pct_strong), 100 - weak - medium)];
}

export function formatPercent(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const v = typeof value === "string" ? Number(value) : value;
  return Number.isFinite(v) ? `${Math.round(v)} %` : "—";
}

export function formatScore(score: number | string | null | undefined): string {
  if (score === null || score === undefined || score === "") return "—";
  const s = typeof score === "string" ? Number(score) : score;
  return Number.isFinite(s) ? s.toFixed(1).replace(".", ",") : "—";
}
