import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Message } from "@/lib/api/outreach-hooks";

import { MessagePanel } from "./outreach-message";

function wrap(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

const message = (over: Partial<Message> = {}): Message => ({
  id: 21,
  lead_id: 9,
  offer_id: 3,
  channel_id: 7,
  channel_title: "Alpha",
  channel: "telegram",
  status: "manual",
  email_account_id: null,
  email_account_name: null,
  contact_id: 5,
  contact_type: "telegram",
  to_address: "@alpha",
  subject: "",
  body: "Привет! Посмотрел канал Alpha.",
  attachment_url: null,
  content_hash: "a".repeat(64),
  attempts: 0,
  scheduled_at: "2026-10-15T10:00:00Z",
  next_attempt_at: null,
  sent_at: null,
  provider_message_id: null,
  error_code: null,
  error_message: null,
  events: [],
  created_at: "2026-10-15T10:00:00Z",
  ...over,
});

describe("MessagePanel", () => {
  const fetchMock = vi.fn<typeof fetch>();
  const sent: { path: string; body: unknown }[] = [];

  beforeEach(() => {
    sent.length = 0;
    fetchMock.mockReset();
    fetchMock.mockImplementation(async (input) => {
      const req = input as Request;
      const text = await req.clone().text();
      sent.push({ path: new URL(req.url).pathname, body: text ? JSON.parse(text) : null });
      return json(200, message());
    });
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("shows a manual message to copy and marks it sent with a note", async () => {
    wrap(<MessagePanel message={message()} />);
    expect(screen.getByText("ждёт ручной отправки")).toBeInTheDocument();
    expect(screen.getByText("@alpha")).toBeInTheDocument();
    expect(screen.getByText("Привет! Посмотрел канал Alpha.")).toBeVisible();
    await userEvent.type(screen.getByLabelText("Заметка"), "написал в TG");
    await userEvent.click(screen.getByRole("button", { name: "Отправлено" }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toEqual({ path: "/api/messages/21/mark-sent", body: { note: "написал в TG" } });
  });

  it("cancels a queued message unless the parent owns the cancel", async () => {
    const queued = message({ channel: "email", status: "queued", email_account_name: "Main" });
    const { unmount } = wrap(<MessagePanel message={queued} cancellable={false} />);
    expect(screen.queryByRole("button", { name: /Отменить отправку/ })).not.toBeInTheDocument();
    unmount();
    wrap(<MessagePanel message={queued} />);
    expect(screen.getByText(/с аккаунта «Main»/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Отменить отправку/ }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0].path).toBe("/api/messages/21/cancel");
  });

  it("records what happened after the send and warns that opt-out suppresses the address", async () => {
    wrap(
      <MessagePanel
        message={message({
          status: "sent",
          sent_at: "2026-10-15T11:00:00Z",
          events: [{ id: 1, type: "sent", note: "", payload: {}, user_id: 1, occurred_at: "2026-10-15T11:00:00Z" }],
        })}
      />,
    );
    expect(screen.getByRole("list", { name: "История сообщения" })).toHaveTextContent("отправлено");
    expect(screen.queryByRole("button", { name: "Отправлено" })).not.toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Что произошло"), "opt_out");
    expect(screen.getByText(/попадёт в список «Не писать»/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Записать/ }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toEqual({ path: "/api/messages/21/events", body: { type: "opt_out", note: "" } });
  });

  it("shows the send error of a failed message and offers no actions", () => {
    wrap(
      <MessagePanel
        message={message({ channel: "email", status: "failed", error_code: "auth_failed", error_message: "Сервер отклонил вход" })}
      />,
    );
    expect(screen.getByText("не отправлено")).toBeInTheDocument();
    expect(screen.getByText(/Сервер отклонил вход/)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
