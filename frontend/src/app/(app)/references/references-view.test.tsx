import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ReferencesView } from "./references-view";

function wrap(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function json(status: number, body: unknown) {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const ref = {
  id: 7, image_asset_id: 70, image_url: "/api/images/70", width: 1280, height: 720, title: "Неон",
  description: "яркий неон", style: "neon", category: "tech", tags: ["dark"], created_at: "2026-10-15T10:00:00Z",
};

describe("ReferencesView", () => {
  const fetchMock = vi.fn<typeof fetch>();
  const calls: { path: string; method: string; body: unknown }[] = [];
  beforeEach(() => {
    calls.length = 0;
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
    fetchMock.mockImplementation(async (input) => {
      const req = input as Request;
      const path = new URL(req.url).pathname;
      const text = await req.clone().text();
      calls.push({ path, method: req.method, body: text ? JSON.parse(text) : undefined });
      if (req.method === "GET") return json(200, [ref]);
      if (req.method === "POST") return json(201, { ...ref, id: 8 });
      if (req.method === "PATCH") return json(200, { ...ref, title: "Новое" });
      return json(204, null);
    });
  });
  afterEach(() => vi.unstubAllGlobals());

  it("lists references with their metadata", async () => {
    wrap(<ReferencesView />);
    expect(await screen.findByRole("img", { name: "Референс: Неон" })).toHaveAttribute("src", "/api/images/70");
    expect(screen.getByText("neon")).toBeInTheDocument();
    expect(screen.getByText("dark")).toBeInTheDocument();
  });

  it("uploads the file as a data URL with the title from the file name and parsed tags", async () => {
    wrap(<ReferencesView />);
    await screen.findByText("Неон");
    const file = new File([new Uint8Array([1, 2, 3])], "cover.png", { type: "image/png" });
    await userEvent.upload(screen.getByLabelText(/Изображение/), file);
    expect(screen.getByLabelText("Название", { selector: "#ref-new-title" })).toHaveValue("cover");
    await userEvent.type(screen.getByLabelText("Теги через запятую", { selector: "#ref-new-tags" }), "неон, , финансы");
    await userEvent.click(screen.getByRole("button", { name: /Загрузить референс/ }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")).toEqual({
      path: "/api/references",
      method: "POST",
      body: {
        image_base64: "data:image/png;base64,AQID",
        title: "cover",
        description: "",
        style: "",
        category: "",
        tags: ["неон", "финансы"],
      },
    });
  });

  it("rejects unsupported files before uploading", async () => {
    wrap(<ReferencesView />);
    await screen.findByText("Неон");
    const file = new File(["GIF89a"], "anim.gif", { type: "image/gif" });
    await userEvent.upload(screen.getByLabelText(/Изображение/), file, { applyAccept: false });
    expect(screen.getByRole("alert")).toHaveTextContent("JPEG, PNG и WebP");
    expect(screen.getByRole("button", { name: /Загрузить референс/ })).toBeDisabled();
  });

  it("edits and deletes a reference", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    wrap(<ReferencesView />);
    await userEvent.click(await screen.findByRole("button", { name: "Изменить" }));
    const title = screen.getByLabelText("Название", { selector: "#ref-7-title" });
    await userEvent.clear(title);
    await userEvent.type(title, "Новое");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")).toEqual({
      path: "/api/references/7",
      method: "PATCH",
      body: { title: "Новое", description: "яркий неон", style: "neon", category: "tech", tags: ["dark"] },
    });

    await userEvent.click(await screen.findByRole("button", { name: "Удалить референс «Неон»" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
    expect(calls.find((c) => c.method === "DELETE")!.path).toBe("/api/references/7");
  });
});
