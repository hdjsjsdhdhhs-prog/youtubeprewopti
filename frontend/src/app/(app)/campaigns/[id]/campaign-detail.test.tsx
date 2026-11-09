import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Campaign, CampaignEstimate, CampaignStats } from "@/lib/api/campaigns-hooks";

import { CampaignDetail } from "./campaign-detail";

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

const CAMPAIGN: Campaign = {
  id: 7,
  name: "Бизнес, ноябрь",
  description: "",
  status: "ready",
  project_id: null,
  project_name: null,
  template_id: null,
  template_name: null,
  instructions: "",
  language: null,
  styles: ["professional", "short"],
  channels: ["email", "manual"],
  email_accounts: [{ id: 4, name: "Main", from_email: "me@example.com", status: "active" }],
  send_days: [1, 2, 3, 4, 5],
  send_from: "10:00:00",
  send_to: "18:00:00",
  timezone: "Europe/Moscow",
  starts_at: null,
  daily_limit: 20,
  sent_today: 0,
  max_consecutive_failures: 3,
  consecutive_failures: 0,
  paused_reason: null,
  in_window_now: false,
  next_window_at: "2026-11-09T07:00:00Z",
  issues: [],
  target_filters: {},
  counts: {
    leads: 3,
    active: 3,
    sent: 0,
    failed: 0,
    rejected: 0,
    skipped: 0,
    removed: 0,
    stages: { no_offer: 2, review: 1 },
    replied: 0,
    opted_out: 0,
  },
  started_at: null,
  completed_at: null,
  archived_at: null,
  created_at: "2026-11-08T10:00:00Z",
  updated_at: "2026-11-08T10:00:00Z",
};

const STATS: CampaignStats = {
  campaign_id: 7,
  min_sample: 30,
  note: "Это счётчики, а не выводы.",
  variants: [
    {
      style: "professional",
      label: "A",
      leads: 2,
      approved: 0,
      sent: 0,
      delivered: 0,
      opened: 0,
      replied: 0,
      positive_reply: 0,
      negative_reply: 0,
      no_reply: 0,
      bounced: 0,
      opt_out: 0,
      reply_rate: null,
      small_sample: true,
    },
  ],
};

const ESTIMATE: CampaignEstimate = {
  campaign_id: 7,
  lead_ids: [11, 12],
  styles: { professional: 1, short: 1 },
  skipped: { no_contact: 1, lead_status: 0 },
  remaining: 0,
  provider: "mock",
  model_key: "text-mock",
  api_model_id: null,
  cost_per_offer_usd: "0.01",
  estimated_cost_usd: "0.02",
  pricing_verified: true,
  budgets: [],
  within_budgets: true,
  confirm_token: "tok-1",
};

describe("CampaignDetail", () => {
  const fetchMock = vi.fn<typeof fetch>();
  const sent: { path: string; method: string; body: unknown }[] = [];
  let campaign: Campaign = CAMPAIGN;

  beforeEach(() => {
    sent.length = 0;
    campaign = CAMPAIGN;
    fetchMock.mockReset();
    fetchMock.mockImplementation(async (input) => {
      const req = input as Request;
      const path = new URL(req.url).pathname;
      if (req.method === "GET") {
        if (path === "/api/campaigns/7") return json(200, campaign);
        if (path === "/api/campaigns/7/stats") return json(200, STATS);
        if (path === "/api/campaigns/7/leads") {
          return json(200, {
            items: [
              {
                lead_id: 11,
                channel_id: 101,
                channel_title: "Канал про бизнес",
                lead_status: "selected",
                style: "short",
                label: "B",
                status: "active",
                stage: "no_offer",
                reason: null,
                offer_id: null,
                offer_status: null,
                message_id: null,
                message_status: null,
                sent_at: null,
                added_at: "2026-11-08T10:00:00Z",
              },
            ],
            total: 1,
            limit: 50,
            offset: 0,
          });
        }
        return json(404, { error: { code: "not_found", message: "nope" } });
      }
      const text = await req.clone().text();
      sent.push({ path, method: req.method, body: text ? JSON.parse(text) : null });
      if (path === "/api/campaigns/7/offers/estimate") return json(200, ESTIMATE);
      if (path === "/api/campaigns/7/offers") return json(202, { campaign, started: 2, job_ids: [1, 2] });
      return json(200, { ...campaign, status: "running" });
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("confirm", vi.fn(() => true));
  });
  afterEach(() => vi.unstubAllGlobals());

  const view = () =>
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>
        <CampaignDetail id={7} />
      </QueryClientProvider>,
    );

  it("shows the settings, the funnel, the A/B counts without a verdict and the leads", async () => {
    view();
    expect(await screen.findByText("Бизнес, ноябрь")).toBeInTheDocument();
    expect(screen.getByText("готова к запуску")).toBeInTheDocument();
    expect(screen.getByText("Пн–Пт, 10:00–18:00 (Europe/Moscow)")).toBeInTheDocument();
    expect(screen.getByText("Main")).toBeInTheDocument();
    expect(screen.getByText("оффер не написан: 2")).toBeInTheDocument();
    expect(screen.getByText("ждёт подтверждения: 1")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /офферы на подтверждении \(1\)/ })).toHaveAttribute("href", "/approvals?campaign_id=7");
    expect(await screen.findByText("мало данных")).toBeInTheDocument();
    expect(screen.getByText("Это счётчики, а не выводы.")).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "Канал про бизнес" })).toHaveAttribute("href", "/channels/101");
  });

  it("starts only after the confirmation", async () => {
    view();
    await userEvent.click(await screen.findByRole("button", { name: /Запустить/ }));
    expect(window.confirm).toHaveBeenCalledOnce();
    await waitFor(() => expect(sent).toEqual([{ path: "/api/campaigns/7/start", method: "POST", body: null }]));
  });

  it("does not start a campaign with open issues and says what is missing", async () => {
    campaign = { ...CAMPAIGN, status: "draft", issues: ["no_leads"] };
    view();
    expect(await screen.findByRole("button", { name: /Запустить/ })).toBeDisabled();
    expect(screen.getByText(/Чтобы запустить: в кампании нет лидов в работе/)).toBeInTheDocument();
  });

  it("estimates the offers and starts exactly the confirmed estimate", async () => {
    view();
    await userEvent.click(await screen.findByRole("button", { name: /Оценить написание офферов/ }));
    const box = (await screen.findByText(/Офферов:/)).closest("div")!;
    expect(box).toHaveTextContent("Офферов: 2");
    expect(within(box).getByText(/нет контакта для каналов кампании — 1/)).toBeInTheDocument();
    await userEvent.click(within(box).getByRole("button", { name: /Подтвердить и написать/ }));
    await waitFor(() => expect(sent).toHaveLength(2));
    expect(sent[1]).toEqual({ path: "/api/campaigns/7/offers", method: "POST", body: { confirm_token: "tok-1" } });
    expect(await screen.findByText(/Поставлено в очередь: 2/)).toBeInTheDocument();
  });
});
