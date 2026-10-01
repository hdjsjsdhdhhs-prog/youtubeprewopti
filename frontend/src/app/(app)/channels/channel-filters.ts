import type { ChannelFilterSet, ChannelFilters, ChannelSort, SortOrder } from "@/lib/api/hooks";
import { enumParam, intParam, type UrlPatch } from "@/lib/url-state";

export const CHANNEL_SORTS = [
  "subscribers",
  "views",
  "videos",
  "title",
  "published",
  "discovered",
  "avg_views",
  "median_views",
  "last_video",
  "views_ratio",
  "videos_30d",
] as const satisfies readonly ChannelSort[];
const ORDERS = ["asc", "desc"] as const satisfies readonly SortOrder[];

/** Whole-number filters of the §3 filter set (URL key == API key). */
export const INT_FILTERS = [
  "min_subscribers",
  "max_subscribers",
  "min_avg_views",
  "max_avg_views",
  "min_median_views",
  "min_last_video_views",
  "min_avg_views_recent",
  "min_videos",
  "max_videos",
  "min_videos_7d",
  "min_videos_30d",
  "min_videos_90d",
  "max_days_since_last_upload",
] as const satisfies readonly (keyof ChannelFilterSet)[];

/** Decimal filters with their allowed maximum. */
export const FLOAT_FILTERS = {
  max_avg_upload_gap_days: Infinity,
  min_views_to_subs: Infinity,
  min_upload_consistency: 1,
} as const satisfies Partial<Record<keyof ChannelFilterSet, number>>;

const LIST_FILTERS = ["niche", "exclude_niche"] as const satisfies readonly (keyof ChannelFilterSet)[];

/** Every URL key that belongs to the filter set (search, project and sorting are not part of it). */
export const FILTER_SET_KEYS = [
  ...INT_FILTERS,
  ...(Object.keys(FLOAT_FILTERS) as (keyof typeof FLOAT_FILTERS)[]),
  ...LIST_FILTERS,
  "country",
  "language",
] as const;

function floatParam(params: URLSearchParams, key: string, max: number): number | undefined {
  const raw = params.get(key);
  if (raw === null || raw.trim() === "") return undefined;
  const n = Number(raw);
  return Number.isFinite(n) && n >= 0 && n <= max ? n : undefined;
}

function idListParam(params: URLSearchParams, key: string): number[] | undefined {
  const ids = (params.get(key) ?? "")
    .split(",")
    .map((s) => Number(s.trim()))
    .filter((n) => Number.isInteger(n) && n > 0);
  return ids.length ? [...new Set(ids)].slice(0, 50) : undefined;
}

/** URL search params -> API filters. Invalid values are dropped instead of producing a 422. */
export function filtersFromParams(params: URLSearchParams): ChannelFilters {
  const q = params.get("q")?.trim().slice(0, 200);
  const country = params.get("country")?.trim().toUpperCase();
  const language = params.get("language")?.trim().slice(0, 20);
  const f: ChannelFilters = {
    project_id: intParam(params, "project_id"),
    q: q || undefined,
    country: country && /^[A-Z]{2}$/.test(country) ? country : undefined,
    language: language || undefined,
    sort: enumParam(params, "sort", CHANNEL_SORTS) ?? "subscribers",
    order: enumParam(params, "order", ORDERS) ?? "desc",
  };
  for (const key of INT_FILTERS) f[key] = intParam(params, key);
  for (const [key, max] of Object.entries(FLOAT_FILTERS) as [keyof typeof FLOAT_FILTERS, number][]) {
    f[key] = floatParam(params, key, max);
  }
  for (const key of LIST_FILTERS) f[key] = idListParam(params, key);
  return f;
}

function isSet(v: unknown): boolean {
  return Array.isArray(v) ? v.length > 0 : v !== undefined && v !== null && v !== "";
}

/** Number of active filter-set values (for the "more filters" badge). */
export function activeFilterCount(f: ChannelFilters): number {
  return FILTER_SET_KEYS.filter((k) => isSet(f[k])).length;
}

/** Current filters -> the project's saved ``filter_settings`` (only set values). */
export function filterSetFromFilters(f: ChannelFilters): ChannelFilterSet {
  const out: Record<string, unknown> = {};
  for (const k of FILTER_SET_KEYS) if (isSet(f[k])) out[k] = f[k];
  return out as ChannelFilterSet;
}

/** Saved ``filter_settings`` -> URL patch that replaces every filter-set key (unset ones are cleared). */
export function patchFromFilterSet(saved: Record<string, unknown>): UrlPatch {
  const patch: UrlPatch = {};
  for (const k of FILTER_SET_KEYS) {
    const v = saved[k];
    if (Array.isArray(v)) patch[k] = v.length ? v.join(",") : null;
    else if (typeof v === "number" || typeof v === "string") patch[k] = v;
    else patch[k] = null;
  }
  return patch;
}

export const CLEAR_FILTERS: UrlPatch = {
  project_id: null,
  q: null,
  ...Object.fromEntries(FILTER_SET_KEYS.map((k) => [k, null])),
};

/** Clicking a header: same column flips the order; a new column starts with its natural order. */
export function nextSort(current: ChannelFilters, column: ChannelSort): { sort: ChannelSort; order: SortOrder } {
  if (current.sort === column) return { sort: column, order: current.order === "asc" ? "desc" : "asc" };
  return { sort: column, order: column === "title" ? "asc" : "desc" };
}
