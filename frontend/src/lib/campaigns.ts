/** Campaigns (Phase 8.2): labels, which actions a status allows, the settings form ↔ request, the send window.
 * Records keyed by API enums make `tsc` fail when the backend adds a status / stage without a label here. */
import { ApiError, type Schemas } from "@/lib/api/client";
import { localInputToIso, offerErrorMessage, type OfferStyle, type OutreachChannel } from "@/lib/offers";

export type CampaignStatus = Schemas["CampaignStatus"];
export type CampaignLeadStatus = Schemas["CampaignLeadStatus"];
export type CampaignStage = Schemas["CampaignLeadOut"]["stage"];
export type CampaignIssue = Schemas["CampaignOut"]["issues"][number];
type CampaignOut = Schemas["CampaignOut"];
type CampaignIn = Schemas["CampaignIn"];
type CampaignPatch = Schemas["CampaignPatch"];

type Tone = "neutral" | "green" | "blue" | "amber" | "red" | "violet";

/** Backend limits (campaigns/models.py, campaigns/schemas.py). */
export const MAX_CAMPAIGN_DAILY_LIMIT = 500;
export const MAX_FAILURES_LIMIT = 50;
export const MAX_CAMPAIGN_INSTRUCTIONS = 2000;

export const CAMPAIGN_STATUS_LABELS: Record<CampaignStatus, string> = {
  draft: "черновик",
  ready: "готова к запуску",
  pending_approval: "офферы на подтверждении",
  running: "идёт",
  paused: "на паузе",
  completed: "завершена",
  archived: "в архиве",
};

export const CAMPAIGN_STATUS_TONES: Record<CampaignStatus, Tone> = {
  draft: "neutral",
  ready: "blue",
  pending_approval: "amber",
  running: "green",
  paused: "amber",
  completed: "violet",
  archived: "neutral",
};

export const CAMPAIGN_LEAD_STATUS_LABELS: Record<CampaignLeadStatus, string> = {
  active: "в работе",
  sent: "отправлено",
  failed: "не отправлено",
  rejected: "оффер отклонён",
  skipped: "пропущен",
  removed: "убран",
};

export const STAGE_LABELS: Record<CampaignStage, string> = {
  no_offer: "оффер не написан",
  drafting: "пишется черновик",
  generation_failed: "черновик не получился",
  review: "ждёт подтверждения",
  approved: "подтверждён",
  queued: "в очереди на отправку",
  manual: "ждёт ручной отправки",
  send_cancelled: "отправка отменена",
  sent: "отправлено",
  replied: "ответил",
  failed: "не отправлено",
  rejected: "оффер отклонён",
  skipped: "пропущен",
  removed: "убран",
};

export const STAGE_TONES: Record<CampaignStage, Tone> = {
  no_offer: "neutral",
  drafting: "blue",
  generation_failed: "red",
  review: "amber",
  approved: "blue",
  queued: "violet",
  manual: "amber",
  send_cancelled: "neutral",
  sent: "green",
  replied: "green",
  failed: "red",
  rejected: "neutral",
  skipped: "neutral",
  removed: "neutral",
};

/** Funnel order of the active leads' stages. */
export const FUNNEL: readonly CampaignStage[] = [
  "no_offer",
  "drafting",
  "generation_failed",
  "review",
  "approved",
  "queued",
  "manual",
  "send_cancelled",
];

export const ISSUE_LABELS: Record<CampaignIssue, string> = {
  no_leads: "в кампании нет лидов в работе — добавьте их на странице «Лиды»",
  email_account_required: "email разрешён, но нет активного почтового аккаунта для отправки",
};

export const ADD_SKIP_LABELS: Record<string, string> = {
  already_in_campaign: "уже в этой кампании",
  other_campaign: "в работе в другой кампании",
  lead_status: "закрыт или уже получил письмо",
};

export const GENERATION_SKIP_LABELS: Record<string, string> = {
  no_contact: "нет контакта для каналов кампании",
  lead_status: "статус лида не позволяет писать оффер",
  generation_running: "идёт генерация превью",
  offer_generating: "черновик уже пишется",
};

export const WEEKDAYS: readonly { day: number; label: string }[] = [
  { day: 1, label: "Пн" },
  { day: 2, label: "Вт" },
  { day: 3, label: "Ср" },
  { day: 4, label: "Чт" },
  { day: 5, label: "Пт" },
  { day: 6, label: "Сб" },
  { day: 7, label: "Вс" },
];

/** Suggestions for the time zone field (any IANA name is accepted by the backend). */
export const TIMEZONES = [
  "Europe/Moscow",
  "Europe/Kaliningrad",
  "Europe/Samara",
  "Asia/Yekaterinburg",
  "Asia/Novosibirsk",
  "Asia/Vladivostok",
  "Europe/Kyiv",
  "Europe/Minsk",
  "Asia/Almaty",
  "Europe/Berlin",
  "Europe/London",
  "America/New_York",
  "UTC",
] as const;

// --- what a status allows (the backend checks again) ------------------------------------------

const OPEN: ReadonlySet<CampaignStatus> = new Set(["draft", "ready", "pending_approval", "running", "paused"]);
export const canEditCampaign = (s: CampaignStatus) => OPEN.has(s);
export const canAddLeads = canEditCampaign;
export const canGenerate = (s: CampaignStatus) => s === "ready" || s === "pending_approval" || s === "running" || s === "paused";
export const canStart = (s: CampaignStatus) => s === "draft" || s === "ready" || s === "pending_approval";
export const canPause = (s: CampaignStatus) => s === "running";
export const canResume = (s: CampaignStatus) => s === "paused";
export const canComplete = (s: CampaignStatus) => s === "pending_approval" || s === "running" || s === "paused";
export const canArchive = (s: CampaignStatus) =>
  s === "draft" || s === "ready" || s === "pending_approval" || s === "completed";
/** The campaign changes by itself (the minute tick, the dispatcher, the generation jobs): poll. */
export const isCampaignLive = (s: CampaignStatus) => s === "running" || s === "pending_approval" || s === "paused";

// --- formatting ---------------------------------------------------------------------------------

/** "10:00:00" → "10:00". */
export const hhmm = (t: string) => t.slice(0, 5);

/** «Пн–Пт», «Пн, Ср, Пт», «каждый день». */
export function formatDays(days: readonly number[]): string {
  const sorted = [...new Set(days)].sort((a, b) => a - b);
  if (sorted.length === 7) return "каждый день";
  const label = (d: number) => WEEKDAYS[d - 1]?.label ?? String(d);
  const contiguous = sorted.length > 2 && sorted.every((d, i) => i === 0 || d === sorted[i - 1] + 1);
  return contiguous ? `${label(sorted[0])}–${label(sorted[sorted.length - 1])}` : sorted.map(label).join(", ");
}

export function formatWindow(c: Pick<CampaignOut, "send_days" | "send_from" | "send_to" | "timezone">): string {
  return `${formatDays(c.send_days)}, ${hhmm(c.send_from)}–${hhmm(c.send_to)} (${c.timezone})`;
}

/** "0.2500" → "25 %"; null → "—". */
export function formatRate(rate: string | null | undefined): string {
  if (rate === null || rate === undefined) return "—";
  return `${Math.round(Number(rate) * 100)} %`;
}

// --- the settings form ----------------------------------------------------------------------------

export type CampaignForm = {
  name: string;
  description: string;
  project_id: number | null;
  template_id: number | null;
  instructions: string;
  language: string; // "" = the channel's language
  styles: OfferStyle[];
  channels: OutreachChannel[];
  email_account_ids: number[];
  send_days: number[];
  send_from: string; // "HH:MM"
  send_to: string;
  timezone: string;
  starts_at: string; // datetime-local value, "" = right away
  daily_limit: number;
  max_consecutive_failures: number;
};

export const DEFAULT_CAMPAIGN_FORM: CampaignForm = {
  name: "",
  description: "",
  project_id: null,
  template_id: null,
  instructions: "",
  language: "",
  styles: ["professional", "short"],
  channels: ["email"],
  email_account_ids: [],
  send_days: [1, 2, 3, 4, 5],
  send_from: "10:00",
  send_to: "18:00",
  timezone: "Europe/Moscow",
  starts_at: "",
  daily_limit: 20,
  max_consecutive_failures: 3,
};

/** ISO (UTC) → a datetime-local value in the browser's time zone. */
function isoToLocalInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function campaignForm(c: CampaignOut): CampaignForm {
  return {
    name: c.name,
    description: c.description,
    project_id: c.project_id,
    template_id: c.template_id,
    instructions: c.instructions,
    language: c.language ?? "",
    styles: [...c.styles],
    channels: [...c.channels],
    email_account_ids: c.email_accounts.map((a) => a.id),
    send_days: [...c.send_days].sort((a, b) => a - b),
    send_from: hhmm(c.send_from),
    send_to: hhmm(c.send_to),
    timezone: c.timezone,
    starts_at: isoToLocalInput(c.starts_at),
    daily_limit: c.daily_limit,
    max_consecutive_failures: c.max_consecutive_failures,
  };
}

const TIME = /^([01]\d|2[0-3]):[0-5]\d$/;
const LANGUAGE = /^[a-z]{2}(-[A-Za-z]{2,4})?$/;
const isInt = (n: number, lo: number, hi: number) => Number.isInteger(n) && n >= lo && n <= hi;

export function campaignFormErrors(f: CampaignForm): string[] {
  const errors: string[] = [];
  if (!f.name.trim()) errors.push("Укажите название кампании.");
  if (f.name.trim().length > 200) errors.push("Название — не длиннее 200 символов.");
  if (!f.styles.length) errors.push("Выберите хотя бы один вариант оффера (A/B).");
  if (!f.channels.length) errors.push("Выберите хотя бы один канал отправки.");
  if (!f.send_days.length) errors.push("Выберите хотя бы один день отправки.");
  if (!TIME.test(f.send_from) || !TIME.test(f.send_to)) errors.push("Время окна — в формате ЧЧ:ММ.");
  else if (f.send_from >= f.send_to) errors.push("Окно отправки должно заканчиваться позже, чем начинается.");
  if (!f.timezone.trim()) errors.push("Укажите часовой пояс, например Europe/Moscow.");
  if (f.language.trim() && !LANGUAGE.test(f.language.trim())) errors.push("Язык — код вида ru или en.");
  if (!isInt(f.daily_limit, 1, MAX_CAMPAIGN_DAILY_LIMIT)) errors.push(`Писем в день — от 1 до ${MAX_CAMPAIGN_DAILY_LIMIT}.`);
  if (!isInt(f.max_consecutive_failures, 1, MAX_FAILURES_LIMIT)) {
    errors.push(`Автопауза — после 1–${MAX_FAILURES_LIMIT} ошибок подряд.`);
  }
  if (f.instructions.length > MAX_CAMPAIGN_INSTRUCTIONS) {
    errors.push(`Инструкции — не длиннее ${MAX_CAMPAIGN_INSTRUCTIONS} символов.`);
  }
  if (f.starts_at && !localInputToIso(f.starts_at)) errors.push("Некорректная дата начала.");
  return errors;
}

export function campaignCreateRequest(f: CampaignForm): CampaignIn {
  return {
    name: f.name.trim(),
    description: f.description.trim(),
    project_id: f.project_id,
    template_id: f.template_id,
    instructions: f.instructions.trim(),
    language: f.language.trim() || null,
    styles: f.styles,
    channels: f.channels,
    email_account_ids: f.email_account_ids,
    send_days: [...f.send_days].sort((a, b) => a - b),
    send_from: `${f.send_from}:00`,
    send_to: `${f.send_to}:00`,
    timezone: f.timezone.trim(),
    starts_at: f.starts_at ? localInputToIso(f.starts_at) : null,
    daily_limit: f.daily_limit,
    max_consecutive_failures: f.max_consecutive_failures,
  };
}

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/** Only the fields that differ from the saved campaign. */
export function campaignPatchRequest(saved: CampaignForm, f: CampaignForm): CampaignPatch {
  const next = campaignCreateRequest(f);
  const prev = campaignCreateRequest(saved);
  const out: Record<string, unknown> = {};
  for (const k of Object.keys(next) as (keyof CampaignIn)[]) {
    if (!same(next[k], prev[k])) out[k] = next[k];
  }
  return out as CampaignPatch;
}

/** Toggle a value in a list keeping the given order (styles A/B/C, channel preference, weekdays). */
export function toggleIn<T>(list: readonly T[], value: T, order: readonly T[]): T[] {
  const set = new Set(list);
  if (set.has(value)) set.delete(value);
  else set.add(value);
  return order.filter((v) => set.has(v));
}

const ERRORS: Record<string, string> = {
  campaign_status: "Действие недоступно в текущем статусе кампании.",
  campaign_not_ready: "Кампанию пока нельзя запустить — см. что нужно сделать.",
  campaign_lead_status: "Лид уже не в работе в этой кампании.",
  invalid_window: "Окно отправки должно заканчиваться позже, чем начинается.",
  template_archived: "Шаблон оффера кампании в архиве — выберите другой.",
  nothing_to_generate: "Сейчас никому из лидов кампании не нужен оффер.",
  confirmation_required: "Цена или состав изменились — проверьте новую оценку и подтвердите снова.",
  message_sending: "Сообщение кампании отправляется прямо сейчас — повторите через минуту.",
};

export function campaignErrorMessage(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null;
  if (error.code === "campaign_not_ready") {
    const issues = ((error.details as { issues?: CampaignIssue[] } | null)?.issues ?? []).map((i) => ISSUE_LABELS[i] ?? i);
    if (issues.length) return `Кампанию пока нельзя запустить: ${issues.join("; ")}.`;
  }
  return ERRORS[error.code] ?? offerErrorMessage(error);
}
