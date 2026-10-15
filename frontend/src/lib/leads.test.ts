import { describe, expect, it } from "vitest";

import {
  CLEAR_LEAD_FILTERS,
  COMPONENTS,
  criteriaFromForm,
  formFromProfile,
  formatPoints,
  hasLeadFilters,
  leadQueryFromParams,
  leadScoreTone,
  nextLeadSort,
  pointsWidth,
  weightsValid,
} from "./leads";

describe("leadQueryFromParams", () => {
  it("defaults to the highest Lead Score first", () => {
    const q = leadQueryFromParams(new URLSearchParams());
    expect(q).toMatchObject({ sort: "lead_score", order: "desc", offset: 0 });
    expect(hasLeadFilters(q)).toBe(false);
  });

  it("parses every §9 filter and drops invalid values instead of sending a 422", () => {
    const q = leadQueryFromParams(
      new URLSearchParams(
        "max_thumbnail_score=5,5&min_lead_score=70&min_subscribers=5000&max_subscribers=100000&min_avg_views=10000" +
          "&activity=high&max_days_since_last_upload=14&status=analyzed,bogus,analyzed&niche=3,x,3,4" +
          "&visual_opportunity=high&sort=priority&order=asc&offset=50&project_id=2",
      ),
    );
    expect(q).toMatchObject({
      max_thumbnail_score: 5.5,
      min_lead_score: 70,
      min_subscribers: 5000,
      max_subscribers: 100000,
      min_avg_views: 10000,
      activity: "high",
      max_days_since_last_upload: 14,
      status: ["analyzed"],
      niche: [3, 4],
      visual_opportunity: "high",
      sort: "priority",
      order: "asc",
      offset: 50,
      project_id: 2,
    });
    expect(hasLeadFilters(q)).toBe(true);

    const bad = leadQueryFromParams(
      new URLSearchParams("max_thumbnail_score=11&min_lead_score=-1&activity=max&sort=nope&min_subscribers=1.5"),
    );
    expect(bad.max_thumbnail_score).toBeUndefined();
    expect(bad.min_lead_score).toBeUndefined();
    expect(bad.activity).toBeUndefined();
    expect(bad.min_subscribers).toBeUndefined();
    expect(bad.sort).toBe("lead_score");
  });

  it("clears every filter key and the page", () => {
    expect(CLEAR_LEAD_FILTERS).toMatchObject({ status: null, min_thumbnail_score: null, niche: null, offset: null });
  });

  it("parses the contact filters of Phase 6 and drops unknown types / out-of-range scores", () => {
    const q = leadQueryFromParams(new URLSearchParams("contact_type=telegram,fax,email,telegram&min_contactability=60"));
    expect(q).toMatchObject({ contact_type: ["telegram", "email"], min_contactability: 60 });
    expect(hasLeadFilters(q)).toBe(true);
    expect(hasLeadFilters(leadQueryFromParams(new URLSearchParams("min_contactability=0")))).toBe(true);

    const bad = leadQueryFromParams(new URLSearchParams("contact_type=fax&min_contactability=101"));
    expect(bad.contact_type).toBeUndefined();
    expect(bad.min_contactability).toBeUndefined();
    expect(hasLeadFilters(bad)).toBe(false);
    expect(CLEAR_LEAD_FILTERS).toMatchObject({ contact_type: null, min_contactability: null });
  });
});

describe("sorting", () => {
  const q = leadQueryFromParams(new URLSearchParams());
  it("flips the current column and starts thumbnails weakest-first", () => {
    expect(nextLeadSort(q, "lead_score")).toEqual({ sort: "lead_score", order: "asc", offset: null });
    expect(nextLeadSort(q, "thumbnail_score")).toEqual({ sort: "thumbnail_score", order: "asc", offset: null });
    expect(nextLeadSort(q, "subscribers")).toEqual({ sort: "subscribers", order: "desc", offset: null });
  });
});

describe("score helpers", () => {
  it("formats points out of max as in ТЗ §8", () => {
    expect(formatPoints("23.00", "25.00")).toBe("23 / 25");
    expect(formatPoints("8.49", "15.00")).toBe("8,5 / 15");
    expect(pointsWidth("5", "20")).toBe(25);
    expect(pointsWidth("5", "0")).toBe(0);
    expect(leadScoreTone("87.5")).toBe("green");
    expect(leadScoreTone("40")).toBe("amber");
    expect(leadScoreTone(null)).toBe("neutral");
  });

  it("validates weights like the API", () => {
    const zero = Object.fromEntries(COMPONENTS.map((k) => [k, 0]));
    expect(weightsValid(zero)).toBe(false);
    expect(weightsValid({ ...zero, audience: 1 })).toBe(true);
    expect(weightsValid({ ...zero, audience: 101 })).toBe(false);
  });
});

describe("ideal lead profile form", () => {
  it("round-trips criteria; empty fields are unset", () => {
    const form = formFromProfile({
      name: "Business",
      is_default: true,
      criteria: { min_subscribers: 10000, max_thumbnail_score: 6, countries: ["US"], niches: [3], min_activity: "high" },
    });
    expect(form).toMatchObject({ min_subscribers: "10000", max_subscribers: "", countries: "US", min_activity: "high" });
    const out = criteriaFromForm({ ...form, max_subscribers: "100000", countries: "us, ru us", languages: "EN" });
    expect(out).toEqual({
      min_subscribers: 10000,
      max_subscribers: 100000,
      min_avg_views: null,
      min_activity: "high",
      max_days_since_last_upload: null,
      max_thumbnail_score: 6,
      niches: [3],
      countries: ["US", "RU"],
      languages: ["en"],
      contact_types: [],
    });
    expect(formFromProfile(null)).toMatchObject({ name: "", is_default: true, niches: [] });
  });
});
