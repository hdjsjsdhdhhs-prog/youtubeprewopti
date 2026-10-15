import { describe, expect, it } from "vitest";

import {
  DEFAULT_OFFER_FORM,
  approveBlockers,
  channelsFor,
  defaultScheduleInput,
  formatPersonalization,
  localInputToIso,
  offerFormErrors,
  offerRequest,
  personalizationTone,
  regenerateRequest,
  sameOfferRequest,
  textChanges,
  textDraftErrors,
} from "./offers";

describe("offer form", () => {
  it("omits defaults and maps the attachment choice", () => {
    expect(offerRequest(DEFAULT_OFFER_FORM)).toEqual({ variant_count: 3, instructions: "", attach_sample: true });
    expect(offerRequest({ ...DEFAULT_OFFER_FORM, attachment: "none", instructions: "  на «вы» ", language: "en" })).toEqual({
      variant_count: 3,
      instructions: "на «вы»",
      attach_sample: false,
      language: "en",
    });
    expect(offerRequest({ ...DEFAULT_OFFER_FORM, attachment: 42, template_id: 7 })).toEqual({
      variant_count: 3,
      instructions: "",
      attach_sample: true,
      attachment_variant_id: 42,
      template_id: 7,
    });
  });

  it("validates the form like the backend", () => {
    expect(offerFormErrors(DEFAULT_OFFER_FORM)).toEqual([]);
    expect(offerFormErrors({ ...DEFAULT_OFFER_FORM, variant_count: 4 })).toHaveLength(1);
    expect(offerFormErrors({ ...DEFAULT_OFFER_FORM, language: "Russian" })).toHaveLength(1);
    expect(offerFormErrors({ ...DEFAULT_OFFER_FORM, language: "pt-BR" })).toEqual([]);
    expect(offerFormErrors({ ...DEFAULT_OFFER_FORM, instructions: "x".repeat(2001) })).toHaveLength(1);
  });

  it("compares requests regardless of key order", () => {
    expect(sameOfferRequest({ variant_count: 2, instructions: "", attach_sample: true }, { attach_sample: true, instructions: "", variant_count: 2 })).toBe(true);
    expect(sameOfferRequest({ variant_count: 2, instructions: "", attach_sample: true }, { variant_count: 3, instructions: "", attach_sample: true })).toBe(false);
    expect(sameOfferRequest(null, null)).toBe(true);
  });

  it("regenerates with the settings of the current round", () => {
    const offer = {
      variant_count: 2,
      instructions: "коротко",
      language: "ru",
      template: { id: 5, name: "T", version: 2, body: "b" },
      attachment: null,
    } as unknown as Parameters<typeof regenerateRequest>[0];
    expect(regenerateRequest(offer)).toEqual({ variant_count: 2, instructions: "коротко", language: "ru", attach_sample: false, template_id: 5 });
  });
});

describe("editor helpers", () => {
  it("sends only changed text fields, trimmed", () => {
    const saved = { subject: "Тема", body: "Текст", cta: "Ок?" };
    expect(textChanges(saved, { ...saved })).toEqual({});
    expect(textChanges(saved, { ...saved, body: " Новый текст " })).toEqual({ body: "Новый текст" });
    expect(textDraftErrors({ subject: "", body: "  ", cta: "" })).toHaveLength(1);
    expect(textDraftErrors({ subject: "x".repeat(151), body: "ok", cta: "" })).toHaveLength(1);
  });

  it("offers only channels that fit the contact type", () => {
    expect(channelsFor("email")).toEqual(["email", "manual"]);
    expect(channelsFor("instagram")).toEqual(["manual"]);
    expect(channelsFor(null)).toEqual(["manual"]);
  });

  it("hides the status blocker and formats personalization", () => {
    expect(approveBlockers({ blockers: ["status", "contact_required"] })).toEqual(["contact_required"]);
    expect(formatPersonalization("72.40")).toBe("72/100");
    expect(formatPersonalization(null)).toBe("—");
    expect([personalizationTone("80"), personalizationTone(50), personalizationTone(10), personalizationTone(null)]).toEqual([
      "green",
      "amber",
      "neutral",
      "neutral",
    ]);
  });

  it("converts the schedule input to an aware ISO time", () => {
    const value = defaultScheduleInput(new Date(2026, 9, 15, 18, 30));
    expect(value).toBe("2026-10-16T10:00");
    expect(localInputToIso(value)).toBe(new Date(2026, 9, 16, 10, 0).toISOString());
    expect(localInputToIso("")).toBeNull();
    expect(localInputToIso("garbage")).toBeNull();
  });
});
