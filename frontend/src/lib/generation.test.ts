import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api/client";

import {
  DEFAULT_GENERATION_FORM,
  canMakeMoreLike,
  generationErrorMessage,
  generationFormErrors,
  generationRequest,
  originalProblems,
  parseTags,
  referenceFileError,
  sameRequest,
  strategyLabel,
  titleFromFileName,
  variantDecisions,
} from "./generation";

describe("generationRequest", () => {
  it("omits unset options so the backend applies its defaults", () => {
    expect(generationRequest(DEFAULT_GENERATION_FORM)).toEqual({
      variant_count: 4,
      instructions: "",
      reference_ids: [],
      use_source_image: true,
      size: "1536x1024",
    });
  });

  it("sends the chosen source, parent, model and quality; trims instructions; dedupes references", () => {
    const body = generationRequest({
      ...DEFAULT_GENERATION_FORM,
      variant_count: 2,
      instructions: "  без текста  ",
      reference_ids: [3, 1, 3],
      source_video_id: 17,
      parent_variant_id: 9,
      model_key: "gpt-image-1",
      quality: "low",
      use_source_image: false,
    });
    expect(body).toEqual({
      variant_count: 2,
      instructions: "без текста",
      reference_ids: [3, 1],
      use_source_image: false,
      size: "1536x1024",
      source_video_id: 17,
      parent_variant_id: 9,
      model_key: "gpt-image-1",
      quality: "low",
    });
  });
});

describe("sameRequest", () => {
  const base = generationRequest(DEFAULT_GENERATION_FORM);
  it("ignores key and reference order", () => {
    const { size, ...rest } = { ...base, reference_ids: [2, 1] };
    expect(sameRequest({ size, ...rest }, { ...base, reference_ids: [1, 2] })).toBe(true);
  });
  it("detects any changed field", () => {
    expect(sameRequest(base, { ...base, variant_count: 5 })).toBe(false);
    expect(sameRequest(base, { ...base, parent_variant_id: 3 })).toBe(false);
    expect(sameRequest(base, null)).toBe(false);
    expect(sameRequest(null, null)).toBe(true);
  });
});

describe("generationFormErrors", () => {
  it("accepts the defaults and rejects out-of-range values", () => {
    expect(generationFormErrors(DEFAULT_GENERATION_FORM)).toEqual([]);
    expect(generationFormErrors({ ...DEFAULT_GENERATION_FORM, variant_count: 7 })).toHaveLength(1);
    expect(generationFormErrors({ ...DEFAULT_GENERATION_FORM, reference_ids: [1, 2, 3, 4] })).toHaveLength(1);
    expect(generationFormErrors({ ...DEFAULT_GENERATION_FORM, instructions: "x".repeat(2001) })).toHaveLength(1);
  });
});

describe("variant decisions", () => {
  it("offers decisions only for generated variants", () => {
    expect(variantDecisions("ready")).toEqual(["approve", "reject", "final"]);
    expect(variantDecisions("final")).toEqual(["approve", "reset"]);
    expect(variantDecisions("pending")).toEqual([]);
    expect(variantDecisions("failed")).toEqual([]);
    expect(variantDecisions("refused")).toEqual([]);
  });

  it("«more like this» needs an image of a generated variant", () => {
    expect(canMakeMoreLike({ status: "approved", image_url: "/api/images/1" })).toBe(true);
    expect(canMakeMoreLike({ status: "ready", image_url: null })).toBe(false);
    expect(canMakeMoreLike({ status: "failed", image_url: "/api/images/1" })).toBe(false);
  });

  it("labels known strategies and keeps unknown keys", () => {
    expect(strategyLabel("minimal")).toBe("Минимализм");
    expect(strategyLabel("new_one")).toBe("new_one");
  });
});

describe("helpers", () => {
  it("maps known conflict codes to Russian", () => {
    expect(generationErrorMessage(new ApiError(409, "generation_running", "running"))).toMatch(/уже идёт/);
    expect(generationErrorMessage(new ApiError(409, "other", "x"))).toBeNull();
    expect(generationErrorMessage(new Error("x"))).toBeNull();
  });

  it("reads free-form problems of an original", () => {
    expect(originalProblems([{ code: "low_contrast", severity: "high", description: "d" }, { foo: 1 }])).toEqual([
      { code: "low_contrast", severity: "high", description: "d" },
      { code: "other", severity: "", description: "" },
    ]);
  });

  it("validates reference files like the backend", () => {
    expect(referenceFileError({ type: "image/png", size: 1000 })).toBeNull();
    expect(referenceFileError({ type: "image/gif", size: 1000 })).toMatch(/JPEG/);
    expect(referenceFileError({ type: "image/jpeg", size: 10 * 1024 * 1024 + 1 })).toMatch(/10 МБ/);
  });

  it("parses tags and default titles", () => {
    expect(parseTags(" неон, , финансы,неон ")).toEqual(["неон", "финансы"]);
    expect(parseTags(Array.from({ length: 25 }, (_, i) => `t${i}`).join(","))).toHaveLength(20);
    expect(titleFromFileName("cover.final.png")).toBe("cover.final");
    expect(titleFromFileName(".png")).toBe(".png");
  });
});
