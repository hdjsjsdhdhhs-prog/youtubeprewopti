/** Outreach (Phase 8.1): SMTP accounts, outgoing messages and their events — labels, the account form → request.
 * Records keyed by API enums make `tsc` fail when the backend adds a status / event without a label here. */
import type { Schemas } from "@/lib/api/client";

export type MessageStatus = Schemas["MessageStatus"];
export type MessageEventType = Schemas["MessageEventType"];
export type ManualEventType = Schemas["MessageEventIn"]["type"];
export type AccountStatus = Schemas["AccountStatus"];
export type SmtpSecurity = Schemas["SmtpSecurityMode"];
type EmailAccountIn = Schemas["EmailAccountIn"];
type EmailAccountPatch = Schemas["EmailAccountPatch"];
type EmailAccountOut = Schemas["EmailAccountOut"];

type Tone = "neutral" | "green" | "blue" | "amber" | "red" | "violet";

/** Backend limits (outreach/models.py, outreach/schemas.py). */
export const MAX_DAILY_LIMIT = 500;
export const MAX_COOLDOWN_SECONDS = 86_400;
export const MAX_OPT_OUT = 1000;

export const MESSAGE_STATUS_LABELS: Record<MessageStatus, string> = {
  queued: "в очереди",
  sending: "отправляется",
  sent: "отправлено",
  failed: "не отправлено",
  cancelled: "отменено",
  manual: "ждёт ручной отправки",
};

export const MESSAGE_STATUS_TONES: Record<MessageStatus, Tone> = {
  queued: "violet",
  sending: "blue",
  sent: "green",
  failed: "red",
  cancelled: "neutral",
  manual: "amber",
};

/** Tabs of the «Рассылка» page, in this order. */
export const MESSAGE_TABS: readonly MessageStatus[] = ["queued", "manual", "sending", "sent", "failed", "cancelled"];

export const MESSAGE_EVENT_LABELS: Record<MessageEventType, string> = {
  sent: "отправлено",
  delivered: "доставлено",
  opened: "открыто",
  replied: "ответ",
  positive_reply: "положительный ответ",
  negative_reply: "отрицательный ответ",
  bounced: "недоставка (bounce)",
  opt_out: "просит не писать",
  failed: "ошибка отправки",
  cancelled: "отменено",
};

/** Events the operator records on a sent message (SMTP reports none of them), in menu order. */
export const MANUAL_EVENTS: readonly ManualEventType[] = [
  "delivered",
  "opened",
  "replied",
  "positive_reply",
  "negative_reply",
  "bounced",
  "opt_out",
];

/** Events that put the address on the «Не писать» list. */
export const SUPPRESSING_EVENTS: ReadonlySet<ManualEventType> = new Set(["bounced", "opt_out"]);

export const ACCOUNT_STATUS_LABELS: Record<AccountStatus, string> = {
  active: "активен",
  paused: "на паузе",
  error: "ошибка входа",
};

export const ACCOUNT_STATUS_TONES: Record<AccountStatus, Tone> = { active: "green", paused: "amber", error: "red" };

export const SMTP_SECURITY_LABELS: Record<SmtpSecurity, string> = {
  starttls: "STARTTLS",
  ssl: "SSL/TLS",
  none: "без шифрования",
};

export const DEFAULT_PORTS: Record<SmtpSecurity, number> = { starttls: 587, ssl: 465, none: 25 };

/** The message can still change by itself (dispatcher / worker): poll. */
export function isMessageActive(status: MessageStatus): boolean {
  return status === "queued" || status === "sending";
}

export function canCancelMessage(status: MessageStatus): boolean {
  return status === "queued" || status === "manual";
}

/** «60–180 с», «2–5 мин». */
export function formatCooldown(min: number, max: number): string {
  if (max >= 120 && min % 60 === 0 && max % 60 === 0) return `${min / 60}–${max / 60} мин`;
  return min === max ? `${min} с` : `${min}–${max} с`;
}

// --- the account form --------------------------------------------------------------------------

export type AccountForm = {
  name: string;
  from_email: string;
  from_name: string;
  reply_to: string;
  smtp_host: string;
  smtp_port: number;
  smtp_security: SmtpSecurity;
  smtp_username: string;
  password: string; // "" = keep the stored one (edit) / no password (create)
  daily_limit: number;
  cooldown_min_seconds: number;
  cooldown_max_seconds: number;
  opt_out_text: string; // "" on create = the backend default
};

export const DEFAULT_ACCOUNT_FORM: AccountForm = {
  name: "",
  from_email: "",
  from_name: "",
  reply_to: "",
  smtp_host: "",
  smtp_port: DEFAULT_PORTS.starttls,
  smtp_security: "starttls",
  smtp_username: "",
  password: "",
  daily_limit: 20,
  cooldown_min_seconds: 60,
  cooldown_max_seconds: 180,
  opt_out_text: "",
};

export function accountForm(a: EmailAccountOut): AccountForm {
  return {
    name: a.name,
    from_email: a.from_email,
    from_name: a.from_name,
    reply_to: a.reply_to ?? "",
    smtp_host: a.smtp_host,
    smtp_port: a.smtp_port,
    smtp_security: a.smtp_security,
    smtp_username: a.smtp_username,
    password: "",
    daily_limit: a.daily_limit,
    cooldown_min_seconds: a.cooldown_min_seconds,
    cooldown_max_seconds: a.cooldown_max_seconds,
    opt_out_text: a.opt_out_text,
  };
}

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const isInt = (n: number, lo: number, hi: number) => Number.isInteger(n) && n >= lo && n <= hi;

export function accountFormErrors(f: AccountForm, { editing = false } = {}): string[] {
  const errors: string[] = [];
  if (!f.name.trim()) errors.push("Укажите название аккаунта.");
  if (!EMAIL.test(f.from_email.trim())) errors.push("Адрес отправителя — корректный email.");
  if (f.reply_to.trim() && !EMAIL.test(f.reply_to.trim())) errors.push("Reply-To — корректный email или пусто.");
  if (!f.smtp_host.trim()) errors.push("Укажите SMTP-сервер.");
  if (!isInt(f.smtp_port, 1, 65535)) errors.push("Порт — от 1 до 65535.");
  if (!isInt(f.daily_limit, 1, MAX_DAILY_LIMIT)) errors.push(`Дневной лимит — от 1 до ${MAX_DAILY_LIMIT}.`);
  if (!isInt(f.cooldown_min_seconds, 0, MAX_COOLDOWN_SECONDS) || !isInt(f.cooldown_max_seconds, 0, MAX_COOLDOWN_SECONDS)) {
    errors.push(`Пауза между письмами — от 0 до ${MAX_COOLDOWN_SECONDS} секунд.`);
  } else if (f.cooldown_max_seconds < f.cooldown_min_seconds) {
    errors.push("Максимальная пауза не может быть меньше минимальной.");
  }
  if (editing && !f.opt_out_text.trim()) errors.push("Текст отказа от рассылки обязателен.");
  if (f.opt_out_text.trim().length > MAX_OPT_OUT) errors.push(`Текст отказа — не длиннее ${MAX_OPT_OUT} символов.`);
  return errors;
}

/** Form → POST body; an empty password / opt-out text is omitted (no password / the backend's default text). */
export function accountCreateRequest(f: AccountForm): EmailAccountIn {
  const body: EmailAccountIn = {
    name: f.name.trim(),
    from_email: f.from_email.trim(),
    from_name: f.from_name.trim(),
    reply_to: f.reply_to.trim() || null,
    smtp_host: f.smtp_host.trim(),
    smtp_port: f.smtp_port,
    smtp_security: f.smtp_security,
    smtp_username: f.smtp_username.trim(),
    daily_limit: f.daily_limit,
    cooldown_min_seconds: f.cooldown_min_seconds,
    cooldown_max_seconds: f.cooldown_max_seconds,
  } as EmailAccountIn;
  if (f.password) body.password = f.password;
  if (f.opt_out_text.trim()) body.opt_out_text = f.opt_out_text.trim();
  return body;
}

/** Only the fields that differ from the saved account; the password only when a new one was typed. */
export function accountPatchRequest(saved: AccountForm, f: AccountForm): EmailAccountPatch {
  const next = accountCreateRequest(f);
  const prev = accountCreateRequest(saved);
  const out: Record<string, unknown> = {};
  for (const k of Object.keys(next) as (keyof EmailAccountIn)[]) {
    if (k === "password") continue;
    if (next[k] !== prev[k]) out[k] = next[k];
  }
  if (f.password) out.password = f.password;
  return out as EmailAccountPatch;
}
