import { describe, expect, it } from "vitest";

import { ApiError } from "./api/client";
import {
  DEFAULT_CAMPAIGN_FORM,
  campaignCreateRequest,
  campaignErrorMessage,
  campaignFormErrors,
  campaignPatchRequest,
  canArchive,
  canComplete,
  canGenerate,
  canPause,
  canResume,
  canStart,
  formatDays,
  formatRate,
  formatWindow,
  hhmm,
  isCampaignLive,
  toggleIn,
  type CampaignForm,
} from "./campaigns";

const form = (over: Partial<CampaignForm> = {}): CampaignForm => ({ ...DEFAULT_CAMPAIGN_FORM, name: " Ноябрь ", ...over });

describe("campaign formatting", () => {
  it("formats send days as a range, a list or every day", () => {
    expect(formatDays([1, 2, 3, 4, 5])).toBe("Пн–Пт");
    expect(formatDays([5, 1, 3])).toBe("Пн, Ср, Пт");
    expect(formatDays([6, 7])).toBe("Сб, Вс");
    expect(formatDays([1, 2, 3, 4, 5, 6, 7])).toBe("каждый день");
  });

  it("formats the window with hours and the time zone", () => {
    expect(hhmm("10:00:00")).toBe("10:00");
    expect(formatWindow({ send_days: [1, 2, 3, 4, 5], send_from: "10:00:00", send_to: "18:30:00", timezone: "Europe/Moscow" })).toBe(
      "Пн–Пт, 10:00–18:30 (Europe/Moscow)",
    );
  });

  it("formats reply rates and missing values", () => {
    expect(formatRate("0.2500")).toBe("25 %");
    expect(formatRate(null)).toBe("—");
  });
});

describe("what a campaign status allows", () => {
  it("matches the backend state machine", () => {
    expect(canStart("ready")).toBe(true);
    expect(canStart("running")).toBe(false);
    expect(canPause("running")).toBe(true);
    expect(canPause("paused")).toBe(false);
    expect(canResume("paused")).toBe(true);
    expect(canComplete("draft")).toBe(false);
    expect(canComplete("paused")).toBe(true);
    expect(canArchive("running")).toBe(false);
    expect(canArchive("completed")).toBe(true);
    expect(canGenerate("draft")).toBe(false);
    expect(canGenerate("running")).toBe(true);
    expect(isCampaignLive("running")).toBe(true);
    expect(isCampaignLive("completed")).toBe(false);
  });
});

describe("campaign form", () => {
  it("builds the create request: trimmed, sorted days, seconds in times, empty language = null", () => {
    expect(campaignCreateRequest(form({ send_days: [5, 1], language: " ", starts_at: "" }))).toEqual({
      name: "Ноябрь",
      description: "",
      project_id: null,
      template_id: null,
      instructions: "",
      language: null,
      styles: ["professional", "short"],
      channels: ["email"],
      email_account_ids: [],
      send_days: [1, 5],
      send_from: "10:00:00",
      send_to: "18:00:00",
      timezone: "Europe/Moscow",
      starts_at: null,
      daily_limit: 20,
      max_consecutive_failures: 3,
    });
  });

  it("validates the window, limits and required choices", () => {
    expect(campaignFormErrors(form())).toEqual([]);
    const errs = campaignFormErrors(
      form({ name: " ", styles: [], channels: [], send_days: [], send_from: "18:00", send_to: "10:00", daily_limit: 0, language: "русский" }),
    );
    expect(errs).toEqual([
      "Укажите название кампании.",
      "Выберите хотя бы один вариант оффера (A/B).",
      "Выберите хотя бы один канал отправки.",
      "Выберите хотя бы один день отправки.",
      "Окно отправки должно заканчиваться позже, чем начинается.",
      "Язык — код вида ru или en.",
      "Писем в день — от 1 до 500.",
    ]);
    expect(campaignFormErrors(form({ max_consecutive_failures: Number.NaN }))).toEqual(["Автопауза — после 1–50 ошибок подряд."]);
  });

  it("sends only the changed fields on edit", () => {
    const saved = form();
    expect(campaignPatchRequest(saved, saved)).toEqual({});
    expect(campaignPatchRequest(saved, form({ daily_limit: 5, send_to: "17:00", email_account_ids: [3] }))).toEqual({
      daily_limit: 5,
      send_to: "17:00:00",
      email_account_ids: [3],
    });
  });

  it("toggles values keeping the canonical order", () => {
    expect(toggleIn(["short"], "professional", ["professional", "short", "free_sample"])).toEqual(["professional", "short"]);
    expect(toggleIn([1, 3], 3, [1, 2, 3])).toEqual([1]);
  });
});

describe("campaign errors", () => {
  it("lists the issues of campaign_not_ready", () => {
    const err = new ApiError(409, "campaign_not_ready", "not ready", { issues: ["no_leads", "email_account_required"] });
    expect(campaignErrorMessage(err)).toBe(
      "Кампанию пока нельзя запустить: в кампании нет лидов в работе — добавьте их на странице «Лиды»; email разрешён, но нет активного почтового аккаунта для отправки.",
    );
  });

  it("falls back to the offer messages and to null for unknown codes", () => {
    expect(campaignErrorMessage(new ApiError(409, "campaign_status", "x"))).toBe("Действие недоступно в текущем статусе кампании.");
    expect(campaignErrorMessage(new ApiError(409, "not_approved", "x"))).toBe("Сначала подтвердите оффер.");
    expect(campaignErrorMessage(new ApiError(409, "something_else", "x"))).toBeNull();
    expect(campaignErrorMessage(new Error("x"))).toBeNull();
  });
});
