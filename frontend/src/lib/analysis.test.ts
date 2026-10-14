import { describe, expect, it } from "vitest";

import {
  bandWidths,
  formatPercent,
  formatScore,
  inverseScoreTone,
  opportunityTone,
  problemLabel,
  scoreTone,
  SUBSCORES,
} from "./analysis";

describe("channel audit helpers", () => {
  it("colours 'higher = worse' scores inversely", () => {
    expect([inverseScoreTone("8.00"), inverseScoreTone(5), inverseScoreTone(4.99)]).toEqual(["red", "amber", "green"]);
  });

  it("keeps band widths within 100 %", () => {
    expect(bandWidths({ pct_weak: "50.0", pct_medium: "25.0", pct_strong: "25.0" })).toEqual([50, 25, 25]);
    expect(bandWidths({ pct_weak: "33.3", pct_medium: "33.3", pct_strong: "33.4" })).toEqual([33.3, 33.3, 33.4]);
    const [w, m, s] = bandWidths({ pct_weak: "66.7", pct_medium: "33.4", pct_strong: "0.0" });
    expect(w + m + s).toBeLessThanOrEqual(100);
    expect(bandWidths({ pct_weak: "x", pct_medium: "-5", pct_strong: "120" })).toEqual([0, 0, 100]);
  });

  it("formats percentages", () => {
    expect(formatPercent("33.3")).toBe("33 %");
    expect(formatPercent(null)).toBe("—");
  });
});

describe("thumbnail audit helpers", () => {
  it("keeps the backend subscore order", () => {
    expect(SUBSCORES[0]).toBe("composition_score");
    expect(SUBSCORES.at(-1)).toBe("redesign_potential");
    expect(SUBSCORES).toHaveLength(11);
  });

  it("labels known problem codes and passes unknown ones through", () => {
    expect(problemLabel("small_text")).toBe("Мелкий текст");
    expect(problemLabel("future_code")).toBe("future_code");
  });

  it("colours scores and opportunity", () => {
    expect([scoreTone(4.99), scoreTone("5.00"), scoreTone(6.99), scoreTone("7")]).toEqual([
      "red",
      "amber",
      "amber",
      "green",
    ]);
    expect([opportunityTone("high"), opportunityTone("medium"), opportunityTone("low")]).toEqual([
      "green",
      "amber",
      "neutral",
    ]);
  });

  it("formats decimal strings from the API", () => {
    expect(formatScore("5.40")).toBe("5,4");
    expect(formatScore(10)).toBe("10,0");
    expect(formatScore(null)).toBe("—");
    expect(formatScore("x")).toBe("—");
  });
});
