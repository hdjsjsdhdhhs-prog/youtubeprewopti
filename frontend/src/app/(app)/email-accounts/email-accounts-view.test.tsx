import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { EmailAccount } from "@/lib/api/outreach-hooks";

import { EmailAccountsView } from "./email-accounts-view";

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

const ACCOUNT: EmailAccount = {
  id: 4,
  name: "Main",
  from_email: "me@example.com",
  from_name: "Лёша",
  reply_to: null,
  smtp_host: "smtp.example.com",
  smtp_port: 587,
  smtp_security: "starttls",
  smtp_username: "me@example.com",
  password_configured: true,
  password_hint: "••••••••",
  status: "paused",
  paused_reason: "5 ошибок подряд — автоматическая пауза",
  daily_limit: 20,
  sent_today: 3,
  remaining_today: 17,
  cooldown_min_seconds: 60,
  cooldown_max_seconds: 180,
  next_send_at: null,
  opt_out_text: "Ответьте «нет».",
  consecutive_failures: 5,
  bounces: 1,
  replies: 2,
  opt_outs: 0,
  last_sent_at: null,
  last_error_code: "network",
  last_error_message: "Сервер недоступен",
  last_checked_at: null,
  created_at: "2026-10-15T10:00:00Z",
};

describe("EmailAccountsView", () => {
  const fetchMock = vi.fn<typeof fetch>();
  const sent: { path: string; method: string; body: unknown }[] = [];
  let role = "owner";

  beforeEach(() => {
    sent.length = 0;
    role = "owner";
    fetchMock.mockReset();
    fetchMock.mockImplementation(async (input) => {
      const req = input as Request;
      const path = new URL(req.url).pathname;
      if (req.method === "GET" && path === "/api/auth/me") return json(200, { role, user: {}, workspace: {} });
      if (req.method === "GET" && path === "/api/email-accounts") return json(200, [ACCOUNT]);
      const text = await req.clone().text();
      sent.push({ path, method: req.method, body: text ? JSON.parse(text) : null });
      return json(path.endsWith("/verify") ? 200 : 201, path.endsWith("/verify") ? { ok: true, transport: "mock", error_code: null, message: "Подключение и вход успешны." } : ACCOUNT);
    });
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  const view = () =>
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>
        <EmailAccountsView />
      </QueryClientProvider>,
    );

  it("shows health, limits and counters but never a password", async () => {
    view();
    expect(await screen.findByText("Main")).toBeInTheDocument();
    expect(screen.getByText("на паузе")).toBeInTheDocument();
    expect(screen.getByText(/автоматическая пауза/)).toBeInTheDocument();
    expect(screen.getByText(/3 из 20 · осталось 17/)).toBeInTheDocument();
    expect(screen.getByText("1–3 мин")).toBeInTheDocument();
    expect(screen.getByText("2 · 0 · 1")).toBeInTheDocument();
    expect(screen.getByText(/Сервер недоступен/)).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /Возобновить/ })).toBeInTheDocument();
  });

  it("adds an account; the password is sent once and the defaults are left to the backend", async () => {
    view();
    await userEvent.click(await screen.findByRole("button", { name: /Добавить аккаунт/ }));
    await userEvent.type(screen.getByLabelText("Название"), "Second");
    await userEvent.type(screen.getByLabelText("Адрес отправителя"), "two@example.com");
    await userEvent.type(screen.getByLabelText("SMTP-сервер"), "smtp.example.com");
    await userEvent.selectOptions(screen.getByLabelText("Шифрование"), "ssl");
    expect(screen.getByLabelText("Порт")).toHaveValue(465);
    await userEvent.type(screen.getByLabelText("Пароль"), "app-password");
    await userEvent.click(screen.getByRole("button", { name: "Добавить" }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toEqual({
      path: "/api/email-accounts",
      method: "POST",
      body: {
        name: "Second",
        from_email: "two@example.com",
        from_name: "",
        reply_to: null,
        smtp_host: "smtp.example.com",
        smtp_port: 465,
        smtp_security: "ssl",
        smtp_username: "",
        password: "app-password",
        daily_limit: 20,
        cooldown_min_seconds: 60,
        cooldown_max_seconds: 180,
      },
    });
  });

  it("checks the connection and shows the result", async () => {
    view();
    await userEvent.click(await screen.findByRole("button", { name: /Проверить подключение/ }));
    expect(await screen.findByRole("status")).toHaveTextContent("Подключение и вход успешны.");
    expect(sent[0]).toMatchObject({ path: "/api/email-accounts/4/verify", method: "POST" });
  });

  it("is read-only for non-admins", async () => {
    role = "member";
    view();
    expect(await screen.findByText(/может администратор/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Добавить аккаунт/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Проверить подключение/ })).not.toBeInTheDocument();
  });
});
