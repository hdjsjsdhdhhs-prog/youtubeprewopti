import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ChannelAggregates, ChannelAnalysis, ChannelVisualReport } from "@/lib/api/hooks";

import { ChannelAuditDetails, ChannelAuditHeadline, ComparedThumbnails } from "./channel-audit";

const analysis: ChannelAnalysis = {
  id: 1,
  channel_id: 7,
  provider: "mock",
  model_key: "mock-vision",
  detail: "low",
  created_at: "2026-10-14T10:00:00Z",
  video_ids: [11, 12, 13],
  visual_consistency: "3.00",
  repeated_template: true,
  outdated_style_score: "8.00",
  has_design_system: false,
  visual_opportunity: "high",
  patterns: [{ pattern: "крупное лицо", evidence: "видео 2: 9 000 просмотров" }],
  recurring_problems: [{ code: "small_text", frequency: "high", description: "текст не читается" }],
  weakest_video_ids: [13],
  redesign_candidates: [{ video_id: 12, why: "сильная идея" }],
  summary: "Единого стиля нет.",
};

const aggregates: ChannelAggregates = {
  thumbnails_analyzed: 3,
  avg_score: "4.50",
  median_score: "4.00",
  pct_weak: "66.7",
  pct_medium: "33.3",
  pct_strong: "0.0",
  best_video_id: 12,
  worst_video_id: 13,
};

const videos: ChannelVisualReport["videos"] = [11, 12, 13].map((id, i) => ({
  video_id: id,
  youtube_video_id: `yt${id}`,
  title: `Видео ${id}`,
  published_at: "2026-10-01T00:00:00Z",
  view_count: 1000 * (i + 1),
  image_asset_id: id === 13 ? null : 100 + id,
  overall_score: ["5.00", "6.00", "2.50"][i],
  improvement_opportunity: "high",
}));

describe("channel audit components", () => {
  it("shows AI judgements and the code-computed aggregates", () => {
    render(<ChannelAuditHeadline analysis={analysis} aggregates={aggregates} />);
    expect(screen.getByText("потенциал: высокий")).toBeInTheDocument();
    expect(screen.getByText("единство стиля 3,0")).toBeInTheDocument();
    expect(screen.getByText("устаревание 8,0")).toBeInTheDocument();
    expect(screen.getByText("один шаблон")).toBeInTheDocument();
    expect(screen.getByText("нет дизайн-системы")).toBeInTheDocument();
    expect(screen.getByText("mock")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Слабые 67 %, средние 33 %, сильные 0 %" })).toBeInTheDocument();
    expect(screen.getByText("4,5")).toBeInTheDocument();
  });

  it("explains that nothing is left to aggregate", () => {
    render(<ChannelAuditHeadline analysis={analysis} aggregates={{ ...aggregates, thumbnails_analyzed: 0 }} />);
    expect(screen.getByText(/больше нет текущих аудитов превью/)).toBeInTheDocument();
  });

  it("names videos by title when the report provides them", () => {
    const { rerender } = render(<ChannelAuditDetails analysis={analysis} videos={videos} />);
    expect(screen.getByText("Видео 12")).toBeInTheDocument(); // redesign candidate
    expect(screen.getByText("Видео 13")).toBeInTheDocument(); // weakest
    expect(screen.getByText(/не причина и не прогноз/)).toBeInTheDocument();
    expect(screen.getByText("у большинства")).toBeInTheDocument();
    rerender(<ChannelAuditDetails analysis={analysis} />);
    expect(screen.getByText("видео #12")).toBeInTheDocument();
  });

  it("numbers the compared thumbnails and marks weakest and candidates", () => {
    render(<ComparedThumbnails videos={videos} analysis={analysis} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
    expect(screen.getByAltText("Превью: Видео 11")).toHaveAttribute("src", "/api/images/111");
    expect(screen.queryByAltText("Превью: Видео 13")).toBeNull(); // no stored image
    expect(screen.getByText("слабое")).toBeInTheDocument();
    expect(screen.getByText("кандидат")).toBeInTheDocument();
    expect(screen.getByText("2,5")).toBeInTheDocument();
  });
});
