/** Offers and the Approval Queue (Phase 7): labels, form → request, editor helpers. Records keyed by API enums make
 * `tsc` fail when the backend adds a status / blocker / issue code without a label here. */
import { ApiError, type Schemas } from "@/lib/api/client";

export type OfferStatus = Schemas["OfferStatus"];
export type OfferVariantStatus = Schemas["OfferVariantStatus"];
export type OfferStyle = Schemas["OfferStyle"];
export type OutreachChannel = Schemas["OutreachChannel"];
export type Blocker = Schemas["OfferOut"]["blockers"][number];
export type GuardIssueCode = Schemas["GuardIssue"]["code"];
type OfferIn = Schemas["OfferIn"];
type ContactType = Schemas["ContactType"];

/** Backend `MAX_OFFER_VARIANTS` and field limits (offers/schemas.py). */
export const MAX_OFFER_VARIANTS = 3;
export const MAX_INSTRUCTIONS = 2000;
export const MAX_SUBJECT = 150;
export const MAX_BODY = 4000;
export const MAX_CTA = 300;

export const OFFER_STATUS_LABELS: Record<OfferStatus, string> = {
  draft: "пишется",
  pending_approval: "ждёт подтверждения",
  approved: "подтверждён",
  scheduled: "запланирован",
  sent: "отправлен",
  rejected: "отклонён",
  failed: "не получился",
};

export const OFFER_STATUS_TONES: Record<OfferStatus, "neutral" | "green" | "blue" | "amber" | "red" | "violet"> = {
  draft: "neutral",
  pending_approval: "amber",
  approved: "green",
  scheduled: "violet",
  sent: "blue",
  rejected: "neutral",
  failed: "red",
};

/** Tabs of the Approval Queue, in this order. */
export const QUEUE_TABS: readonly OfferStatus[] = [
  "pending_approval",
  "approved",
  "scheduled",
  "sent",
  "draft",
  "rejected",
  "failed",
];

/** Offers shown on the channel card instead of the «write an offer» form (a sent offer is final). */
export const OPEN_OFFER_STATUSES: ReadonlySet<OfferStatus> = new Set(["draft", "pending_approval", "approved", "scheduled", "sent"]);

export const VARIANT_STATUS_LABELS: Record<OfferVariantStatus, string> = {
  pending: "пишется",
  ready: "готов",
  failed: "ошибка",
  refused: "модель отказалась",
};

export const STYLE_LABELS: Record<OfferStyle, string> = {
  professional: "Профессиональный",
  short: "Максимально короткий",
  free_sample: "Акцент на бесплатном превью",
};

export const CHANNEL_LABELS: Record<OutreachChannel, string> = {
  email: "Email",
  telegram: "Telegram",
  vk: "VK",
  manual: "Вручную (отправлю сам)",
};
export const CHANNELS = Object.keys(CHANNEL_LABELS) as OutreachChannel[];

/** Channels a contact type can be sent through (backend `CHANNEL_FOR_TYPE` + manual for any type). */
export function channelsFor(type: ContactType | null | undefined): OutreachChannel[] {
  const direct: Partial<Record<ContactType, OutreachChannel>> = { email: "email", telegram: "telegram", vk: "vk" };
  const own = type ? direct[type] : undefined;
  return own ? [own, "manual"] : ["manual"];
}

export const BLOCKER_LABELS: Record<Blocker, string> = {
  status: "оффер не ждёт подтверждения",
  no_variant: "нет готового черновика",
  guard_issues: "в тексте есть выдуманные цифры или обещания — исправьте текст",
  contact_required: "выберите контакт",
  contact_unusable: "контакт нельзя использовать (отказ, не работает или в списке «Не писать»)",
  channel_required: "выберите канал отправки",
  channel_mismatch: "канал отправки не подходит к типу контакта",
  lead_closed: "лид отклонён или отказался",
};

export const ISSUE_LABELS: Record<GuardIssueCode, string> = {
  unsupported_number: "число не из данных",
  metric_claim: "метрика без данных",
  guarantee: "обещание результата",
  unsupported_claim: "утверждение без фактов",
  empty_text: "пустой текст",
};

export const FACT_KIND_LABELS: Record<Schemas["OfferFact"]["kind"], string> = {
  channel: "канал",
  niche: "ниша",
  video: "видео",
  thumbnail_problem: "проблема превью",
  recommendation: "рекомендация",
  channel_audit: "аудит канала",
  sample: "бесплатный образец",
};

/** Blockers the UI shows for «Approve» — the status blocker is implied by the buttons themselves. */
export function approveBlockers(offer: { blockers: readonly Blocker[] }): Blocker[] {
  return offer.blockers.filter((b) => b !== "status");
}

export function personalizationTone(score: string | number | null | undefined): "green" | "amber" | "neutral" {
  if (score === null || score === undefined) return "neutral";
  const n = Number(score);
  return n >= 70 ? "green" : n >= 40 ? "amber" : "neutral";
}

export function formatPersonalization(score: string | number | null | undefined): string {
  return score === null || score === undefined ? "—" : `${Math.round(Number(score))}/100`;
}

export function isOfferActive(o: { status: OfferStatus; job: { status: string } | null }): boolean {
  return o.status === "draft" && !!o.job && ["queued", "running", "retrying"].includes(o.job.status);
}

/** Poll the offer while drafts are written or its message is due / being sent (the dispatcher runs every minute);
 * a message queued for later does not keep the page polling. */
export function offerNeedsPolling(
  o: { status: OfferStatus; job: { status: string } | null; message: { status: string; scheduled_at: string; next_attempt_at: string | null } | null },
  now = Date.now(),
): boolean {
  if (isOfferActive(o)) return true;
  const m = o.message;
  if (!m) return false;
  if (m.status === "sending") return true;
  return m.status === "queued" && Date.parse(m.next_attempt_at ?? m.scheduled_at) <= now;
}

export function canEditOffer(status: OfferStatus): boolean {
  return status === "pending_approval" || status === "approved" || status === "scheduled";
}

/** Lead statuses an offer can be written from (backend `offers.service.STARTABLE`). */
export const OFFER_STARTABLE: ReadonlySet<Schemas["LeadStatus"]> = new Set([
  "discovered",
  "analyzed",
  "shortlisted",
  "selected",
  "generated",
  "failed",
  "offer_draft",
  "pending_approval",
  "approved",
  "scheduled",
]);

// --- the generation form ----------------------------------------------------------------------

export type OfferForm = {
  variant_count: number;
  instructions: string;
  template_id: number | null;
  language: string; // "" = the channel's language (else the project's, else Russian)
  attachment: "default" | "none" | number; // default = the lead's final sample
};

export const DEFAULT_OFFER_FORM: OfferForm = {
  variant_count: MAX_OFFER_VARIANTS,
  instructions: "",
  template_id: null,
  language: "",
  attachment: "default",
};

export function offerFormErrors(f: OfferForm): string[] {
  const errors: string[] = [];
  if (!Number.isInteger(f.variant_count) || f.variant_count < 1 || f.variant_count > MAX_OFFER_VARIANTS) {
    errors.push(`Число вариантов — от 1 до ${MAX_OFFER_VARIANTS}.`);
  }
  if (f.instructions.trim().length > MAX_INSTRUCTIONS) errors.push(`Инструкции — не длиннее ${MAX_INSTRUCTIONS} символов.`);
  if (f.language && !/^[a-z]{2}(-[A-Za-z]{2,4})?$/.test(f.language)) errors.push("Язык — код вида ru, en, pt-BR.");
  return errors;
}

/** Form → API body; unset optional fields are omitted so the backend applies its defaults. */
export function offerRequest(f: OfferForm): OfferIn {
  const body: OfferIn = {
    variant_count: f.variant_count,
    instructions: f.instructions.trim(),
    attach_sample: f.attachment !== "none",
  };
  if (f.template_id !== null) body.template_id = f.template_id;
  if (f.language) body.language = f.language;
  if (typeof f.attachment === "number") body.attachment_variant_id = f.attachment;
  return body;
}

/** «Regenerate»: the settings of the offer's current round. */
export function regenerateRequest(o: Schemas["OfferOut"]): OfferIn {
  const body: OfferIn = {
    variant_count: o.variant_count,
    instructions: o.instructions,
    language: o.language,
    attach_sample: o.attachment !== null,
  };
  if (o.template) body.template_id = o.template.id;
  if (o.attachment) body.attachment_variant_id = o.attachment.variant_id;
  return body;
}

export function sameOfferRequest(a: OfferIn | null, b: OfferIn | null): boolean {
  if (a === null || b === null) return a === b;
  const norm = (r: OfferIn) => JSON.stringify(Object.keys(r).sort().map((k) => [k, r[k as keyof OfferIn]]));
  return norm(a) === norm(b);
}

// --- the editor --------------------------------------------------------------------------------

export type TextDraft = { subject: string; body: string; cta: string };

/** Only the fields that differ from the saved variant (PATCH sends nothing else). */
export function textChanges(saved: TextDraft, draft: TextDraft): Partial<TextDraft> {
  const out: Partial<TextDraft> = {};
  for (const k of ["subject", "body", "cta"] as const) {
    if (draft[k].trim() !== saved[k]) out[k] = draft[k].trim();
  }
  return out;
}

export function textDraftErrors(d: TextDraft): string[] {
  const errors: string[] = [];
  if (!d.body.trim()) errors.push("Текст сообщения не может быть пустым.");
  if (d.body.trim().length > MAX_BODY) errors.push(`Текст — не длиннее ${MAX_BODY} символов.`);
  if (d.subject.trim().length > MAX_SUBJECT) errors.push(`Тема — не длиннее ${MAX_SUBJECT} символов.`);
  if (d.cta.trim().length > MAX_CTA) errors.push(`CTA — не длиннее ${MAX_CTA} символов.`);
  return errors;
}

/** `<input type="datetime-local">` value → ISO with the browser's offset (the API wants an aware time). */
export function localInputToIso(value: string): string | null {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

/** A default schedule time: tomorrow 10:00 local, as a `datetime-local` value. */
export function defaultScheduleInput(now = new Date()): string {
  const d = new Date(now);
  d.setDate(d.getDate() + 1);
  d.setHours(10, 0, 0, 0);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** Known conflict codes in Russian; everything else shows the backend message. */
const ERROR_MESSAGES: Record<string, string> = {
  offer_generating: "Черновики этого оффера уже пишутся — дождитесь завершения.",
  generation_running: "Для лида идёт генерация превью — напишите оффер, когда она закончится.",
  lead_status: "В текущем статусе лида оффер недоступен.",
  content_changed: "Оффер изменился, пока вы его смотрели. Проверьте актуальную версию и подтвердите снова.",
  offer_not_approvable: "Оффер пока нельзя подтвердить — см. причины ниже.",
  offer_not_editable: "Оффер в этом статусе нельзя редактировать.",
  offer_status: "Действие недоступно в текущем статусе оффера.",
  not_approved: "Сначала подтвердите оффер.",
  approval_outdated: "Оффер изменён после подтверждения — подтвердите его снова.",
  invalid_schedule: "Время отправки должно быть в будущем.",
  variant_not_ready: "Можно выбрать или править только готовый черновик.",
  ai_not_configured: "Модель для текста не настроена: задайте YTL_VIBECODE_API_KEY (или YTL_AI_PROVIDER=mock для демо).",
  // Phase 8 (outreach)
  offer_not_sendable: "Оффер нельзя отправить: контакт, канал или текст больше не подходят — проверьте причины.",
  message_exists: "Сообщение по этому офферу уже в очереди.",
  already_sent: "Этот текст уже отправлен (или отправляется) этому контакту — повторно не отправляется.",
  message_sending: "Сообщение отправляется прямо сейчас — дождитесь результата.",
  message_status: "Действие недоступно в текущем статусе сообщения.",
  email_account_required: "Нет активного почтового аккаунта: добавьте SMTP-аккаунт в разделе «Почтовые аккаунты».",
  email_account_inactive: "Выбранный почтовый аккаунт на паузе или с ошибкой входа — возобновите его или выберите другой.",
  invalid_cooldown: "Максимальная пауза между письмами не может быть меньше минимальной.",
};

export function offerErrorMessage(error: unknown): string | null {
  return error instanceof ApiError ? (ERROR_MESSAGES[error.code] ?? null) : null;
}
