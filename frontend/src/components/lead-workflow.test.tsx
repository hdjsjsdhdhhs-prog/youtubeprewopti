import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HumanScore, LeadStatusActions } from "./lead-workflow";

function wrap(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

async function bodyOf(call: unknown[]) {
  const req = call[0] as Request;
  return { path: new URL(req.url).pathname, method: req.method, body: await req.clone().json() };
}

describe("LeadStatusActions", () => {
  const fetchMock = vi.fn<typeof fetch>();
  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
  });
  afterEach(() => vi.unstubAllGlobals());

  it("offers only the allowed manual transitions", () => {
    wrap(<LeadStatusActions lead={{ id: 5, status: "rejected" }} />);
    expect(screen.getByRole("button", { name: "Восстановить" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Выбрать" })).not.toBeInTheDocument();
  });

  it("says when only the system can change the status", () => {
    wrap(<LeadStatusActions lead={{ id: 5, status: "sent" }} />);
    expect(screen.getByText("Статус меняется только системой.")).toBeInTheDocument();
  });

  it("rejects with a reason", async () => {
    fetchMock.mockResolvedValue(json(200, { id: 5, status: "rejected" }));
    wrap(<LeadStatusActions lead={{ id: 5, status: "analyzed" }} />);
    await userEvent.click(screen.getByRole("button", { name: "Отклонить" }));
    await userEvent.type(screen.getByLabelText("Причина отклонения"), "не та ниша");
    const submit = screen.getAllByRole("button", { name: "Отклонить" }).at(-1)!;
    await userEvent.click(submit);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(await bodyOf(fetchMock.mock.calls[0])).toEqual({
      path: "/api/leads/5/status",
      method: "POST",
      body: { status: "rejected", reason: "не та ниша" },
    });
  });

  it("shows the backend error of a forbidden transition", async () => {
    fetchMock.mockResolvedValue(
      json(409, { error: { code: "invalid_transition", message: "lead: transition is not allowed", details: {} } }),
    );
    wrap(<LeadStatusActions lead={{ id: 5, status: "analyzed" }} />);
    await userEvent.click(screen.getByRole("button", { name: "В шорт-лист" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("transition is not allowed");
  });
});

describe("HumanScore (ТЗ §47)", () => {
  const fetchMock = vi.fn<typeof fetch>();
  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
  });
  afterEach(() => vi.unstubAllGlobals());

  const fmt = (v: string | number | null | undefined) => (v === null || v === undefined ? "—" : String(Number(v)));

  it("shows AI and human values side by side", () => {
    wrap(
      <HumanScore
        target="thumbnail_analysis"
        targetId={3}
        field="overall_score"
        aiValue="6.80"
        overrides={[
          {
            id: 9, target_type: "thumbnail_analysis", target_id: 3, field: "overall_score", ai_value: "6.80",
            human_value: "5.50", reason: "", user_id: 1, created_at: "", updated_at: "",
          },
        ]}
        min={1}
        max={10}
        label="Общий балл"
        format={fmt}
      />,
    );
    expect(screen.getByText("AI 6.8")).toBeInTheDocument();
    expect(screen.getByText("я 5.5")).toBeInTheDocument();
  });

  it("validates the range before saving and sends the typed value", async () => {
    fetchMock.mockResolvedValue(json(200, {}));
    wrap(
      <HumanScore
        target="lead"
        targetId={4}
        field="total"
        aiValue="40.00"
        overrides={[]}
        min={0}
        max={100}
        label="Lead Score"
        format={fmt}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Поставить свою оценку: Lead Score" }));
    const input = screen.getByLabelText("Своя оценка: Lead Score (0–100)");
    await userEvent.type(input, "150");
    expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
    await userEvent.clear(input);
    await userEvent.type(input, "72,5");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(await bodyOf(fetchMock.mock.calls[0])).toEqual({
      path: "/api/score-overrides",
      method: "PUT",
      body: { target_type: "lead", target_id: 4, field: "total", human_value: 72.5, reason: "" },
    });
  });
});
