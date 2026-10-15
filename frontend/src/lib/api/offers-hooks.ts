"use client";

/** Hooks of offers and the Approval Queue (Phase 7): estimate → confirmed start, edits, Approve / Reject / Skip /
 * Schedule, the queue and the offer templates. Sending itself (Phase 8) lives in `outreach-hooks.ts`; «Schedule»
 * queues the message of an approved offer. */
import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";

import { isOfferActive, offerNeedsPolling, type OfferStatus } from "@/lib/offers";

import { api, unwrap, type Schemas } from "./client";
import { qk } from "./hooks";

export type Offer = Schemas["OfferOut"];
export type OfferVariant = Schemas["OfferVariantOut"];
export type OfferIn = Schemas["OfferIn"];
export type OfferStart = Schemas["OfferStart"];
export type OfferEstimate = Schemas["OfferEstimate"];
export type OfferEdit = Schemas["OfferEdit"];
export type OfferDecision = Schemas["OfferDecisionIn"]["decision"];
export type LeadOffers = Schemas["LeadOffers"];
export type QueueCard = Schemas["QueueCard"];
export type ApprovalQueue = Schemas["ApprovalQueue"];
export type Sample = Schemas["SampleOut"];
export type OfferTemplate = Schemas["OfferTemplateOut"];
export type OfferTemplateIn = Schemas["OfferTemplateIn"];

export const QUEUE_PAGE = 20;
const OFFERS_POLL_MS = 2000;
const MESSAGE_POLL_MS = 5000;

export const offerKeys = {
  all: ["offers"] as const,
  lead: (leadId: number) => ["offers", "lead", leadId] as const,
  queue: (p: { status: OfferStatus; offset: number }) => ["offers", "queue", p] as const,
  templates: ["offer-templates"] as const,
};

/** Fast while drafts are written, slower while a due message waits for the (per-minute) dispatcher. */
function pollInterval(offers: Offer[] | undefined): number | false {
  if (!offers) return false;
  if (offers.some(isOfferActive)) return OFFERS_POLL_MS;
  return offers.some((o) => offerNeedsPolling(o)) ? MESSAGE_POLL_MS : false;
}

/** Offers of the lead (newest first) with the channel's contacts and the samples; polls while drafts are written
 * or the message is being sent. */
export function useLeadOffers(leadId: number) {
  return useQuery({
    queryKey: offerKeys.lead(leadId),
    queryFn: () => unwrap(api.GET("/api/leads/{lead_id}/offers", { params: { path: { lead_id: leadId } } })),
    refetchInterval: (query) => pollInterval(query.state.data?.items),
  });
}

/** The Approval Queue (ТЗ §22); polls while a shown offer is being drafted or sent. */
export function useApprovalQueue(status: OfferStatus, offset: number) {
  return useQuery({
    queryKey: offerKeys.queue({ status, offset }),
    queryFn: () =>
      unwrap(api.GET("/api/approval-queue", { params: { query: { status, limit: QUEUE_PAGE, offset } } })),
    refetchInterval: (query) => pollInterval(query.state.data?.items.map((c) => c.offer)),
  });
}

/** Any offer change can move it between queue tabs and change the lead status (lists, cards). */
export function invalidateOffers(qc: QueryClient) {
  return Promise.all([
    qc.invalidateQueries({ queryKey: offerKeys.all }),
    qc.invalidateQueries({ queryKey: qk.leads }),
    qc.invalidateQueries({ queryKey: qk.jobs }),
  ]);
}

/** Pre-flight: facts, the exact prompt of variant A, price and budgets. Nothing is generated or charged. */
export function useOfferEstimate(leadId: number) {
  return useMutation({
    mutationFn: (body: OfferIn) =>
      unwrap(api.POST("/api/leads/{lead_id}/offer/estimate", { params: { path: { lead_id: leadId } }, body })),
  });
}

/** Start exactly the confirmed estimate (a new offer or a new round — «Regenerate»). */
export function useStartOffer(leadId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: OfferStart) =>
      unwrap(api.POST("/api/leads/{lead_id}/offers", { params: { path: { lead_id: leadId } }, body })),
    onSuccess: () => invalidateOffers(qc),
  });
}

export function useEditOffer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: OfferEdit }) =>
      unwrap(api.PATCH("/api/offers/{offer_id}", { params: { path: { offer_id: id } }, body })),
    onSuccess: () => invalidateOffers(qc),
  });
}

/** approve must echo the `content_hash` of the offer on screen (changed meanwhile → 409 `content_changed`). */
export function useDecideOffer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      decision,
      contentHash,
      comment = "",
    }: {
      id: number;
      decision: OfferDecision;
      contentHash?: string;
      comment?: string;
    }) =>
      unwrap(
        api.POST("/api/offers/{offer_id}/decision", {
          params: { path: { offer_id: id } },
          body: { decision, comment, content_hash: contentHash ?? null },
        }),
      ),
    onSuccess: () => Promise.all([invalidateOffers(qc), qc.invalidateQueries({ queryKey: qk.feedbackSummary })]),
  });
}

/** Phase 8: queues the message for `scheduledAt` (email: sent by the dispatcher when due; other channels wait for
 * the operator). Scheduling again moves the queued message. `emailAccountId` — the sender (email channel only). */
export function useScheduleOffer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, scheduledAt, emailAccountId }: { id: number; scheduledAt: string; emailAccountId?: number | null }) =>
      unwrap(
        api.POST("/api/offers/{offer_id}/schedule", {
          params: { path: { offer_id: id } },
          body: emailAccountId ? { scheduled_at: scheduledAt, email_account_id: emailAccountId } : { scheduled_at: scheduledAt },
        }),
      ),
    onSuccess: () => Promise.all([invalidateOffers(qc), qc.invalidateQueries({ queryKey: ["messages"] })]),
  });
}

/** Cancels the queued message as well; the offer stays approved. */
export function useUnscheduleOffer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      unwrap(api.DELETE("/api/offers/{offer_id}/schedule", { params: { path: { offer_id: id } } })),
    onSuccess: () => Promise.all([invalidateOffers(qc), qc.invalidateQueries({ queryKey: ["messages"] })]),
  });
}

// --- templates ---------------------------------------------------------------------------------

export function useOfferTemplates() {
  return useQuery({
    queryKey: offerKeys.templates,
    queryFn: () => unwrap(api.GET("/api/offer-templates")),
    staleTime: 30_000,
  });
}

export function useSaveOfferTemplate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id?: number; body: OfferTemplateIn }) =>
      unwrap(
        id === undefined
          ? api.POST("/api/offer-templates", { body })
          : api.PUT("/api/offer-templates/{template_id}", { params: { path: { template_id: id } }, body }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: offerKeys.templates }),
  });
}

export function useArchiveOfferTemplate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      unwrap(api.DELETE("/api/offer-templates/{template_id}", { params: { path: { template_id: id } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: offerKeys.templates }),
  });
}
