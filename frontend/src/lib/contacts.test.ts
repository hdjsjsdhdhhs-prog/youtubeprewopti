import { describe, expect, it } from "vitest";

import {
  CONTACT_TYPES,
  CONTACT_TYPE_LABELS,
  WEIGHT_KEYS,
  contactHref,
  contactWeightsValid,
  contactabilityTone,
  contactsSummary,
  formatContactScore,
  previewContactScore,
} from "./contacts";

describe("contactHref", () => {
  it("builds links for each type", () => {
    expect(contactHref("email", "info@alpha.io", "Info@Alpha.IO")).toBe("mailto:info@alpha.io");
    expect(contactHref("telegram", "@alpha_tg", "@Alpha_TG")).toBe("https://t.me/alpha_tg");
    expect(contactHref("telegram", "t.me/+AbCdEf123", "https://t.me/+AbCdEf123")).toBe("https://t.me/+AbCdEf123");
    expect(contactHref("website", "alpha.io", "https://www.alpha.io/about")).toBe("https://www.alpha.io/about");
    expect(contactHref("website", "alpha.io", "alpha.io")).toBe("https://alpha.io");
    expect(contactHref("vk", "vk.com/alpha_club", "vk.com/alpha_club")).toBe("https://vk.com/alpha_club");
    expect(contactHref("instagram", "@some.user", "@some.user")).toBe("https://instagram.com/some.user");
    expect(contactHref("other", "boosty.to/author", "https://boosty.to/author")).toBe("https://boosty.to/author");
  });

  it("never produces a script or other non-http link", () => {
    expect(contactHref("website", "x", "javascript:alert(1)")).toBeNull();
    expect(contactHref("other", "x", "javascript:alert(1)")).toBeNull();
    expect(contactHref("email", "a b@c.io", "a b@c.io")).toBeNull();
    expect(contactHref("telegram", "someone", "someone")).toBeNull();
    expect(contactHref("vk", "javascript:alert(1)//x.y/", "x")).toBeNull();
  });
});

describe("Contactability helpers", () => {
  it("«no data» is a dash, not zero", () => {
    expect(formatContactScore(null)).toBe("—");
    expect(formatContactScore("0.00")).toBe("0");
    expect(formatContactScore("90.00")).toBe("90");
    expect(contactabilityTone(null)).toBe("neutral");
    expect(contactabilityTone("0.00")).toBe("neutral");
    expect(contactabilityTone("30.00")).toBe("amber");
    expect(contactabilityTone("60.00")).toBe("green");
  });

  it("previews the score like the backend: a type counts once, Instagram is «other», capped at 100", () => {
    const w = { email: 30, telegram: 30, website: 20, vk: 10, other: 10 };
    expect(previewContactScore(["email", "telegram"], w)).toBe(60);
    expect(previewContactScore(["instagram", "other"], w)).toBe(10);
    expect(previewContactScore(["email", "email"], w)).toBe(30);
    expect(previewContactScore(CONTACT_TYPES, w)).toBe(100);
    expect(previewContactScore(["email", "telegram"], { ...w, email: 90, telegram: 90 })).toBe(100);
    expect(previewContactScore([], w)).toBe(0);
  });

  it("validates weights: 0–100 and at least one positive", () => {
    const ok = { email: 30, telegram: 30, website: 20, vk: 10, other: 10 };
    expect(contactWeightsValid(ok)).toBe(true);
    expect(contactWeightsValid({ ...ok, email: 0, telegram: 0, website: 0, vk: 0, other: 0 })).toBe(false);
    expect(contactWeightsValid({ ...ok, email: 101 })).toBe(false);
    expect(contactWeightsValid({ ...ok, vk: -1 })).toBe(false);
    expect(contactWeightsValid({ ...ok, vk: Number.NaN })).toBe(false);
  });

  it("covers every contact type and weight key with a label", () => {
    expect(CONTACT_TYPES.every((t) => CONTACT_TYPE_LABELS[t].length > 0)).toBe(true);
    expect([...WEIGHT_KEYS].sort()).toEqual(["email", "other", "telegram", "vk", "website"]);
  });

  it("summarises the state of a channel", () => {
    expect(contactsSummary({ status: "not_scanned", usable_types: [], contactability_score: null })).toBe(
      "Контакты ещё не собирались",
    );
    expect(contactsSummary({ status: "not_found", usable_types: [], contactability_score: "0.00" })).toBe(
      "Публичных контактов не найдено",
    );
    expect(contactsSummary({ status: "found", usable_types: ["email", "telegram"], contactability_score: "60.00" })).toBe(
      "Email, Telegram · 60/100",
    );
  });
});
