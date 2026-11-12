"use client";

/** Hooks of the analytics (Phase 9): dashboard overview, costs, niches, thumbnail score ranges, campaigns, A/B and
 * AI vs human. Read-only; every report takes the same period / project query. */
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import type { ABDimension, AnalyticsQuery } from "@/lib/analytics";

import { api, unwrap, type Schemas } from "./client";

export type Overview = Schemas["Overview"];
export type Outcomes = Schemas["Outcomes"];
export type Best = Schemas["Best"];
export type CostReport = Schemas["CostReport"];
export type NicheReport = Schemas["NicheReport"];
export type ScoreRangeReport = Schemas["ScoreRangeReport"];
export type CampaignReport = Schemas["CampaignReport"];
export type ABReport = Schemas["ABReport"];
export type AIFeedbackReport = Schemas["AIFeedbackReport"];

const STALE_MS = 30_000;

export const analyticsKeys = {
  all: ["analytics"] as const,
  report: (name: string, q: AnalyticsQuery, extra?: Record<string, unknown>) => ["analytics", name, q, extra ?? {}] as const,
};

const options = { staleTime: STALE_MS, placeholderData: keepPreviousData };

export function useOverview(q: AnalyticsQuery) {
  return useQuery({
    queryKey: analyticsKeys.report("overview", q),
    queryFn: () => unwrap(api.GET("/api/analytics/overview", { params: { query: q } })),
    ...options,
  });
}

export function useCostReport(q: AnalyticsQuery, enabled = true) {
  return useQuery({
    queryKey: analyticsKeys.report("costs", q),
    queryFn: () => unwrap(api.GET("/api/analytics/costs", { params: { query: q } })),
    enabled,
    ...options,
  });
}

export function useNicheReport(q: AnalyticsQuery, enabled = true) {
  return useQuery({
    queryKey: analyticsKeys.report("niches", q),
    queryFn: () => unwrap(api.GET("/api/analytics/niches", { params: { query: q } })),
    enabled,
    ...options,
  });
}

export function useScoreRangeReport(q: AnalyticsQuery, enabled = true) {
  return useQuery({
    queryKey: analyticsKeys.report("score-ranges", q),
    queryFn: () => unwrap(api.GET("/api/analytics/score-ranges", { params: { query: q } })),
    enabled,
    ...options,
  });
}

export function useCampaignReport(q: AnalyticsQuery, enabled = true) {
  return useQuery({
    queryKey: analyticsKeys.report("campaigns", q),
    queryFn: () => unwrap(api.GET("/api/analytics/campaigns", { params: { query: q } })),
    enabled,
    ...options,
  });
}

export function useABReport(q: AnalyticsQuery, dimension: ABDimension, campaignId?: number, enabled = true) {
  return useQuery({
    queryKey: analyticsKeys.report("ab", q, { dimension, campaignId }),
    queryFn: () =>
      unwrap(
        api.GET("/api/analytics/ab", {
          params: { query: { ...q, dimension, ...(campaignId ? { campaign_id: campaignId } : {}) } },
        }),
      ),
    enabled,
    ...options,
  });
}

export function useAIFeedbackReport(q: AnalyticsQuery, enabled = true) {
  return useQuery({
    queryKey: analyticsKeys.report("ai-feedback", q),
    queryFn: () => unwrap(api.GET("/api/analytics/ai-feedback", { params: { query: q } })),
    enabled,
    ...options,
  });
}
