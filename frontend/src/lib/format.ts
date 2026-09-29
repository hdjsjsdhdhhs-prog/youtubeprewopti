const compact = new Intl.NumberFormat("ru-RU", { notation: "compact", maximumFractionDigits: 1 });
const full = new Intl.NumberFormat("ru-RU");
const dateTime = new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short" });
const dateOnly = new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium" });

export function formatCount(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n >= 10_000 ? compact.format(n) : full.format(n);
}

export function formatDateTime(iso: string | null | undefined): string {
  return iso ? dateTime.format(new Date(iso)) : "—";
}

export function formatDate(iso: string | null | undefined): string {
  return iso ? dateOnly.format(new Date(iso)) : "—";
}

/** Video length as on YouTube: "4:05", "1:02:03". */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || seconds < 0) return "—";
  const s = Math.floor(seconds % 60);
  const m = Math.floor(seconds / 60) % 60;
  const h = Math.floor(seconds / 3600);
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

/** Human duration between two timestamps (or until now), e.g. "1 мин 5 с". */
export function formatElapsed(fromIso: string | null, toIso: string | null, now = Date.now()): string {
  if (!fromIso) return "—";
  const ms = Math.max(0, (toIso ? Date.parse(toIso) : now) - Date.parse(fromIso));
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s} с`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} мин ${s % 60} с`;
  return `${Math.floor(m / 60)} ч ${m % 60} мин`;
}
