import { describe, expect, it } from "vitest";

import { formatScore, opportunityTone, problemLabel, scoreTone, SUBSCORES } from "./analysis";

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
