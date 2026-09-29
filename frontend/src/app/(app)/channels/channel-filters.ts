import type { ChannelFilters, ChannelSort, SortOrder } from "@/lib/api/hooks";
import { enumParam, intParam } from "@/lib/url-state";

export const CHANNEL_SORTS = ["subscribers", "views", "videos", "title", "published", "discovered"] as const satisfies
  readonly ChannelSort[];
const ORDERS = ["asc", "desc"] as const satisfies readonly SortOrder[];

/** URL search params -> API filters. Invalid values are dropped instead of producing a 422. */
export function filtersFromParams(params: URLSearchParams): ChannelFilters {
  const q = params.get("q")?.trim().slice(0, 200);
  const country = params.get("country")?.trim().toUpperCase();
  return {
    project_id: intParam(params, "project_id"),
    q: q || undefined,
    min_subscribers: intParam(params, "min_subscribers"),
    max_subscribers: intParam(params, "max_subscribers"),
    country: country && /^[A-Z]{2}$/.test(country) ? country : undefined,
    sort: enumParam(params, "sort", CHANNEL_SORTS) ?? "subscribers",
    order: enumParam(params, "order", ORDERS) ?? "desc",
  };
}

/** Clicking a header: same column flips the order; a new column starts with its natural order. */
export function nextSort(current: ChannelFilters, column: ChannelSort): { sort: ChannelSort; order: SortOrder } {
  if (current.sort === column) return { sort: column, order: current.order === "asc" ? "desc" : "asc" };
  return { sort: column, order: column === "title" ? "asc" : "desc" };
}
