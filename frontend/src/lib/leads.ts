/** Leads (Phase 3.7): labels, URL filters and helpers. Records keyed by API enums make `tsc` fail when the backend
 * adds a status, component or sort without a label here. */
import type { Schemas } from "@/lib/api/client";
import { enumParam, intParam, type UrlPatch } from "@/lib/url-state";

export type LeadStatus = Schemas["LeadStatus"];
export type LeadSort = Schemas["LeadSort"];
export type ActivityLevel = Schemas["ActivityLevel"];
export type ComponentKey = keyof Schemas["PriorityWeights"];
type Order = Schemas["SortOrder"];

export const STATUS_LABELS: Record<LeadStatus, string> = {
  discovered: "Найден",
  analyzed: "Проанализирован",
  shortlisted: "Шорт-лист",
  selected: "Выбран",
  generation_pending: "Генерация",
  generated: "Сгенерировано",
  offer_draft: "Черновик оффера",
  pending_approval: "На подтверждении",
  approved: "Подтверждён",
  scheduled: "Запланирован",
  sent: "Отправлен",
  replied: "Ответил",
  declined: "Отказ",
  opted_out: "Отписался",
  failed: "Ошибка",
  rejected: "Отклонён",
};
export const LEAD_STATUSES = Object.keys(STATUS_LABELS) as LeadStatus[];

/** Display order = backend `COMPONENTS` order. */
export const COMPONENT_LABELS: Record<ComponentKey, string> = {
  thumbnail_opportunity: "Возможность по превью",
  channel_fit: "Соответствие профилю",
  audience: "Аудитория",
  views: "Просмотры",
  activity: "Активность",
  growth: "Рост",
  contactability: "Контактность",
  niche_value: "Ценность ниши",
};
export const COMPONENTS = Object.keys(COMPONENT_LABELS) as ComponentKey[];

export const COMPONENT_HINTS: Record<ComponentKey, string> = {
  thumbnail_opportunity: "Слабые превью, потенциал редизайна и визуальная возможность канала",
  channel_fit: "Доля критериев профиля идеального лида, которым канал соответствует",
  audience: "Подписчики по логарифмической шкале 1 тыс. → 1 млн",
  views: "Медиана просмотров по логарифмической шкале 500 → 500 тыс.",
  activity: "Давность последнего видео и число видео за 30 дней",
  growth: "Тренд просмотров и рост подписчиков по снимкам",
  contactability: "Появится со сбором контактов (Phase 6); пока нет данных",
  niche_value: "Оценка ценности ниши из таблицы ниже (0–10)",
};

export const ACTIVITY_LABELS: Record<ActivityLevel, string> = { low: "низкая", medium: "средняя", high: "высокая" };
export const ACTIVITY_LEVELS = Object.keys(ACTIVITY_LABELS) as ActivityLevel[];

export const SORT_LABELS: Record<LeadSort, string> = {
  lead_score: "Lead Score",
  priority: "Приоритет (ценность)",
  thumbnail_score: "Балл превью",
  thumbnail_opportunity: "Возможность по превью",
  visual_opportunity: "Визуальная возможность",
  views: "Просмотры",
  subscribers: "Подписчики",
  activity: "Активность",
  contactability: "Контактность",
  last_video: "Дата последнего видео",
  created: "Дата появления лида",
};
export const LEAD_SORTS = Object.keys(SORT_LABELS) as LeadSort[];
const ORDERS = ["asc", "desc"] as const satisfies readonly Order[];
const LEVELS = ["low", "medium", "high"] as const;

/** Filters + paging of the leads page, mirrored in the URL (ТЗ §9: all filters work together). */
export type LeadQuery = {
  project_id?: number;
  channel_id?: number; // not in the URL: the channel card asks for its own lead
  q?: string;
  status?: LeadStatus[];
  min_thumbnail_score?: number;
  max_thumbnail_score?: number;
  min_lead_score?: number;
  max_lead_score?: number;
  min_priority?: number;
  min_subscribers?: number;
  max_subscribers?: number;
  min_avg_views?: number;
  max_days_since_last_upload?: number;
  activity?: ActivityLevel;
  visual_opportunity?: (typeof LEVELS)[number];
  niche?: number[];
  sort: LeadSort;
  order: Order;
  offset: number;
};

export const INT_KEYS = ["min_subscribers", "max_subscribers", "min_avg_views", "max_days_since_last_upload"] as const;
/** Decimal filters with their range. */
export const RANGE_KEYS = {
  min_thumbnail_score: [1, 10],
  max_thumbnail_score: [1, 10],
  min_lead_score: [0, 100],
  max_lead_score: [0, 100],
  min_priority: [0, 100],
} as const satisfies Partial<Record<keyof LeadQuery, readonly [number, number]>>;
type RangeKey = keyof typeof RANGE_KEYS;

function rangeParam(params: URLSearchParams, key: string, [lo, hi]: readonly [number, number]): number | undefined {
  const raw = params.get(key);
  if (raw === null || raw.trim() === "") return undefined;
  const n = Number(raw.replace(",", "."));
  return Number.isFinite(n) && n >= lo && n <= hi ? n : undefined;
}

function listParam(params: URLSearchParams, key: string): string[] {
  return (params.get(key) ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

/** URL -> API query; invalid values are dropped instead of producing a 422. */
export function leadQueryFromParams(params: URLSearchParams): LeadQuery {
  const q = params.get("q")?.trim().slice(0, 200);
  const status = [...new Set(listParam(params, "status"))].filter((s): s is LeadStatus =>
    (LEAD_STATUSES as string[]).includes(s),
  );
  const niche = [...new Set(listParam(params, "niche").map(Number))].filter((n) => Number.isInteger(n) && n > 0);
  const out: LeadQuery = {
    project_id: intParam(params, "project_id"),
    q: q || undefined,
    status: status.length ? status : undefined,
    activity: enumParam(params, "activity", ACTIVITY_LEVELS),
    visual_opportunity: enumParam(params, "visual_opportunity", LEVELS),
    niche: niche.length ? niche.slice(0, 50) : undefined,
    sort: enumParam(params, "sort", LEAD_SORTS) ?? "lead_score",
    order: enumParam(params, "order", ORDERS) ?? "desc",
    offset: intParam(params, "offset") ?? 0,
  };
  for (const key of INT_KEYS) out[key] = intParam(params, key);
  for (const [key, range] of Object.entries(RANGE_KEYS) as [RangeKey, readonly [number, number]][]) {
    out[key] = rangeParam(params, key, range);
  }
  return out;
}

const FILTER_KEYS = [
  "project_id", "q", "status", "activity", "visual_opportunity", "niche", ...INT_KEYS,
  ...(Object.keys(RANGE_KEYS) as RangeKey[]),
] as const;

export function hasLeadFilters(q: LeadQuery): boolean {
  return FILTER_KEYS.some((k) => {
    const v = q[k];
    return Array.isArray(v) ? v.length > 0 : v !== undefined;
  });
}

export const CLEAR_LEAD_FILTERS: UrlPatch = { ...Object.fromEntries(FILTER_KEYS.map((k) => [k, null])), offset: null };

/** Natural first order of a sort: weakest thumbnails first, everything else highest first. */
export function defaultLeadOrder(sort: LeadSort): Order {
  return sort === "thumbnail_score" ? "asc" : "desc";
}

/** Clicking a header: the same column flips the order; a new one starts with its natural order. */
export function nextLeadSort(current: LeadQuery, sort: LeadSort): UrlPatch {
  if (current.sort === sort) return { sort, order: current.order === "asc" ? "desc" : "asc", offset: null };
  return { sort, order: defaultLeadOrder(sort), offset: null };
}

/** Badge tone of a 0–100 lead / priority score. */
export function leadScoreTone(score: number | string | null | undefined): "green" | "amber" | "neutral" {
  const s = Number(score);
  if (!Number.isFinite(s)) return "neutral";
  if (s >= 70) return "green";
  if (s >= 40) return "amber";
  return "neutral";
}

/** "23 / 25" — points out of the component's maximum, as in ТЗ §8. */
export function formatPoints(points: string | number, max: string | number): string {
  const fmt = (v: string | number) => {
    const n = Number(v);
    return Number.isInteger(n) ? String(n) : n.toFixed(1).replace(".", ",");
  };
  return `${fmt(points)} / ${fmt(max)}`;
}

export function formatLeadScore(score: string | number | null | undefined): string {
  if (score === null || score === undefined || score === "") return "—";
  const n = Number(score);
  return Number.isFinite(n) ? String(Math.round(n)) : "—";
}

/** Width (0–100 %) of a component bar. */
export function pointsWidth(points: string | number, max: string | number): number {
  const p = Number(points);
  const m = Number(max);
  if (!(m > 0) || !Number.isFinite(p)) return 0;
  return Math.min(100, Math.max(0, (p / m) * 100));
}

// --- Ideal Lead Profile form (§52) -----------------------------------------------------------

type IdealLeadCriteria = Schemas["IdealLeadCriteria"];

/** Editable form state: numbers as strings (empty = criterion not set), lists as comma-separated text. */
export type CriteriaForm = {
  name: string;
  is_default: boolean;
  min_subscribers: string;
  max_subscribers: string;
  min_avg_views: string;
  min_activity: "" | ActivityLevel;
  max_days_since_last_upload: string;
  max_thumbnail_score: string;
  niches: number[];
  countries: string;
  languages: string;
  contact_types: ("email" | "telegram")[];
};

const numText = (v: number | null | undefined) => (v === null || v === undefined ? "" : String(v));

export function formFromProfile(p: { name: string; is_default: boolean; criteria: IdealLeadCriteria } | null): CriteriaForm {
  const c: IdealLeadCriteria = p?.criteria ?? {};
  return {
    name: p?.name ?? "",
    is_default: p?.is_default ?? true,
    min_subscribers: numText(c.min_subscribers),
    max_subscribers: numText(c.max_subscribers),
    min_avg_views: numText(c.min_avg_views),
    min_activity: c.min_activity ?? "",
    max_days_since_last_upload: numText(c.max_days_since_last_upload),
    max_thumbnail_score: numText(c.max_thumbnail_score),
    niches: c.niches ?? [],
    countries: (c.countries ?? []).join(", "),
    languages: (c.languages ?? []).join(", "),
    contact_types: c.contact_types ?? [],
  };
}

/** Form -> API criteria; empty fields are unset criteria (ignored by Channel Fit). */
export function criteriaFromForm(f: CriteriaForm): IdealLeadCriteria {
  const n = (v: string) => (v.trim() === "" ? null : Number(v.replace(",", ".")));
  const list = (v: string) =>
    v
      .split(/[,\s]+/)
      .map((s) => s.trim())
      .filter(Boolean);
  return {
    min_subscribers: n(f.min_subscribers),
    max_subscribers: n(f.max_subscribers),
    min_avg_views: n(f.min_avg_views),
    min_activity: f.min_activity || null,
    max_days_since_last_upload: n(f.max_days_since_last_upload),
    max_thumbnail_score: n(f.max_thumbnail_score),
    niches: f.niches,
    countries: [...new Set(list(f.countries).map((s) => s.toUpperCase()))],
    languages: [...new Set(list(f.languages).map((s) => s.toLowerCase()))],
    contact_types: f.contact_types,
  };
}

/** Weights must have at least one positive value (the API rejects all-zero weights with 422). */
export function weightsValid(weights: Partial<Record<ComponentKey, number>>): boolean {
  return COMPONENTS.some((k) => (weights[k] ?? 0) > 0) && COMPONENTS.every((k) => {
    const v = weights[k] ?? 0;
    return Number.isFinite(v) && v >= 0 && v <= 100;
  });
}
