"use client";

/** Hooks of campaigns (Phase 8.2): list / card, settings, leads («Add to campaign»), offer generation (estimate →
 * confirmed start), launch / pause / resume / complete / archive and the A/B counts. Every guardrail (one approval
 * per offer, window, limits, suppression) is enforced by the backend. */
import { keepPreviousData, useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";

import { isCampaignLive, type CampaignLeadStatus } from "@/lib/campaigns";

import { api, unwrap, type Schemas } from "./client";
import { qk } from "./hooks";
import { invalidateOffers } from "./offers-hooks";

export type Campaign = Schemas["CampaignOut"];
export type CampaignIn = Schemas["CampaignIn"];
export type CampaignPatch = Schemas["CampaignPatch"];
export type CampaignList = Schemas["CampaignList"];
export type CampaignLead = Schemas["CampaignLeadOut"];
export type CampaignLeadsResult = Schemas["CampaignLeadsResult"];
export type CampaignEstimate = Schemas["CampaignGenerationEstimate"];
export type CampaignStats = Schemas["CampaignStats"];
export type VariantStats = Schemas["VariantStats"];
export type LeadSelectionBody = Schemas["LeadSelection"];
export type CampaignAction = "start" | "pause" | "resume" | "complete" | "archive";

export const CAMPAIGN_LEADS_PAGE = 50;
const LIVE_POLL_MS = 15_000;

export const campaignKeys = {
  all: ["campaigns"] as const,
  list: (includeArchived: boolean) => ["campaigns", "list", includeArchived] as const,
  detail: (id: number) => ["campaigns", "detail", id] as const,
  leads: (id: number, p: { status?: CampaignLeadStatus; offset: number }) => ["campaigns", "leads", id, p] as const,
  stats: (id: number) => ["campaigns", "stats", id] as const,
};

/** A campaign change moves offers, messages and leads too. */
export function invalidateCampaigns(qc: QueryClient) {
  return Promise.all([
    qc.invalidateQueries({ queryKey: campaignKeys.all }),
    invalidateOffers(qc),
    qc.invalidateQueries({ queryKey: ["messages"] }),
  ]);
}

export function useCampaigns(includeArchived = false, enabled = true) {
  return useQuery({
    queryKey: campaignKeys.list(includeArchived),
    queryFn: () => unwrap(api.GET("/api/campaigns", { params: { query: { include_archived: includeArchived } } })),
    staleTime: 10_000,
    enabled,
  });
}

/** Polls while the campaign runs by itself (tick, dispatcher, generation jobs). */
export function useCampaign(id: number) {
  return useQuery({
    queryKey: campaignKeys.detail(id),
    queryFn: () => unwrap(api.GET("/api/campaigns/{campaign_id}", { params: { path: { campaign_id: id } } })),
    refetchInterval: (query) => (query.state.data && isCampaignLive(query.state.data.status) ? LIVE_POLL_MS : false),
  });
}

export function useCampaignLeads(id: number, p: { status?: CampaignLeadStatus; offset: number }, live = false) {
  return useQuery({
    queryKey: campaignKeys.leads(id, p),
    queryFn: () =>
      unwrap(
        api.GET("/api/campaigns/{campaign_id}/leads", {
          params: { path: { campaign_id: id }, query: { ...p, limit: CAMPAIGN_LEADS_PAGE } },
        }),
      ),
    placeholderData: keepPreviousData,
    refetchInterval: live ? LIVE_POLL_MS : false,
  });
}

export function useCampaignStats(id: number, live = false) {
  return useQuery({
    queryKey: campaignKeys.stats(id),
    queryFn: () => unwrap(api.GET("/api/campaigns/{campaign_id}/stats", { params: { path: { campaign_id: id } } })),
    refetchInterval: live ? LIVE_POLL_MS : false,
  });
}

export function useSaveCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (p: { id: number; body: CampaignPatch } | { id?: undefined; body: CampaignIn }) =>
      unwrap(
        p.id === undefined
          ? api.POST("/api/campaigns", { body: p.body })
          : api.PATCH("/api/campaigns/{campaign_id}", { params: { path: { campaign_id: p.id } }, body: p.body }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: campaignKeys.all }),
  });
}

/** «В кампанию»: chosen leads or all filtered (the same selection as the leads bulk actions). */
export function useAddCampaignLeads() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, selection }: { id: number; selection: LeadSelectionBody }) =>
      unwrap(api.POST("/api/campaigns/{campaign_id}/leads", { params: { path: { campaign_id: id } }, body: { selection } })),
    onSuccess: () => Promise.all([qc.invalidateQueries({ queryKey: campaignKeys.all }), qc.invalidateQueries({ queryKey: qk.leads })]),
  });
}

export function useRemoveCampaignLead(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (leadId: number) =>
      unwrap(
        api.DELETE("/api/campaigns/{campaign_id}/leads/{lead_id}", { params: { path: { campaign_id: id, lead_id: leadId } } }),
      ),
    onSuccess: () => invalidateCampaigns(qc),
  });
}

/** Pre-flight: who gets an offer now (one draft each, in its A/B style), who is skipped and why, price. */
export function useCampaignEstimate(id: number) {
  return useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/campaigns/{campaign_id}/offers/estimate", { params: { path: { campaign_id: id } } })),
  });
}

export function useStartCampaignOffers(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (confirmToken: string) =>
      unwrap(
        api.POST("/api/campaigns/{campaign_id}/offers", {
          params: { path: { campaign_id: id } },
          body: { confirm_token: confirmToken },
        }),
      ),
    onSuccess: () => invalidateCampaigns(qc),
  });
}

export function useCampaignAction(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (action: CampaignAction) => {
      const params = { params: { path: { campaign_id: id } } };
      switch (action) {
        case "start":
          return unwrap(api.POST("/api/campaigns/{campaign_id}/start", params));
        case "pause":
          return unwrap(api.POST("/api/campaigns/{campaign_id}/pause", params));
        case "resume":
          return unwrap(api.POST("/api/campaigns/{campaign_id}/resume", params));
        case "complete":
          return unwrap(api.POST("/api/campaigns/{campaign_id}/complete", params));
        case "archive":
          return unwrap(api.POST("/api/campaigns/{campaign_id}/archive", params));
      }
    },
    onSuccess: () => invalidateCampaigns(qc),
  });
}
