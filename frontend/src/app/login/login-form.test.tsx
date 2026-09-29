import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LoginForm } from "./login-form";

const replace = vi.fn();
let search = "";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(search),
}));

function renderForm() {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LoginForm />
    </QueryClientProvider>,
  );
}

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

describe("LoginForm", () => {
  const fetchMock = vi.fn<typeof fetch>();
  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    replace.mockReset();
    fetchMock.mockReset();
    search = "";
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("validates input before calling the API", async () => {
    renderForm();
    await userEvent.click(screen.getByRole("button", { name: "Войти" }));
    expect(screen.getByText("Введите корректный email")).toBeInTheDocument();
    expect(screen.getByText("Введите пароль")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows the backend error message on failed login", async () => {
    fetchMock.mockResolvedValue(jsonResponse(401, { error: { code: "invalid_credentials", message: "Неверный email или пароль" } }));
    renderForm();
    await userEvent.type(screen.getByLabelText("Email"), "owner@example.com");
    await userEvent.type(screen.getByLabelText("Пароль"), "wrong-password");
    await userEvent.click(screen.getByRole("button", { name: "Войти" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Неверный email или пароль");
    expect(replace).not.toHaveBeenCalled();
  });

  it("redirects to a safe ?next= target after login", async () => {
    search = "next=%2Fjobs";
    fetchMock.mockResolvedValue(
      jsonResponse(200, {
        user: { id: 1, email: "owner@example.com", display_name: "Owner" },
        workspace: { id: 1, name: "WS", slug: "ws" },
        role: "owner",
      }),
    );
    renderForm();
    await userEvent.type(screen.getByLabelText("Email"), "owner@example.com");
    await userEvent.type(screen.getByLabelText("Пароль"), "correct-password");
    await userEvent.click(screen.getByRole("button", { name: "Войти" }));
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/jobs"));
    const req = fetchMock.mock.calls[0][0] as Request;
    expect(new URL(req.url).pathname).toBe("/api/auth/login");
  });
});
