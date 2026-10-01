import { describe, expect, it } from "vitest";

import { formatUsd, formFromPrefilter, parseNumber, prefilterFromForm } from "./prefilter";

describe("prefilter form", () => {
  it("starts from the backend defaults when nothing is saved", () => {
    const f = formFromPrefilter({});
    expect(f.videos_per_channel).toBe("6");
    expect(f.exclude_shorts).toBe(true);
    expect(f.apply_channel_filters).toBe(true);
    expect(f.metrics.luminance_mean).toEqual({ min: "", max: "" });
    expect(prefilterFromForm(f)).toEqual({
      value: {
        videos_per_channel: 6,
        exclude_shorts: true,
        apply_channel_filters: true,
        max_video_age_days: null,
        min_video_views: null,
        metrics: {},
      },
      errors: [],
    });
  });

  it("round-trips saved settings and only sends filled metric ranges", () => {
    const saved = {
      videos_per_channel: 3,
      exclude_shorts: false,
      apply_channel_filters: false,
      min_video_views: 1000,
      metrics: { contrast_rms: { min: null, max: 0.2 }, colorfulness: { min: 10, max: 80 } },
    };
    const { value, errors } = prefilterFromForm(formFromPrefilter(saved));
    expect(errors).toEqual([]);
    expect(value).toEqual({ ...saved, max_video_age_days: null });
  });

  it("accepts decimal commas and reports invalid input like the backend would", () => {
    expect(parseNumber(" 0,35 ")).toBe(0.35);
    expect(parseNumber("")).toBeNull();
    expect(parseNumber("abc")).toBe("invalid");

    const f = formFromPrefilter({});
    f.videos_per_channel = "0";
    f.max_video_age_days = "1,5";
    f.metrics.luminance_mean = { min: "0,8", max: "0,2" };
    f.metrics.edge_density = { min: "x", max: "" };
    const { errors, value } = prefilterFromForm(f);
    expect(errors).toEqual([
      "Видео на канал: целое число от 1 до 50",
      "Не старше (дней): целое число от 1 до 3650",
      "Яркость: минимум больше максимума",
      "Плотность деталей: введите число",
    ]);
    expect(value.metrics).toEqual({});
  });
});

describe("formatUsd", () => {
  it("keeps significant digits for tiny AI costs", () => {
    expect(formatUsd(null)).toBe("—");
    expect(formatUsd("0.000000")).toBe("$0");
    expect(formatUsd("0.004885")).toBe("$0.004885");
    expect(formatUsd("0.1234")).toBe("$0.1234");
    expect(formatUsd("12.5")).toBe("$12.50");
  });
});
