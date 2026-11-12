import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Overview } from "@/lib/api/analytics-hooks";

import { DashboardView } from "./dashboard-view";

const replace = vi.fn();
let search = "";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn() }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(search),
}));

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

const OVERVIEW: Overview = {
  period: { date_from: "2026-10-14", date_to: "2026-11-12", project_id: null },
  tiles: {
    total_channels: 120,
    new_channels: 40,
    analyzed: 30,
    selected: 10,
    generated_samples: 4,
    offers_pending_approval: 3,
    sent: 8,
    replies: 2,
    positive_replies: 1,
    reply_rate: "0.2500",
    conversion: "0.1250",
  },
  funnel: [
    { key: "found", label: "Найдено", leads: 40, rate_from_previous: null, rate_from_start: "1.0000" },
    { key: "analyzed", label: "Проанализировано", leads: 30, rate_from_previous: "0.7500", rate_from_start: "0.7500" },
    { key: "sent", label: "Отправлено", leads: 8, rate_from_previous: "0.2667", rate_from_start: "0.2000" },
  ],
  costs: {
    total_usd: "1.200000",
    analysis_usd: "0.800000",
    generation_usd: "0.300000",
    offers_usd: "0.100000",
    other_usd: "0",
    calls: 57,
    unpriced_calls: 0,
    per_lead_usd: "0.030000",
    per_analyzed_lead_usd: "0.040000",
    per_sent_usd: "0.150000",
    per_reply_usd: "0.600000",
    per_positive_usd: "1.200000",
  },
  best_niche: { key: "3", name: "Gaming", sent: 31, reply_rate: "0.1935" },
  best_score_range: null,
  best_campaign: null,
  best_offer_variant: null,
  min_sample: 30,
  notes: ["Лиды — найденные за период и их текущее состояние."],
};

describe("DashboardView", () => {
  const fetchMock = vi.fn<typeof fetch>();
  const requested: string[] = [];

  beforeEach(() => {
    search = "";
    requested.length = 0;
    replace.mockReset();
    fetchMock.mockReset();
    fetchMock.mockImplementation(async (input) => {
      const url = new URL((input as Request).url);
      requested.push(url.pathname + url.search);
      if (url.pathname === "/api/analytics/overview") return json(200, OVERVIEW);
      if (url.pathname === "/api/projects") {
        return json(200, { items: [{ id: 5, name: "Игры" }], total: 1, limit: 50, offset: 0 });
      }
      return json(404, { error: { code: "not_found", message: "nope" } });
    });
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  const view = () =>
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <DashboardView />
      </QueryClientProvider>,
    );

  it("shows the tiles, the funnel, costs and «not enough data» instead of a best guess", async () => {
    view();
    expect(await screen.findByText("Каналов всего")).toBeInTheDocument();
    expect(screen.getByText("120")).toBeInTheDocument();
    expect(screen.getByText("13 %")).toBeInTheDocument(); // conversion 0.125
    const funnel = screen.getByRole("list", { name: "Воронка" });
    expect(within(funnel).getAllByRole("listitem")).toHaveLength(3);
    expect(within(funnel).getByText("75 %")).toBeInTheDocument();
    expect(screen.getByText("$1.20")).toBeInTheDocument();
    expect(screen.getByText("$0.03")).toBeInTheDocument(); // per lead
    expect(screen.getByText("Gaming")).toBeInTheDocument();
    expect(screen.getAllByText("мало данных")).toHaveLength(3);
    // last 30 days by default
    expect(requested.some((r) => r.startsWith("/api/analytics/overview?") && r.includes("date_from="))).toBe(true);
  });

  it("changes the period and the project through the URL", async () => {
    view();
    await screen.findByText("Каналов всего");
    await userEvent.click(screen.getByRole("button", { name: "Всё время" }));
    expect(replace).toHaveBeenLastCalledWith("/?period=all", { scroll: false });
    await screen.findByRole("option", { name: "Игры" });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Проект" }), "5");
    expect(replace).toHaveBeenLastCalledWith("/?project=5", { scroll: false });
  });

  it("asks the API for «all time» without dates", async () => {
    search = "period=all&project=5";
    view();
    await screen.findByText("Каналов всего");
    expect(requested).toContain("/api/analytics/overview?project_id=5");
  });
});
