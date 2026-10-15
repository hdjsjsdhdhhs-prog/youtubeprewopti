"use client";

/** Hooks of outreach (Phase 8.1): SMTP accounts (admin), «Send» of an approved offer, the messages and what
 * happened after the send. Every guardrail (approval, content hash, suppression, limits) is enforced by the backend. */
import { keepPreviousData, useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";

import { isMessageActive, type ManualEventType, type MessageStatus } from "@/lib/outreach";

import { api, unwrap, type Schemas } from "./client";
import { qk } from "./hooks";
import { invalidateOffers } from "./offers-hooks";

export type EmailAccount = Schemas["EmailAccountOut"];
export type EmailAccountIn = Schemas["EmailAccountIn"];
export type EmailAccountPatch = Schemas["EmailAccountPatch"];
export type AccountCheck = Schemas["AccountCheck"];
export type Message = Schemas["MessageOut"];
export type MessageEvent = Schemas["MessageEventOut"];
export type MessageList = Schemas["MessageList"];

export const MESSAGES_PAGE = 50;
const MESSAGES_POLL_MS = 5000;

export const outreachKeys = {
  accounts: ["email-accounts"] as const,
  messages: ["messages"] as const,
  messageList: (p: { status?: MessageStatus; lead_id?: number; offset: number }) => ["messages", "list", p] as const,
};

/** A message change moves the offer / lead (scheduled → sent, replied …) and the account counters. */
function invalidateOutreach(qc: QueryClient) {
  return Promise.all([
    invalidateOffers(qc),
    qc.invalidateQueries({ queryKey: outreachKeys.messages }),
    qc.invalidateQueries({ queryKey: outreachKeys.accounts }),
  ]);
}

// --- accounts ------------------------------------------------------------------------------------

export function useEmailAccounts(enabled = true) {
  return useQuery({
    queryKey: outreachKeys.accounts,
    queryFn: () => unwrap(api.GET("/api/email-accounts")),
    staleTime: 15_000,
    enabled,
  });
}

export function useSaveEmailAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (p: { id: number; body: EmailAccountPatch } | { id?: undefined; body: EmailAccountIn }) =>
      unwrap(
        p.id === undefined
          ? api.POST("/api/email-accounts", { body: p.body })
          : api.PATCH("/api/email-accounts/{account_id}", { params: { path: { account_id: p.id } }, body: p.body }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: outreachKeys.accounts }),
  });
}

/** Connect and log in to the SMTP server; nothing is sent. */
export function useVerifyEmailAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      unwrap(api.POST("/api/email-accounts/{account_id}/verify", { params: { path: { account_id: id } } })),
    onSettled: () => qc.invalidateQueries({ queryKey: outreachKeys.accounts }),
  });
}

export function useSetEmailAccountPaused() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, pause }: { id: number; pause: boolean }) =>
      unwrap(
        pause
          ? api.POST("/api/email-accounts/{account_id}/pause", { params: { path: { account_id: id } } })
          : api.POST("/api/email-accounts/{account_id}/resume", { params: { path: { account_id: id } } }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: outreachKeys.accounts }),
  });
}

// --- messages ------------------------------------------------------------------------------------

/** Messages of the workspace (newest first); polls while a shown message can still change by itself. */
export function useMessages(p: { status?: MessageStatus; lead_id?: number; offset: number }) {
  return useQuery({
    queryKey: outreachKeys.messageList(p),
    queryFn: () => unwrap(api.GET("/api/messages", { params: { query: { ...p, limit: MESSAGES_PAGE } } })),
    placeholderData: keepPreviousData,
    refetchInterval: (query) => (query.state.data?.items.some((m) => isMessageActive(m.status)) ? MESSAGES_POLL_MS : false),
  });
}

/** «Send now»: echo the `content_hash` on screen (changed meanwhile → 409 `content_changed`). */
export function useSendOffer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, contentHash, emailAccountId }: { id: number; contentHash: string; emailAccountId?: number | null }) =>
      unwrap(
        api.POST("/api/offers/{offer_id}/send", {
          params: { path: { offer_id: id } },
          body: { content_hash: contentHash, email_account_id: emailAccountId ?? null },
        }),
      ),
    onSuccess: () => invalidateOutreach(qc),
  });
}

export function useCancelMessage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      unwrap(api.POST("/api/messages/{message_id}/cancel", { params: { path: { message_id: id } } })),
    onSuccess: () => invalidateOutreach(qc),
  });
}

/** A manual message (Telegram, VK …) was sent by the operator. */
export function useMarkMessageSent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, note = "" }: { id: number; note?: string }) =>
      unwrap(api.POST("/api/messages/{message_id}/mark-sent", { params: { path: { message_id: id } }, body: { note } })),
    onSuccess: () => invalidateOutreach(qc),
  });
}

/** Delivered / opened / reply / bounce / opt-out; bounce and opt-out also fill the «Не писать» list. */
export function useRecordMessageEvent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, type, note = "" }: { id: number; type: ManualEventType; note?: string }) =>
      unwrap(
        api.POST("/api/messages/{message_id}/events", { params: { path: { message_id: id } }, body: { type, note } }),
      ),
    onSuccess: () =>
      Promise.all([
        invalidateOutreach(qc),
        qc.invalidateQueries({ queryKey: qk.suppression }),
        qc.invalidateQueries({ queryKey: qk.channels }),
      ]),
  });
}
