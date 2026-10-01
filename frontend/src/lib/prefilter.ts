import type { Schemas } from "./api/client";

export type ThumbnailPrefilter = Schemas["ThumbnailPrefilter"];
export type MetricKey = keyof Schemas["MetricBounds"];

/** Deterministic image metrics (backend: app/domains/media/imaging.py) with their scales. */
export const METRIC_FIELDS: { key: MetricKey; label: string; hint: string; step: string }[] = [
  { key: "luminance_mean", label: "Яркость", hint: "0 — чёрное, 1 — белое", step: "0.01" },
  { key: "contrast_rms", label: "Контраст", hint: "0 — однотонное, ~0,5 — максимум", step: "0.01" },
  { key: "colorfulness", label: "Насыщенность цвета", hint: "0 — серое, >100 — очень яркие цвета", step: "1" },
  { key: "sharpness_laplacian", label: "Резкость", hint: "чем больше, тем резче (сотни–тысячи)", step: "10" },
  { key: "edge_density", label: "Плотность деталей", hint: "0..1 — доля пикселей на контурах", step: "0.01" },
  { key: "saliency_center_ratio", label: "Объект в центре", hint: "0..1; 0,25 — детали распределены равномерно", step: "0.01" },
];

export const PREFILTER_DEFAULTS = { videos_per_channel: 6, exclude_shorts: true, apply_channel_filters: true };

export type PrefilterForm = {
  videos_per_channel: string;
  exclude_shorts: boolean;
  apply_channel_filters: boolean;
  max_video_age_days: string;
  min_video_views: string;
  metrics: Record<MetricKey, { min: string; max: string }>;
};

const str = (v: unknown): string => (typeof v === "number" && Number.isFinite(v) ? String(v) : "");

/** Saved ``prefilter_settings`` ({} = defaults) → form state. Unknown keys are ignored. */
export function formFromPrefilter(saved: Record<string, unknown> | null | undefined): PrefilterForm {
  const s = (saved ?? {}) as Partial<ThumbnailPrefilter>;
  const metrics = {} as PrefilterForm["metrics"];
  for (const { key } of METRIC_FIELDS) {
    const r = s.metrics?.[key];
    metrics[key] = { min: str(r?.min), max: str(r?.max) };
  }
  return {
    videos_per_channel: str(s.videos_per_channel ?? PREFILTER_DEFAULTS.videos_per_channel),
    exclude_shorts: s.exclude_shorts ?? PREFILTER_DEFAULTS.exclude_shorts,
    apply_channel_filters: s.apply_channel_filters ?? PREFILTER_DEFAULTS.apply_channel_filters,
    max_video_age_days: str(s.max_video_age_days),
    min_video_views: str(s.min_video_views),
    metrics,
  };
}

/** "1,5" and "1.5" are both accepted (Russian keyboards). Empty => null. */
export function parseNumber(raw: string): number | null | "invalid" {
  const t = raw.trim().replace(",", ".");
  if (t === "") return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : "invalid";
}

/** Form → API body, with the same rules the backend enforces (so errors show before the request). */
export function prefilterFromForm(f: PrefilterForm): { value: ThumbnailPrefilter; errors: string[] } {
  const errors: string[] = [];
  const int = (raw: string, label: string, min: number, max: number): number | null => {
    const n = parseNumber(raw);
    if (n === null) return null;
    if (n === "invalid" || !Number.isInteger(n) || n < min || n > max) {
      errors.push(`${label}: целое число от ${min} до ${max}`);
      return null;
    }
    return n;
  };
  // validated in the order of the form fields, so messages read top to bottom
  const perChannel = int(f.videos_per_channel, "Видео на канал", 1, 50);
  const maxAge = int(f.max_video_age_days, "Не старше (дней)", 1, 3650);
  const minViews = int(f.min_video_views, "Минимум просмотров", 0, Number.MAX_SAFE_INTEGER);
  const metrics: NonNullable<ThumbnailPrefilter["metrics"]> = {};
  for (const { key, label } of METRIC_FIELDS) {
    const lo = parseNumber(f.metrics[key].min);
    const hi = parseNumber(f.metrics[key].max);
    if (lo === "invalid" || hi === "invalid") {
      errors.push(`${label}: введите число`);
      continue;
    }
    if (lo !== null && hi !== null && lo > hi) {
      errors.push(`${label}: минимум больше максимума`);
      continue;
    }
    if (lo !== null || hi !== null) metrics[key] = { min: lo, max: hi };
  }
  return {
    value: {
      videos_per_channel: perChannel ?? PREFILTER_DEFAULTS.videos_per_channel,
      exclude_shorts: f.exclude_shorts,
      apply_channel_filters: f.apply_channel_filters,
      max_video_age_days: maxAge,
      min_video_views: minViews,
      metrics,
    },
    errors,
  };
}

/** USD amounts arrive as decimal strings. Small amounts keep enough digits to be meaningful. */
export function formatUsd(v: string | number | null | undefined): string {
  if (v === null || v === undefined || v === "") return "—";
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return "—";
  if (n === 0) return "$0";
  const digits = n >= 1 ? 2 : n >= 0.01 ? 4 : 6;
  return `$${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: digits })}`;
}
