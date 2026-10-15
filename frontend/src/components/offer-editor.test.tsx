import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ContactItem } from "@/lib/api/contacts-hooks";
import type { Offer, OfferVariant } from "@/lib/api/offers-hooks";
import type { EmailAccount, Message } from "@/lib/api/outreach-hooks";

import { OfferEditor } from "./offer-editor";

function wrap(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

const HASH = "a".repeat(64);

const variant = (over: Partial<OfferVariant>): OfferVariant => ({
  id: 11,
  round: 1,
  label: "A",
  style: "professional",
  status: "ready",
  is_selected: true,
  subject: "Идея для превью",
  body: "Посмотрел канал Alpha: мелкий текст на превью.",
  cta: "Посмотрите?",
  structure: null,
  claims: [],
  guard_issues: [],
  personalization_score: "72.00",
  personalization_sources: [
    { fact_id: 1, kind: "channel", field: "channel.title", value: "Alpha", counted: true },
    { fact_id: 3, kind: "video", field: "video.title", value: "Видео 9", counted: false },
  ],
  model_key: "mock-text",
  cost_usd: "0",
  error: null,
  edited: false,
  edited_by: null,
  edited_at: null,
  created_at: "2026-10-15T10:00:00Z",
  ...over,
});

const contact: ContactItem = {
  id: 5,
  type: "email",
  value: "author@alpha.io",
  value_normalized: "author@alpha.io",
  source: "manual",
  source_url: null,
  source_video_id: null,
  seen_in_videos: 0,
  confidence: "1.00",
  status: "verified",
  present: true,
  suppressed: false,
  usable: true,
  is_manual: true,
  note: "",
  discovered_at: "2026-10-15T10:00:00Z",
  last_seen_at: "2026-10-15T10:00:00Z",
  verified_at: null,
};

const offer = (over: Partial<Offer> = {}): Offer => ({
  id: 3,
  lead_id: 9,
  channel_id: 7,
  status: "pending_approval",
  round: 1,
  variant_count: 2,
  language: "ru",
  instructions: "",
  template: null,
  inputs: { channel_title: "Alpha", language: "ru", facts: [], sample: null, template: null, instructions: "" },
  contact,
  channel: "email",
  attachment: null,
  manually_edited: false,
  scheduled_at: null,
  skipped_at: null,
  model_key: "mock-text",
  estimated_cost_usd: "0",
  spent_usd: "0",
  error: null,
  job: null,
  provider_is_mock: true,
  selected_variant_id: 11,
  personalization_score: "72.00",
  content_hash: HASH,
  blockers: [],
  can_approve: true,
  approval: null,
  email_account_id: null,
  message: null,
  variants: [variant({}), variant({ id: 12, label: "B", style: "short", is_selected: false, personalization_score: "40.00" })],
  created_at: "2026-10-15T10:00:00Z",
  updated_at: "2026-10-15T10:00:00Z",
  ...over,
});

const account = (over: Partial<EmailAccount> = {}): EmailAccount => ({
  id: 4,
  name: "Main",
  from_email: "me@example.com",
  from_name: "",
  reply_to: null,
  smtp_host: "smtp.example.com",
  smtp_port: 587,
  smtp_security: "starttls",
  smtp_username: "me@example.com",
  password_configured: true,
  password_hint: "••••••••",
  status: "active",
  paused_reason: null,
  daily_limit: 20,
  sent_today: 2,
  remaining_today: 18,
  cooldown_min_seconds: 60,
  cooldown_max_seconds: 180,
  next_send_at: null,
  opt_out_text: "Ответьте «нет».",
  consecutive_failures: 0,
  bounces: 0,
  replies: 0,
  opt_outs: 0,
  last_sent_at: null,
  last_error_code: null,
  last_error_message: null,
  last_checked_at: null,
  created_at: "2026-10-15T10:00:00Z",
  ...over,
});

const message = (over: Partial<Message> = {}): Message => ({
  id: 21,
  lead_id: 9,
  offer_id: 3,
  channel_id: 7,
  channel_title: "Alpha",
  channel: "email",
  status: "queued",
  email_account_id: 4,
  email_account_name: "Main",
  contact_id: 5,
  contact_type: "email",
  to_address: "author@alpha.io",
  subject: "Идея для превью",
  body: "Посмотрел канал Alpha: мелкий текст на превью.\n\n—\nОтветьте «нет».",
  attachment_url: null,
  content_hash: HASH,
  attempts: 0,
  scheduled_at: "2030-01-02T10:00:00Z",
  next_attempt_at: null,
  sent_at: null,
  provider_message_id: null,
  error_code: null,
  error_message: null,
  events: [],
  created_at: "2026-10-15T10:00:00Z",
  ...over,
});

describe("OfferEditor", () => {
  const fetchMock = vi.fn<typeof fetch>();
  // mutations only: reads (the email accounts) are answered from `accounts`
  const sent: { path: string; method: string; body: unknown }[] = [];
  let accounts: EmailAccount[] = [];

  beforeEach(() => {
    sent.length = 0;
    accounts = [account()];
    fetchMock.mockReset();
    fetchMock.mockImplementation(async (input) => {
      const req = input as Request;
      const path = new URL(req.url).pathname;
      if (req.method === "GET" && path === "/api/email-accounts") return json(200, accounts);
      const text = await req.clone().text();
      sent.push({ path, method: req.method, body: text ? JSON.parse(text) : null });
      return json(200, offer());
    });
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("approves exactly the content on screen", async () => {
    wrap(<OfferEditor offer={offer()} leadId={9} contacts={[contact]} samples={[]} />);
    expect(screen.getByText("ждёт подтверждения")).toBeInTheDocument();
    expect(screen.getByText("mock")).toBeInTheDocument();
    expect(screen.getByLabelText("Текст сообщения")).toHaveValue("Посмотрел канал Alpha: мелкий текст на превью.");
    await userEvent.type(screen.getByLabelText("Комментарий к решению"), "ок");
    await userEvent.click(screen.getByRole("button", { name: "Подтвердить" }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toEqual({
      path: "/api/offers/3/decision",
      method: "POST",
      body: { decision: "approve", comment: "ок", content_hash: HASH },
    });
  });

  it("shows why it cannot be approved and the guard issues", () => {
    const bad = variant({
      guard_issues: [{ code: "unsupported_number", text: "27", message: "Числа «27» нет во входных данных." }],
    });
    wrap(
      <OfferEditor
        offer={offer({ variants: [bad], blockers: ["guard_issues", "contact_required"], can_approve: false, contact: null })}
        leadId={9}
        contacts={[]}
        samples={[]}
      />,
    );
    expect(screen.getByRole("button", { name: "Подтвердить" })).toBeDisabled();
    expect(screen.getByText(/выдуманные цифры/)).toBeInTheDocument();
    expect(screen.getByText(/выберите контакт/)).toBeInTheDocument();
    expect(screen.getByText("число не из данных")).toBeInTheDocument();
    expect(screen.getByText(/Числа «27» нет/)).toBeInTheDocument();
  });

  it("saves only the edited fields and blocks approve while edits are unsaved", async () => {
    wrap(<OfferEditor offer={offer()} leadId={9} contacts={[contact]} samples={[]} />);
    const body = screen.getByLabelText("Текст сообщения");
    await userEvent.clear(body);
    await userEvent.type(body, "Новый текст");
    expect(screen.getByRole("button", { name: "Подтвердить" })).toBeDisabled();
    expect(screen.getByText(/будет помечен «изменён вручную»/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Сохранить текст" }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toEqual({ path: "/api/offers/3", method: "PATCH", body: { body: "Новый текст" } });
  });

  it("selects another draft and changes the channel", async () => {
    wrap(<OfferEditor offer={offer()} leadId={9} contacts={[contact]} samples={[]} />);
    await userEvent.click(screen.getByRole("tab", { name: /B · Максимально короткий/ }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0].body).toEqual({ variant_id: 12 });
    expect(screen.getByRole("option", { name: "Вручную (отправлю сам)" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Telegram" })).not.toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Канал отправки"), "manual");
    await waitFor(() => expect(sent).toHaveLength(2));
    expect(sent[1].body).toEqual({ channel: "manual" });
  });

  it("lists only the data the text really uses", async () => {
    wrap(<OfferEditor offer={offer()} leadId={9} contacts={[contact]} samples={[]} />);
    await userEvent.click(screen.getByText(/Использованные данные канала: 1/));
    expect(screen.getByText("Alpha", { selector: "li" })).toBeInTheDocument();
    expect(screen.getByText(/в тексте их нет: Видео 9/)).toBeInTheDocument();
  });

  it("schedules an approved offer with an aware time", async () => {
    wrap(<OfferEditor offer={offer({ status: "approved" })} leadId={9} contacts={[contact]} samples={[]} />);
    expect(screen.queryByRole("button", { name: "Подтвердить" })).not.toBeInTheDocument();
    const when = screen.getByLabelText("Когда отправить");
    await userEvent.clear(when);
    await userEvent.type(when, "2030-01-02T10:00");
    await userEvent.click(screen.getByRole("button", { name: /Запланировать/ }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0].path).toBe("/api/offers/3/schedule");
    expect(sent[0].body).toEqual({ scheduled_at: new Date(2030, 0, 2, 10, 0).toISOString() });
  });

  it("explains a content change since the offer was opened", async () => {
    fetchMock.mockImplementation(async () =>
      json(409, { error: { code: "content_changed", message: "changed", details: { content_hash: "b".repeat(64) } } }),
    );
    wrap(<OfferEditor offer={offer()} leadId={9} contacts={[contact]} samples={[]} />);
    await userEvent.click(screen.getByRole("button", { name: "Подтвердить" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Оффер изменился, пока вы его смотрели");
  });

  it("sends an approved offer now with the content on screen and the chosen account", async () => {
    accounts = [account(), account({ id: 8, name: "Spare", from_email: "spare@example.com" })];
    wrap(<OfferEditor offer={offer({ status: "approved" })} leadId={9} contacts={[contact]} samples={[]} />);
    await screen.findByRole("option", { name: /Spare · spare@example.com · осталось сегодня 18/ });
    await userEvent.selectOptions(screen.getByLabelText("С какого аккаунта"), "8");
    await userEvent.click(screen.getByRole("button", { name: /Отправить сейчас/ }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toEqual({
      path: "/api/offers/3/send",
      method: "POST",
      body: { content_hash: HASH, email_account_id: 8 },
    });
  });

  it("does not offer to send email without an active account", async () => {
    accounts = [account({ status: "paused" })];
    wrap(<OfferEditor offer={offer({ status: "approved" })} leadId={9} contacts={[contact]} samples={[]} />);
    expect(await screen.findByText(/Нет активного почтового аккаунта/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "«Почтовые аккаунты»" })).toHaveAttribute("href", "/email-accounts");
    expect(screen.getByRole("button", { name: /Отправить сейчас/ })).toBeDisabled();
    expect(screen.getByRole("option", { name: /Main · me@example.com \(не активен\)/ })).toBeDisabled();
  });

  it("queues a manual-channel offer for the operator without an email account", async () => {
    accounts = [];
    wrap(<OfferEditor offer={offer({ status: "approved", channel: "manual" })} leadId={9} contacts={[contact]} samples={[]} />);
    expect(screen.queryByLabelText("С какого аккаунта")).not.toBeInTheDocument();
    expect(screen.getByText(/автоматической отправки нет/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /В рассылку \(отправлю сам\)/ }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0].body).toEqual({ content_hash: HASH, email_account_id: null });
  });

  it("shows the queued message of a scheduled offer; «Отменить план» is the only cancel", () => {
    wrap(
      <OfferEditor
        offer={offer({ status: "scheduled", scheduled_at: "2030-01-02T10:00:00Z", message: message() })}
        leadId={9}
        contacts={[contact]}
        samples={[]}
      />,
    );
    expect(screen.getByText("в очереди")).toBeInTheDocument();
    expect(screen.getByText(/отправка не раньше/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Перенести" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Отменить план" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Отменить отправку/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Отправить сейчас/ })).not.toBeInTheDocument();
  });

  it("a sent offer is final: no editing, sending or regenerating", () => {
    const sentAt = "2026-10-15T12:00:00Z";
    wrap(
      <OfferEditor
        offer={offer({ status: "sent", message: message({ status: "sent", sent_at: sentAt }) })}
        leadId={9}
        contacts={[contact]}
        samples={[]}
      />,
    );
    expect(screen.getByText("отправлен")).toBeInTheDocument();
    expect(screen.getByLabelText("Текст сообщения")).toHaveAttribute("readonly");
    expect(screen.queryByRole("button", { name: /Отправить сейчас/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Перегенерировать/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Записать/ })).toBeInTheDocument();
  });
});
