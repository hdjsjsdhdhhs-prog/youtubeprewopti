import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ContactItem } from "@/lib/api/contacts-hooks";
import type { Offer, OfferVariant } from "@/lib/api/offers-hooks";

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
  variants: [variant({}), variant({ id: 12, label: "B", style: "short", is_selected: false, personalization_score: "40.00" })],
  created_at: "2026-10-15T10:00:00Z",
  updated_at: "2026-10-15T10:00:00Z",
  ...over,
});

describe("OfferEditor", () => {
  const fetchMock = vi.fn<typeof fetch>();
  const sent: { path: string; method: string; body: unknown }[] = [];

  beforeEach(() => {
    sent.length = 0;
    fetchMock.mockReset();
    fetchMock.mockImplementation(async (input) => {
      const req = input as Request;
      const text = await req.clone().text();
      sent.push({ path: new URL(req.url).pathname, method: req.method, body: text ? JSON.parse(text) : null });
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
});
