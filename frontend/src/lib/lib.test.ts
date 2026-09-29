import { describe, expect, it } from "vitest";

import { filtersFromParams, nextSort } from "@/app/(app)/channels/channel-filters";

import { ApiError, toApiError, unwrap } from "./api/client";
import { formatCount, formatDuration, formatElapsed } from "./format";
import { countQueryLines, parseProjectForm } from "./forms";
import { loginHref, safeNext } from "./navigation";

describe("safeNext (post-login redirect)", () => {
  it.each([
    [null, "/projects"],
    ["", "/projects"],
    ["/channels?q=a", "/channels?q=a"],
    ["https://evil.example", "/projects"],
    ["//evil.example", "/projects"],
    ["/\\evil.example", "/projects"],
    ["/login?next=/x", "/projects"],
  ])("%s -> %s", (input, expected) => {
    expect(safeNext(input)).toBe(expected);
  });

  it("encodes the target into the login href", () => {
    expect(loginHref("/channels?q=a b")).toBe("/login?next=%2Fchannels%3Fq%3Da%20b");
    expect(loginHref("/projects")).toBe("/login");
  });
});

describe("channel filters from URL", () => {
  it("uses defaults and drops invalid values instead of sending them to the API", () => {
    const f = filtersFromParams(
      new URLSearchParams("sort=bogus&order=up&min_subscribers=-5&max_subscribers=1.5&country=RUS&project_id=x"),
    );
    expect(f).toEqual({
      project_id: undefined,
      q: undefined,
      min_subscribers: undefined,
      max_subscribers: undefined,
      country: undefined,
      sort: "subscribers",
      order: "desc",
    });
  });

  it("parses valid values", () => {
    const f = filtersFromParams(
      new URLSearchParams("q=%20cooking%20&country=ru&min_subscribers=1000&project_id=7&sort=title&order=asc"),
    );
    expect(f).toMatchObject({ q: "cooking", country: "RU", min_subscribers: 1000, project_id: 7, sort: "title", order: "asc" });
  });

  it("toggles order on the same column and uses a natural default for a new one", () => {
    const base = filtersFromParams(new URLSearchParams());
    expect(nextSort(base, "subscribers")).toEqual({ sort: "subscribers", order: "asc" });
    expect(nextSort(base, "title")).toEqual({ sort: "title", order: "asc" });
    expect(nextSort(base, "views")).toEqual({ sort: "views", order: "desc" });
  });
});

describe("project form", () => {
  it("normalizes optional fields", () => {
    const r = parseProjectForm({ name: "  Cooking  ", description: "", language: "en-US", region_code: "ru" });
    expect(r).toEqual({ ok: true, data: { name: "Cooking", description: undefined, language: "en-US", region_code: "RU" } });
  });

  it("reports field errors matching backend constraints", () => {
    const r = parseProjectForm({ name: " ", description: null, language: "english", region_code: "RUS" });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(Object.keys(r.errors).sort()).toEqual(["language", "name", "region_code"]);
  });

  it("counts non-blank query lines", () => {
    expect(countQueryLines("a\r\n\n  \nb\n")).toBe(2);
  });
});

describe("API errors", () => {
  it("reads the uniform backend error body", () => {
    const e = toApiError(409, { error: { code: "conflict", message: "Уже существует", details: { a: 1 } } });
    expect(e).toMatchObject({ status: 409, code: "conflict", message: "Уже существует", details: { a: 1 } });
  });

  it("falls back for proxy/HTML errors", () => {
    expect(toApiError(502, "<html>").code).toBe("server_unavailable");
    expect(toApiError(418, null).code).toBe("http_418");
  });

  it("unwrap returns data on 2xx and throws ApiError otherwise", async () => {
    await expect(unwrap(Promise.resolve({ data: 1, response: new Response(null, { status: 200 }) }))).resolves.toBe(1);
    const failed = unwrap(
      Promise.resolve({ error: { error: { code: "unauthenticated", message: "Войдите" } }, response: new Response(null, { status: 401 }) }),
    );
    await expect(failed).rejects.toBeInstanceOf(ApiError);
    await expect(failed).rejects.toMatchObject({ isUnauthorized: true });
    await expect(unwrap(Promise.reject(new TypeError("fetch failed")))).rejects.toMatchObject({ code: "network_error" });
  });
});

describe("format", () => {
  it("formats counts and durations", () => {
    expect(formatCount(null)).toBe("—");
    expect(formatCount(950).replace(/\s/g, " ")).toBe("950");
    expect(formatElapsed("2026-01-01T00:00:00Z", "2026-01-01T00:01:05Z")).toBe("1 мин 5 с");
    expect(formatElapsed(null, null)).toBe("—");
  });

  it("formats video length like YouTube", () => {
    expect(formatDuration(245)).toBe("4:05");
    expect(formatDuration(3723)).toBe("1:02:03");
    expect(formatDuration(59)).toBe("0:59");
    expect(formatDuration(null)).toBe("—");
    expect(formatDuration(-1)).toBe("—");
  });
});
