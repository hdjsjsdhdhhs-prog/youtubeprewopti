"use client";

/** Hooks of thumbnail generation (Phase 5): estimate → confirmed start, results, decisions, style references. */
import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";

import { isGenerationActive } from "@/lib/generation";

import { api, unwrap, type Schemas } from "./client";
import { qk } from "./hooks";

export type GenerationIn = Schemas["GenerationIn"];
export type GenerationStart = Schemas["GenerationStart"];
export type GenerationEstimate = Schemas["GenerationEstimate"];
export type GenerationStartResult = Schemas["GenerationStartResult"];
export type Generation = Schemas["GenerationOut"];
export type GenerationVariant = Schemas["VariantOut"];
export type LeadGenerations = Schemas["LeadGenerations"];
export type ComparisonOriginal = Schemas["ComparisonOriginal"];
export type SourceCandidate = Schemas["SourceCandidate"];
export type VariantDecision = Schemas["VariantDecision"]["decision"];
export type StyleReference = Schemas["ReferenceOut"];
export type ReferenceIn = Schemas["ReferenceIn"];
export type ReferencePatch = Schemas["ReferencePatch"];

const GENERATIONS_POLL_MS = 2000;

/** All generations of the lead with their variants and the originals («Original vs Generated»). Polls while a
 * generation is queued or running. */
export function useLeadGenerations(leadId: number) {
  return useQuery({
    queryKey: qk.leadGenerations(leadId),
    queryFn: () =>
      unwrap(api.GET("/api/leads/{lead_id}/generations", { params: { path: { lead_id: leadId } } })),
    refetchInterval: (query) => (query.state.data?.items.some(isGenerationActive) ? GENERATIONS_POLL_MS : false),
  });
}

/** Pre-flight estimate: brief, prompt of variant A, price and budgets. Nothing is generated or charged. */
export function useGenerationEstimate(leadId: number) {
  return useMutation({
    mutationFn: (body: GenerationIn) =>
      unwrap(api.POST("/api/leads/{lead_id}/generation/estimate", { params: { path: { lead_id: leadId } }, body })),
  });
}

/** A start, a finished run or a decision can change the lead status (lists, cards) and the jobs page. */
export function invalidateAfterGeneration(qc: QueryClient, leadId: number) {
  return Promise.all([
    qc.invalidateQueries({ queryKey: qk.leadGenerations(leadId) }),
    qc.invalidateQueries({ queryKey: qk.leads }),
    qc.invalidateQueries({ queryKey: qk.jobs }),
  ]);
}

/** Start exactly the confirmed estimate (409 `confirmation_required` if it changed, `generation_running`). */
export function useStartGeneration(leadId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: GenerationStart) =>
      unwrap(api.POST("/api/leads/{lead_id}/generations", { params: { path: { lead_id: leadId } }, body })),
    onSuccess: () => invalidateAfterGeneration(qc, leadId),
  });
}

/** approve / reject / final / reset; the response is the fresh lead generations. */
export function useDecideVariant(leadId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, decision, comment = "" }: { id: number; decision: VariantDecision; comment?: string }) =>
      unwrap(
        api.POST("/api/generated-thumbnails/{variant_id}/decision", {
          params: { path: { variant_id: id } },
          body: { decision, comment },
        }),
      ),
    onSuccess: (data) => {
      qc.setQueryData(qk.leadGenerations(leadId), data);
      return qc.invalidateQueries({ queryKey: qk.feedbackSummary });
    },
  });
}

// --- style references (ТЗ §43) -----------------------------------------------------------------

export function useReferences() {
  return useQuery({ queryKey: qk.references, queryFn: () => unwrap(api.GET("/api/references")), staleTime: 30_000 });
}

export function useCreateReference() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ReferenceIn) => unwrap(api.POST("/api/references", { body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.references }),
  });
}

export function useUpdateReference() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: ReferencePatch }) =>
      unwrap(api.PATCH("/api/references/{reference_id}", { params: { path: { reference_id: id } }, body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.references }),
  });
}

export function useDeleteReference() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      unwrap(api.DELETE("/api/references/{reference_id}", { params: { path: { reference_id: id } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.references }),
  });
}
