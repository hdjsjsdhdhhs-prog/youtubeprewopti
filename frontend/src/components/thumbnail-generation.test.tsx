import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Schemas } from "@/lib/api/client";

import { LeadGeneration } from "./thumbnail-generation";

function wrap(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

type Call = { path: string; method: string; body: unknown };

const brief: Schemas["GenerationBrief"] = {
  channel_title: "Demo Channel",
  niches: ["Финансы"],
  video_title: "Как накопить",
  base_image: "current_thumbnail",
  fixes: [
    {
      code: "low_contrast",
      severity: "high",
      source: "thumbnail",
      problem: "Текст сливается с фоном",
      directive: "strong tonal contrast between subject, text and background",
    },
  ],
  recommendations: ["Увеличить контраст"],
  channel_summary: null,
  palette: ["#112233"],
  instructions: "",
  references: [],
  history: { previous_requests: 1, approved: ["minimal"], rejected: [] },
};

function estimate(token = "tok-1", over: Partial<Schemas["GenerationEstimate"]> = {}): Schemas["GenerationEstimate"] {
  return {
    lead_id: 5,
    lead_status: "analyzed",
    provider: "mock",
    model_key: "mock-image",
    api_model_id: "mock-image-1",
    models: [{ key: "mock-image", api_model_id: "mock-image-1", price_per_image_usd: "0.019683", pricing_verified: true }],
    variant_count: 4,
    price_per_image_usd: "0.019683",
    estimated_cost_usd: "0.078732",
    pricing_verified: true,
    source: null,
    source_candidates: [
      { video_id: 11, title: "Как накопить", published_at: null, view_count: 100, image_url: null, overall_score: "4.20", problem_codes: [] },
    ],
    brief,
    prompt_preview: "PROMPT A",
    budgets: [],
    within_budgets: true,
    confirm_token: token,
    ...over,
  };
}

function variant(id: number, label: string, status: Schemas["VariantStatus"], image = true): Schemas["VariantOut"] {
  return {
    id, label, strategy: "minimal", status, image_asset_id: image ? id : null, image_url: image ? `/api/images/${id}` : null,
    prompt: `prompt ${label}`, model_key: "mock-image", api_model_id: null, settings: { size: "1536x1024" },
    source_image_ids: [], parent_id: null, cost_usd: "0.019683", error: status === "refused" ? "blocked" : null,
    decided_by: null, decided_at: null, created_at: "2026-10-15T10:00:00Z", updated_at: "2026-10-15T10:00:00Z",
  };
}

function generations(variants: Schemas["VariantOut"][] = []): Schemas["LeadGenerations"] {
  return {
    lead_id: 5,
    lead_status: variants.length ? "generated" : "analyzed",
    final_variant_id: null,
    originals: variants.length
      ? [{ video_id: 11, title: "Как накопить", image_url: "/api/images/100", overall_score: "4.20",
           problems: [{ code: "low_contrast", severity: "high", description: "d" }], recommendations: [], visual_summary: null }]
      : [],
    items: variants.length
      ? [{
          id: 3, lead_id: 5, status: "completed", source_video_id: 11, source_image_url: "/api/images/100",
          parent_variant_id: null, instructions: "", reference_ids: [], variant_count: variants.length,
          model_key: "mock-image", size: "1536x1024", quality: null, brief, estimated_cost_usd: "0.078732",
          spent_usd: "0.039366", error: null, job: null, provider_is_mock: true, variants, created_at: "2026-10-15T10:00:00Z",
        }]
      : [],
  };
}

/** Routes fetch by "METHOD path"; every call is recorded with its JSON body. */
function routeFetch(fetchMock: ReturnType<typeof vi.fn<typeof fetch>>, routes: Record<string, (call: Call) => Response>) {
  const calls: Call[] = [];
  fetchMock.mockImplementation(async (input) => {
    const req = input as Request;
    const path = new URL(req.url).pathname;
    const text = await req.clone().text();
    const call = { path, method: req.method, body: text ? JSON.parse(text) : undefined };
    calls.push(call);
    const route = routes[`${req.method} ${path}`];
    return route ? route(call) : json(404, { error: { code: "not_found", message: `no route ${path}`, details: {} } });
  });
  return calls;
}

describe("LeadGeneration", () => {
  const fetchMock = vi.fn<typeof fetch>();
  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
  });
  afterEach(() => vi.unstubAllGlobals());

  it("estimates, shows the brief and price, then starts exactly the confirmed estimate", async () => {
    const calls = routeFetch(fetchMock, {
      "GET /api/leads/5/generations": () => json(200, generations()),
      "GET /api/references": () => json(200, []),
      "POST /api/leads/5/generation/estimate": () => json(200, estimate()),
      "POST /api/leads/5/generations": () =>
        json(202, { generation: { ...generations([variant(1, "A", "pending", false)]).items[0], variant_count: 4 }, lead_status: "generation_pending" }),
    });
    wrap(<LeadGeneration lead={{ id: 5, status: "analyzed" }} />);
    expect(await screen.findByText(/Генераций ещё не было/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Оценить задание/ }));
    const view = await screen.findByRole("status", { name: "Оценка генерации" });
    expect(within(view).getByText("Текст сливается с фоном")).toBeInTheDocument();
    expect(within(view).getByText(/нравилось — Минимализм/)).toBeInTheDocument();
    expect(within(view).getByText("PROMPT A")).toBeInTheDocument();
    expect(within(view).getByText("mock — демо-картинки")).toBeInTheDocument();

    await userEvent.click(within(view).getByRole("button", { name: /Подтвердить и запустить: 4 вар\. · \$0\.0787/ }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/api/leads/5/generations")).toBe(true));
    const startCall = calls.find((c) => c.method === "POST" && c.path === "/api/leads/5/generations")!;
    expect(startCall.body).toEqual({
      variant_count: 4, instructions: "", reference_ids: [], use_source_image: true, size: "1536x1024", confirm_token: "tok-1",
    });
    expect(await screen.findByText(/Генерация поставлена: 4 вариантов/)).toBeInTheDocument();
  });

  it("blocks the start after the form changes until it is estimated again", async () => {
    routeFetch(fetchMock, {
      "GET /api/leads/5/generations": () => json(200, generations()),
      "GET /api/references": () => json(200, []),
      "POST /api/leads/5/generation/estimate": () => json(200, estimate()),
    });
    wrap(<LeadGeneration lead={{ id: 5, status: "analyzed" }} />);
    await userEvent.click(await screen.findByRole("button", { name: /Оценить задание/ }));
    const start = await screen.findByRole("button", { name: /Подтвердить и запустить/ });
    expect(start).toBeEnabled();
    await userEvent.selectOptions(screen.getByLabelText("Вариантов"), "2");
    expect(start).toBeDisabled();
    expect(screen.getByText(/Форма изменилась после оценки/)).toBeInTheDocument();
    // The source candidates of the estimate are offered.
    expect(screen.getByRole("option", { name: /Как накопить · балл 4,2/ })).toBeInTheDocument();
  });

  it("re-estimates and asks again when the confirmation is outdated", async () => {
    let n = 0;
    const calls = routeFetch(fetchMock, {
      "GET /api/leads/5/generations": () => json(200, generations()),
      "GET /api/references": () => json(200, []),
      "POST /api/leads/5/generation/estimate": () => json(200, estimate(`tok-${++n}`)),
      "POST /api/leads/5/generations": () =>
        json(409, { error: { code: "confirmation_required", message: "changed", details: {} } }),
    });
    wrap(<LeadGeneration lead={{ id: 5, status: "analyzed" }} />);
    await userEvent.click(await screen.findByRole("button", { name: /Оценить задание/ }));
    await userEvent.click(await screen.findByRole("button", { name: /Подтвердить и запустить/ }));
    expect(await screen.findByText(/изменились после оценки/)).toBeInTheDocument();
    await waitFor(() => expect(calls.filter((c) => c.path.endsWith("/generation/estimate"))).toHaveLength(2));
    expect(await screen.findByText("tok-2".slice(0, 8))).toBeInTheDocument();
  });

  it("explains a running generation in Russian", async () => {
    routeFetch(fetchMock, {
      "GET /api/leads/5/generations": () => json(200, generations()),
      "GET /api/references": () => json(200, []),
      "POST /api/leads/5/generation/estimate": () => json(200, estimate()),
      "POST /api/leads/5/generations": () =>
        json(409, { error: { code: "generation_running", message: "already running", details: {} } }),
    });
    wrap(<LeadGeneration lead={{ id: 5, status: "analyzed" }} />);
    await userEvent.click(await screen.findByRole("button", { name: /Оценить задание/ }));
    await userEvent.click(await screen.findByRole("button", { name: /Подтвердить и запустить/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("генерация уже идёт");
  });

  it("does not offer a start for a lead in a non-startable status", async () => {
    routeFetch(fetchMock, {
      "GET /api/leads/5/generations": () => json(200, { ...generations(), lead_status: "sent" }),
      "GET /api/references": () => json(200, []),
      "POST /api/leads/5/generation/estimate": () => json(200, estimate("tok-1", { lead_status: "sent" })),
    });
    wrap(<LeadGeneration lead={{ id: 5, status: "sent" }} />);
    expect(await screen.findByText(/В статусе «Отправлен» генерация недоступна/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Оценить задание/ }));
    expect(await screen.findByRole("button", { name: /Подтвердить и запустить/ })).toBeDisabled();
  });

  it("compares the original with variants, records decisions and starts «more like this»", async () => {
    const calls = routeFetch(fetchMock, {
      "GET /api/leads/5/generations": () =>
        json(200, generations([variant(1, "A", "ready"), variant(2, "B", "refused", false)])),
      "GET /api/references": () => json(200, []),
      "POST /api/generated-thumbnails/1/decision": () =>
        json(200, { ...generations([variant(1, "A", "final"), variant(2, "B", "refused", false)]), final_variant_id: 1 }),
      "POST /api/leads/5/generation/estimate": () => json(200, estimate()),
    });
    wrap(<LeadGeneration lead={{ id: 5, status: "generated" }} />);

    expect(await screen.findByRole("img", { name: "Оригинал: Как накопить" })).toHaveAttribute("src", "/api/images/100");
    expect(screen.getByRole("img", { name: "Вариант A" })).toHaveAttribute("src", "/api/images/1");
    const b = screen.getByRole("listitem", { name: "Вариант B" });
    expect(within(b).getAllByText("отклонено модерацией").length).toBeGreaterThan(0);
    expect(within(b).queryByRole("button", { name: /Одобрить/ })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Финальный образец: вариант A" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/generated-thumbnails/1/decision")).toBe(true));
    expect(calls.find((c) => c.path === "/api/generated-thumbnails/1/decision")!.body).toEqual({ decision: "final", comment: "" });
    const a = screen.getByRole("listitem", { name: "Вариант A" });
    expect(await within(a).findByText("финальный образец")).toBeInTheDocument();

    await userEvent.click(within(a).getByRole("button", { name: "Ещё такие же: вариант A" }));
    expect(screen.getByText(/на основе варианта/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Оценить задание/ }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/generation/estimate"))).toBe(true));
    expect(calls.find((c) => c.path.endsWith("/generation/estimate"))!.body).toMatchObject({ parent_variant_id: 1 });
  });

  it("limits the chosen references to three", async () => {
    const ref = (id: number) => ({
      id, image_asset_id: id, image_url: `/api/images/r${id}`, width: 1280, height: 720, title: `Ref ${id}`,
      description: "", style: "", category: "", tags: [], created_at: "2026-10-15T10:00:00Z",
    });
    const calls = routeFetch(fetchMock, {
      "GET /api/leads/5/generations": () => json(200, generations()),
      "GET /api/references": () => json(200, [ref(1), ref(2), ref(3), ref(4)]),
      "POST /api/leads/5/generation/estimate": () => json(200, estimate()),
    });
    wrap(<LeadGeneration lead={{ id: 5, status: "analyzed" }} />);
    for (const id of [1, 2, 3]) await userEvent.click(await screen.findByLabelText(`Референс «Ref ${id}»`));
    expect(screen.getByLabelText("Референс «Ref 4»")).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: /Оценить задание/ }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/generation/estimate"))).toBe(true));
    expect(calls.find((c) => c.path.endsWith("/generation/estimate"))!.body).toMatchObject({ reference_ids: [1, 2, 3] });
  });
});
