"use client";

/** Hooks of the human workflow (Phase 4): manual statuses, bulk actions, tags, favorites, human scores. */
import { keepPreviousData, useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";

import { api, unwrap, type Schemas } from "./client";
import { qk, type FavoriteEntity, type LeadBulkRequest, type ScoreOverrideIn } from "./hooks";

/** Status / tag / favorite changes touch lead lists and cards, tag counts and the feedback summary. */
function invalidateWorkflow(qc: QueryClient) {
  return Promise.all([
    qc.invalidateQueries({ queryKey: qk.leads }),
    qc.invalidateQueries({ queryKey: qk.tags }),
    qc.invalidateQueries({ queryKey: qk.favorites }),
    qc.invalidateQueries({ queryKey: qk.feedbackSummary }),
  ]);
}

/** Manual decision on one lead (shortlist / select / back / reject / restore). */
export function useSetLeadStatus() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, status, reason = "" }: { id: number; status: Schemas["LeadStatus"]; reason?: string }) =>
      unwrap(api.POST("/api/leads/{lead_id}/status", { params: { path: { lead_id: id } }, body: { status, reason } })),
    onSuccess: (lead) => {
      qc.setQueryData(qk.lead(lead.id), lead);
      return invalidateWorkflow(qc);
    },
  });
}

/** One action on chosen rows or on «all filtered» (ТЗ §11). */
export function useBulkLeads() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: LeadBulkRequest) => unwrap(api.POST("/api/leads/bulk", { body })),
    onSuccess: () => invalidateWorkflow(qc),
  });
}

export function useTags() {
  return useQuery({ queryKey: qk.tags, queryFn: () => unwrap(api.GET("/api/tags")), staleTime: 30_000 });
}

export function useCreateTag() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["TagIn"]) => unwrap(api.POST("/api/tags", { body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.tags }),
  });
}

export function useUpdateTag() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: Schemas["TagPatch"] }) =>
      unwrap(api.PATCH("/api/tags/{tag_id}", { params: { path: { tag_id: id } }, body })),
    onSuccess: () => invalidateWorkflow(qc),
  });
}

export function useDeleteTag() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => unwrap(api.DELETE("/api/tags/{tag_id}", { params: { path: { tag_id: id } } })),
    onSuccess: () => invalidateWorkflow(qc),
  });
}

export const FAVORITES_PAGE = 48;

export function useFavorites(p: { entity_type?: FavoriteEntity; offset: number }) {
  return useQuery({
    queryKey: qk.favoriteList(p),
    queryFn: () => unwrap(api.GET("/api/favorites", { params: { query: { ...p, limit: FAVORITES_PAGE } } })),
    placeholderData: keepPreviousData,
  });
}

/** Personal favorite on / off; also refreshes the thumbnail audit lists (project tree) that show the star. */
export function useToggleFavorite() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ type, id, on }: { type: FavoriteEntity; id: number; on: boolean }) => {
      const params = { path: { entity_type: type, entity_id: id } };
      return on
        ? unwrap(api.PUT("/api/favorites/{entity_type}/{entity_id}", { params }))
        : unwrap(api.DELETE("/api/favorites/{entity_type}/{entity_id}", { params }));
    },
    onSuccess: () => Promise.all([invalidateWorkflow(qc), qc.invalidateQueries({ queryKey: qk.projects })]),
  });
}

/** A human score may rescore leads, so lists, audits (projects tree) and channel reports are refreshed. */
function invalidateScores(qc: QueryClient) {
  return Promise.all([
    invalidateWorkflow(qc),
    qc.invalidateQueries({ queryKey: qk.projects }),
    qc.invalidateQueries({ queryKey: qk.channels }),
  ]);
}

/** Human score next to the AI value (ТЗ §47); the AI value is never overwritten. */
export function useSetScoreOverride() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ScoreOverrideIn) => unwrap(api.PUT("/api/score-overrides", { body })),
    onSuccess: () => invalidateScores(qc),
  });
}

export function useRemoveScoreOverride() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      unwrap(api.DELETE("/api/score-overrides/{override_id}", { params: { path: { override_id: id } } })),
    onSuccess: () => invalidateScores(qc),
  });
}

export function useFeedbackSummary() {
  return useQuery({ queryKey: qk.feedbackSummary, queryFn: () => unwrap(api.GET("/api/feedback/summary")) });
}
