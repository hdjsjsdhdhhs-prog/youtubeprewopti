/** Human workflow (Phase 4): manual statuses, selection for bulk actions, tags and human scores. Pure helpers. */
import type { Schemas } from "@/lib/api/client";
import type { LeadQuery, LeadStatus } from "@/lib/leads";

export type ManualStatus = "analyzed" | "shortlisted" | "selected" | "rejected";
export type ScoreOverride = Schemas["ScoreOverrideOut"];
export type LeadFiltersBody = Schemas["LeadFilters"];
export type LeadSelectionBody = Schemas["LeadSelection"];

/** Statuses an operator sets by hand (backend `workflow.MANUAL_STATUSES`). */
export const MANUAL_STATUSES: readonly ManualStatus[] = ["shortlisted", "selected", "analyzed", "rejected"];

/** Manual targets per status — the manual part of the backend `LEAD_MACHINE`. «analyzed» from «discovered» is
 * left out on purpose: the system sets it once thumbnails are audited. */
const MANUAL_TRANSITIONS: Record<LeadStatus, readonly ManualStatus[]> = {
  discovered: ["shortlisted", "selected", "rejected"],
  analyzed: ["shortlisted", "selected", "rejected"],
  shortlisted: ["selected", "analyzed", "rejected"],
  selected: ["shortlisted", "rejected"],
  generation_pending: ["rejected"],
  generated: ["rejected"],
  offer_draft: ["rejected"],
  pending_approval: ["rejected"],
  approved: ["rejected"],
  scheduled: ["rejected"],
  failed: ["selected", "rejected"],
  rejected: ["shortlisted"],
  sent: [],
  replied: [],
  declined: [],
  opted_out: [],
};

export function manualTargets(status: LeadStatus): readonly ManualStatus[] {
  return MANUAL_TRANSITIONS[status];
}

/** Button label of a transition (depends on where the lead comes from). */
export function actionLabel(from: LeadStatus | null, to: ManualStatus): string {
  if (to === "shortlisted") return from === "rejected" ? "Восстановить" : from === "selected" ? "Вернуть в шорт-лист" : "В шорт-лист";
  if (to === "selected") return "Выбрать";
  if (to === "analyzed") return "Убрать из шорт-листа";
  return "Отклонить";
}

/** Bulk labels (the source status differs per lead). */
export const BULK_STATUS_LABELS: Record<ManualStatus, string> = {
  shortlisted: "В шорт-лист",
  selected: "Выбрать",
  analyzed: "Убрать из шорт-листа",
  rejected: "Отклонить",
};

// --- selection (ТЗ §11) ------------------------------------------------------------------------

/** Either explicit rows, or «all filtered» minus unticked rows. */
export type Selection = { mode: "ids"; ids: ReadonlySet<number> } | { mode: "all"; excluded: ReadonlySet<number> };

export const EMPTY_SELECTION: Selection = { mode: "ids", ids: new Set() };
export const MAX_BULK_LEADS = 5000;

export function isSelected(sel: Selection, id: number): boolean {
  return sel.mode === "ids" ? sel.ids.has(id) : !sel.excluded.has(id);
}

export function toggleOne(sel: Selection, id: number): Selection {
  if (sel.mode === "ids") {
    const ids = new Set(sel.ids);
    if (ids.has(id)) ids.delete(id);
    else ids.add(id);
    return { mode: "ids", ids };
  }
  const excluded = new Set(sel.excluded);
  if (excluded.has(id)) excluded.delete(id);
  else excluded.add(id);
  return { mode: "all", excluded };
}

/** Header checkbox: tick / untick the rows of the current page. */
export function togglePage(sel: Selection, pageIds: readonly number[]): Selection {
  const allOn = pageIds.length > 0 && pageIds.every((id) => isSelected(sel, id));
  if (sel.mode === "ids") {
    const ids = new Set(sel.ids);
    for (const id of pageIds) {
      if (allOn) ids.delete(id);
      else ids.add(id);
    }
    return { mode: "ids", ids };
  }
  const excluded = new Set(sel.excluded);
  for (const id of pageIds) {
    if (allOn) excluded.add(id);
    else excluded.delete(id);
  }
  return { mode: "all", excluded };
}

export function selectedCount(sel: Selection, total: number): number {
  return sel.mode === "ids" ? sel.ids.size : Math.max(0, total - sel.excluded.size);
}

/** The list query -> the API filter body (paging and sorting are not filters). */
export function filtersFromQuery(q: LeadQuery): LeadFiltersBody {
  const { sort: _sort, order: _order, offset: _offset, ...filters } = q;
  void _sort;
  void _order;
  void _offset;
  return filters;
}

export function selectionBody(sel: Selection, q: LeadQuery): LeadSelectionBody {
  if (sel.mode === "ids") return { lead_ids: [...sel.ids] };
  return { filters: filtersFromQuery(q), exclude_ids: [...sel.excluded] };
}

// --- human scores (ТЗ §47) ---------------------------------------------------------------------

export function humanValue(overrides: readonly ScoreOverride[] | undefined, field: string): ScoreOverride | undefined {
  return overrides?.find((o) => o.field === field);
}

/** Parses a typed score ("5,5" or "5.5") within [min, max] with at most 2 decimals; null = invalid. */
export function parseScore(raw: string, min: number, max: number): number | null {
  const s = raw.trim().replace(",", ".");
  if (!/^\d+(\.\d{1,2})?$/.test(s)) return null;
  const n = Number(s);
  return n >= min && n <= max ? n : null;
}

// --- tags --------------------------------------------------------------------------------------

/** Inline style of a tag chip: the tag color as a tinted background with a readable border. */
export function tagStyle(color: string | null | undefined): { backgroundColor?: string; borderColor?: string } {
  if (!color || !/^#[0-9a-fA-F]{6}$/.test(color)) return {};
  return { backgroundColor: `${color}26`, borderColor: color };
}
