import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChannelContacts, ContactItem } from "@/lib/api/contacts-hooks";

import { ChannelContactsCard } from "./channel-contacts";

function wrap(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

const item = (over: Partial<ContactItem>): ContactItem => ({
  id: 1,
  type: "email",
  value: "info@alpha.io",
  value_normalized: "info@alpha.io",
  source: "youtube_description",
  source_url: null,
  source_video_id: null,
  seen_in_videos: 0,
  confidence: "0.90",
  status: "public",
  present: true,
  suppressed: false,
  usable: true,
  is_manual: false,
  note: "",
  discovered_at: "2026-10-15T10:00:00Z",
  last_seen_at: "2026-10-15T10:00:00Z",
  verified_at: null,
  ...over,
});

const contacts = (over: Partial<ChannelContacts> = {}): ChannelContacts => ({
  channel_id: 7,
  status: "found",
  scan: { extractor_version: 1, texts_scanned: 2, contacts_found: 2, scanned_at: "2026-10-15T10:00:00Z" },
  contactability_score: "60.00",
  usable_types: ["email", "telegram"],
  items: [
    item({ id: 1 }),
    item({ id: 2, type: "telegram", value: "@alpha_tg", value_normalized: "@alpha_tg", seen_in_videos: 3 }),
    item({ id: 3, type: "vk", value: "vk.com/old", value_normalized: "vk.com/old", present: false, usable: false }),
  ],
  ...over,
});

const NO_RESCORE = { leads_created: 0, leads_analyzed: 0, scores_changed: 1, scores_unchanged: 0, system_tags_changed: 0 };

describe("ChannelContactsCard", () => {
  const fetchMock = vi.fn<typeof fetch>();
  let current: ChannelContacts;
  const sent: { path: string; method: string; body: unknown }[] = [];

  beforeEach(() => {
    current = contacts();
    sent.length = 0;
    fetchMock.mockReset();
    fetchMock.mockImplementation(async (input) => {
      const req = input as Request;
      const path = new URL(req.url).pathname;
      if (req.method === "GET") return json(200, current);
      const text = await req.clone().text();
      sent.push({ path, method: req.method, body: text ? JSON.parse(text) : null });
      return json(200, { contacts: current, rescored: NO_RESCORE });
    });
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("shows the state, the score and every contact with safe links", async () => {
    wrap(<ChannelContactsCard channelId={7} />);
    expect(await screen.findByText("Контактность: 60")).toBeInTheDocument();
    expect(screen.getByText("найдены")).toBeInTheDocument();
    expect(screen.getByText("Email, Telegram · 60/100")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "info@alpha.io" })).toHaveAttribute("href", "mailto:info@alpha.io");
    const tg = screen.getByRole("link", { name: "@alpha_tg" });
    expect(tg).toHaveAttribute("href", "https://t.me/alpha_tg");
    expect(tg).toHaveAttribute("rel", expect.stringContaining("noopener"));
    expect(screen.getByText("пропал из текстов")).toBeInTheDocument();
    expect(screen.getByText(/в 3 видео/)).toBeInTheDocument();
  });

  it("says «not scanned» and offers to collect", async () => {
    current = contacts({ status: "not_scanned", scan: null, contactability_score: null, usable_types: [], items: [] });
    wrap(<ChannelContactsCard channelId={7} />);
    expect(await screen.findByText("Контакты ещё не собирались")).toBeInTheDocument();
    expect(screen.getByText("Контактность: —")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Собрать контакты" }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({ path: "/api/channels/7/contacts/scan", method: "POST" });
  });

  it("explains «not found» instead of showing an empty list", async () => {
    current = contacts({ status: "not_found", contactability_score: "0.00", usable_types: [], items: [] });
    wrap(<ChannelContactsCard channelId={7} />);
    expect(await screen.findByText(/нет публичных контактов/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Собрать заново" })).toBeInTheDocument();
  });

  it("marks a contact as not working", async () => {
    wrap(<ChannelContactsCard channelId={7} />);
    const row = (await screen.findByRole("link", { name: "info@alpha.io" })).closest("li")!;
    await userEvent.click(within(row).getByRole("button", { name: "Не работает" }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toEqual({ path: "/api/contacts/1", method: "PATCH", body: { status: "invalid", note: "" } });
  });

  it("opt-out is its own action and a suppressed contact has no actions left", async () => {
    wrap(<ChannelContactsCard channelId={7} />);
    const row = (await screen.findByRole("link", { name: "@alpha_tg" })).closest("li")!;
    await userEvent.click(within(row).getByRole("button", { name: /Отказался/ }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0].body).toEqual({ status: "opted_out", note: "" });
  });

  it("a suppressed contact only points to the admin-owned list", async () => {
    current = contacts({
      items: [item({ id: 5, status: "opted_out", suppressed: true, usable: false })],
      usable_types: [],
    });
    wrap(<ChannelContactsCard channelId={7} />);
    expect(await screen.findByText("в списке подавления")).toBeInTheDocument();
    expect(screen.getByText(/снять отметку может администратор/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Отказался/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Подтвердить" })).not.toBeInTheDocument();
  });

  it("adds a contact by hand and shows the validation error of the backend", async () => {
    wrap(<ChannelContactsCard channelId={7} />);
    await screen.findByText("Контактность: 60");
    await userEvent.selectOptions(screen.getByLabelText("Тип контакта"), "email");
    await userEvent.type(screen.getByLabelText("Значение контакта"), "not-an-email");
    fetchMock.mockImplementation(async (input) => {
      const req = input as Request;
      if (req.method === "GET") return json(200, current);
      return json(422, { error: { code: "invalid_contact", message: "'not-an-email' is not a valid email contact", details: {} } });
    });
    await userEvent.click(screen.getByRole("button", { name: "Добавить контакт" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("is not a valid email contact");
  });

  it("deletes only manual contacts", async () => {
    current = contacts({
      items: [item({ id: 1 }), item({ id: 9, type: "telegram", value: "@mine", value_normalized: "@mine", source: "manual", is_manual: true })],
    });
    wrap(<ChannelContactsCard channelId={7} />);
    await screen.findByRole("link", { name: "@mine" });
    expect(screen.getAllByRole("button", { name: "Удалить контакт" })).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: "Удалить контакт" }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({ path: "/api/contacts/9", method: "DELETE" });
  });
});
