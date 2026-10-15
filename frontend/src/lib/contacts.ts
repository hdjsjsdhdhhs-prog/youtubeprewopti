/** Contacts (Phase 6, ТЗ §18–19): labels, safe links and form helpers. Records keyed by API enums make `tsc` fail
 * when the backend adds a type, status or source without a label here. */
import type { Schemas } from "@/lib/api/client";

export type ContactType = Schemas["ContactType"];
export type ContactStatus = Schemas["ContactStatusIn"]["status"];
export type ContactSource = Schemas["ContactSource"];
export type ScanStatus = Schemas["ChannelContacts"]["status"];
export type ContactWeights = Schemas["ContactabilityWeights"];
export type ContactWeightKey = keyof ContactWeights;

export const CONTACT_TYPE_LABELS: Record<ContactType, string> = {
  email: "Email",
  telegram: "Telegram",
  vk: "VK",
  instagram: "Instagram",
  website: "Сайт",
  other: "Другая соцсеть",
};
export const CONTACT_TYPES = Object.keys(CONTACT_TYPE_LABELS) as ContactType[];

export const CONTACT_STATUS_LABELS: Record<ContactStatus, string> = {
  public: "публичный",
  verified: "подтверждён",
  unverified: "не проверен",
  invalid: "не работает",
  opted_out: "отказался",
};
/** Statuses an operator can set on a contact (opted_out is final and has its own button). */
export const SETTABLE_STATUSES = ["verified", "unverified", "invalid"] as const satisfies readonly ContactStatus[];

export const SOURCE_LABELS: Record<ContactSource, string> = {
  youtube_about: "страница «О канале»",
  youtube_description: "описание канала",
  video_description: "описания видео",
  website: "сайт канала",
  linked_profile: "связанный профиль",
  manual: "добавлен вручную",
};

export const SCAN_STATUS_LABELS: Record<ScanStatus, string> = {
  not_scanned: "не собирались",
  found: "найдены",
  not_found: "не найдены",
};

export const SUPPRESSION_REASON_LABELS: Record<Schemas["SuppressionReason"], string> = {
  opt_out: "отказ от общения",
  bounce: "письмо не доставлено",
  manual: "вручную",
  complaint: "жалоба",
};
export const SUPPRESSION_REASONS = ["manual", "opt_out", "complaint", "bounce"] as const satisfies readonly Schemas["SuppressionReason"][];

/** Weight keys of the Contactability Score in display order (Instagram counts as «other»). */
export const WEIGHT_LABELS: Record<ContactWeightKey, string> = {
  email: "Email",
  telegram: "Telegram",
  website: "Сайт",
  vk: "VK",
  other: "Другие соцсети (вкл. Instagram)",
};
export const WEIGHT_KEYS = Object.keys(WEIGHT_LABELS) as ContactWeightKey[];

/** Tone of a 0–100 Contactability Score. */
export function contactabilityTone(score: number | string | null | undefined): "green" | "amber" | "neutral" {
  const s = Number(score);
  if (score === null || score === undefined || !Number.isFinite(s)) return "neutral";
  if (s >= 60) return "green";
  if (s > 0) return "amber";
  return "neutral";
}

/** «60» / «—» (never scanned = no data, which is not the same as 0). */
export function formatContactScore(score: string | number | null | undefined): string {
  if (score === null || score === undefined || score === "") return "—";
  const n = Number(score);
  return Number.isFinite(n) ? String(Math.round(n)) : "—";
}

const HTTP = /^https?:\/\//i;

/** A link for a contact, or null when none can be built safely. Websites and profiles open only with http(s)
 * (never `javascript:`); a Telegram invite link keeps its path. */
export function contactHref(type: ContactType, normalized: string, value: string): string | null {
  switch (type) {
    case "email":
      return /^[^\s@<>"]+@[^\s@<>"]+$/.test(normalized) ? `mailto:${normalized}` : null;
    case "telegram":
      if (normalized.startsWith("@")) return `https://t.me/${encodeURIComponent(normalized.slice(1))}`;
      return normalized.startsWith("t.me/") ? `https://${normalized}` : null;
    case "website":
      return HTTP.test(value) ? value : /^[\w.-]+\.[a-z]{2,}$/i.test(normalized) ? `https://${normalized}` : null;
    case "vk":
    case "instagram":
    case "other": {
      if (HTTP.test(value)) return value;
      if (type === "instagram" && normalized.startsWith("@")) return `https://instagram.com/${normalized.slice(1)}`;
      return /^[\w.-]+\.[a-z]{2,}\//i.test(normalized) ? `https://${normalized}` : null;
    }
  }
}

/** Weights: each 0–100 and at least one above 0 (the API rejects all-zero weights with 422). */
export function contactWeightsValid(w: Partial<Record<ContactWeightKey, number>>): boolean {
  return (
    WEIGHT_KEYS.some((k) => (w[k] ?? 0) > 0) &&
    WEIGHT_KEYS.every((k) => {
      const v = w[k] ?? 0;
      return Number.isFinite(v) && v >= 0 && v <= 100;
    })
  );
}

/** The score the weights give for a set of available types (capped at 100, a type counts once). */
export function previewContactScore(types: readonly ContactType[], w: Record<ContactWeightKey, number>): number {
  const keys = new Set<ContactWeightKey>(types.map((t) => (t === "instagram" ? "other" : (t as ContactWeightKey))));
  return Math.min(100, WEIGHT_KEYS.filter((k) => keys.has(k)).reduce((acc, k) => acc + (w[k] || 0), 0));
}

/** Short summary under the channel header: «Email, Telegram · 60/100». */
export function contactsSummary(c: { status: ScanStatus; usable_types: readonly ContactType[]; contactability_score: string | null }): string {
  if (c.status === "not_scanned") return "Контакты ещё не собирались";
  if (c.status === "not_found") return "Публичных контактов не найдено";
  const types = c.usable_types.map((t) => CONTACT_TYPE_LABELS[t]).join(", ");
  return `${types} · ${formatContactScore(c.contactability_score)}/100`;
}
