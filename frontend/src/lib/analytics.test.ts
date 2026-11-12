import { describe, expect, it } from "vitest";

import { analyticsQueryFromParams, formatUnitCost, funnelWidth, periodRange, verdictText } from "./analytics";

const TODAY = new Date("2026-11-12T22:30:00Z");

describe("periodRange", () => {
  it("counts the last N days including today (UTC)", () => {
    expect(periodRange("7d", TODAY)).toEqual({ date_from: "2026-11-06", date_to: "2026-11-12" });
    expect(periodRange("30d", TODAY)).toEqual({ date_from: "2026-10-14", date_to: "2026-11-12" });
    expect(periodRange("all", TODAY)).toEqual({});
  });
});

describe("analyticsQueryFromParams", () => {
  it("defaults to 30 days and all projects, drops invalid values", () => {
    expect(analyticsQueryFromParams(new URLSearchParams(""), TODAY)).toEqual({
      period: "30d",
      query: { date_from: "2026-10-14", date_to: "2026-11-12" },
    });
    expect(analyticsQueryFromParams(new URLSearchParams("period=year&project=abc"), TODAY).query).toEqual({
      date_from: "2026-10-14",
      date_to: "2026-11-12",
    });
  });

  it("passes the project and «all time»", () => {
    expect(analyticsQueryFromParams(new URLSearchParams("period=all&project=5"), TODAY)).toEqual({
      period: "all",
      query: { project_id: 5 },
    });
  });
});

describe("funnelWidth", () => {
  it("is relative to the first step and stays visible when not zero", () => {
    expect(funnelWidth(50, 100)).toBe(50);
    expect(funnelWidth(1, 1000)).toBe(2);
    expect(funnelWidth(0, 100)).toBe(0);
    expect(funnelWidth(5, 0)).toBe(0);
  });
});

describe("verdictText", () => {
  it("never claims a winner without enough data", () => {
    expect(verdictText({ verdict: "insufficient_data", min_sample: 30, p_value: null })).toContain("не меньше 30");
    expect(verdictText({ verdict: "no_difference", min_sample: 30, p_value: "0.42" }, "B")).toContain("может быть случайной");
    const leader = verdictText({ verdict: "leader", min_sample: 30, p_value: "0.01" }, "B");
    expect(leader).toContain("«B»");
    expect(leader).toContain("не доказанная причина");
  });
});

describe("formatUnitCost", () => {
  it("keeps small amounts readable", () => {
    expect(formatUnitCost(null)).toBe("—");
    expect(formatUnitCost("0.075000")).toBe("$0.075");
    expect(formatUnitCost("0.000123")).toBe("$0.0001");
    expect(formatUnitCost("12.5")).toBe("$12.50");
  });
});
