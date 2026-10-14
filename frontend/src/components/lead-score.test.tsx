import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { LeadScore, ScoreComponent } from "@/lib/api/hooks";

import { LeadScoreBadge, LeadScoreBreakdown } from "./lead-score";

const component = (over: Partial<ScoreComponent>): ScoreComponent => ({
  key: "audience",
  label: "Audience",
  points: "0.00",
  max: "0.00",
  fraction: null,
  has_data: false,
  reason: "нет данных",
  inputs: {},
  priority_weight: "0.00",
  ...over,
});

const score: LeadScore = {
  total: "56.31",
  priority_score: "59.35",
  data_coverage: "70.00",
  avg_thumbnail_score: "2.00",
  thumbnail_opportunity: "22.22",
  channel_fit: "0.00",
  audience: "8.49",
  views: "6.34",
  activity: "15.00",
  growth: "4.25",
  contactability: "0.00",
  niche_value: "0.00",
  lead_profile_version: 0,
  priority_profile_version: 0,
  thumbnail_profile_version: 0,
  ideal_lead_profile_id: null,
  created_at: "2026-10-14T10:00:00Z",
  components: [
    component({
      key: "thumbnail_opportunity",
      points: "22.22",
      max: "25.00",
      fraction: 0.8889,
      has_data: true,
      reason: "средний балл превью 2.00/10 по 3 превью",
    }),
    component({ key: "channel_fit", max: "20.00", reason: "нет данных: профиль идеального лида не задан" }),
    component({ key: "contactability", reason: "нет данных: сбор контактов появится в Phase 6" }),
  ],
};

describe("LeadScoreBreakdown", () => {
  it("explains every component as points out of max with the code's reason", () => {
    render(<LeadScoreBreakdown score={score} />);
    expect(screen.getByText("56")).toBeInTheDocument();
    expect(screen.getByText("59")).toBeInTheDocument();
    expect(screen.getByText(/данных: 70 %/)).toBeInTheDocument();
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent("Возможность по превью");
    expect(items[0]).toHaveTextContent("22,2 / 25");
    expect(items[0]).toHaveTextContent("средний балл превью 2.00/10");
    expect(items[1]).toHaveTextContent("0 / 20");
    expect(items[1]).toHaveTextContent("профиль идеального лида не задан");
    expect(items[2]).toHaveTextContent("вес 0");
    expect(items[2]).toHaveTextContent("Phase 6");
  });

  it("shows a dash for a lead without a score", () => {
    render(<LeadScoreBadge score={null} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});
