"use client";

/** Hooks of contacts (Phase 6): a channel's contacts, manual contacts and statuses, the suppression list,
 * Contactability weights, the project scan. Every contact change rescores leads, so lead queries are refreshed. */
import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";

import { api, unwrap, type Schemas } from "./client";
import { qk } from "./hooks";

export type ChannelContacts = Schemas["ChannelContacts"];
export type ContactItem = Schemas["ContactOut"];
export type ContactChange = Schemas["ContactChange"];
export type ManualContactIn = Schemas["ManualContactIn"];
export type ContactStatusIn = Schemas["ContactStatusIn"];
export type SuppressionEntry = Schemas["SuppressionOut"];
export type SuppressionIn = Schemas["SuppressionIn"];
export type ContactStats = Schemas["ContactStats"];
export type ContactScanStartResult = Schemas["ContactScanStartResult"];
export type ContactabilityProfile = Schemas["ContactabilityProfileOut"];
export type ContactabilityWeights = Schemas["ContactabilityWeights"];

export const SUPPRESSION_PAGE = 50;

/** Contacts of a channel: found in its texts + this workspace's manual ones, with the workspace's statuses. */
export function useChannelContacts(channelId: number) {
  return useQuery({
    queryKey: qk.channelContacts(channelId),
    queryFn: () =>
      unwrap(api.GET("/api/channels/{channel_id}/contacts", { params: { path: { channel_id: channelId } } })),
  });
}

/** A change returns the fresh channel contacts; leads (scores, filters), stats and the suppression list follow. */
function afterChange(qc: QueryClient, channelId: number | undefined, data: ContactChange | undefined) {
  if (channelId !== undefined && data) qc.setQueryData(qk.channelContacts(channelId), data.contacts);
  return Promise.all([
    qc.invalidateQueries({ queryKey: qk.leads }),
    qc.invalidateQueries({ queryKey: ["contacts"] }),
    qc.invalidateQueries({ queryKey: qk.suppression }),
  ]);
}

export function useScanChannelContacts(channelId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/channels/{channel_id}/contacts/scan", { params: { path: { channel_id: channelId } } })),
    onSuccess: (data) => afterChange(qc, channelId, data),
  });
}

export function useAddContact(channelId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ManualContactIn) =>
      unwrap(api.POST("/api/channels/{channel_id}/contacts", { params: { path: { channel_id: channelId } }, body })),
    onSuccess: (data) => afterChange(qc, channelId, data),
  });
}

export function useSetContactStatus(channelId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: ContactStatusIn }) =>
      unwrap(api.PATCH("/api/contacts/{contact_id}", { params: { path: { contact_id: id } }, body })),
    onSuccess: (data) => afterChange(qc, channelId, data),
  });
}

export function useDeleteContact(channelId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => unwrap(api.DELETE("/api/contacts/{contact_id}", { params: { path: { contact_id: id } } })),
    onSuccess: (data) => afterChange(qc, channelId, data),
  });
}

// --- suppression list ---------------------------------------------------------------------------

export function useSuppressionList(offset: number) {
  return useQuery({
    queryKey: [...qk.suppression, offset] as const,
    queryFn: () => unwrap(api.GET("/api/suppression-list", { params: { query: { limit: SUPPRESSION_PAGE, offset } } })),
    placeholderData: (previous) => previous,
  });
}

export function useAddSuppression() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: SuppressionIn) => unwrap(api.POST("/api/suppression-list", { body })),
    onSuccess: () => afterChange(qc, undefined, undefined).then(() => qc.invalidateQueries({ queryKey: ["channels"] })),
  });
}

export function useRemoveSuppression() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      unwrap(api.DELETE("/api/suppression-list/{entry_id}", { params: { path: { entry_id: id } } })),
    onSuccess: () => afterChange(qc, undefined, undefined).then(() => qc.invalidateQueries({ queryKey: ["channels"] })),
  });
}

// --- project ------------------------------------------------------------------------------------

export function useProjectContactStats(projectId: number) {
  return useQuery({
    queryKey: qk.contactStats(projectId),
    queryFn: () =>
      unwrap(api.GET("/api/projects/{project_id}/contacts/stats", { params: { path: { project_id: projectId } } })),
  });
}

/** Queue the scan of the project's channels not scanned with the current extractor (free, no network). */
export function useStartContactScan(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (rescan: boolean) =>
      unwrap(
        api.POST("/api/projects/{project_id}/contacts/scan", {
          params: { path: { project_id: projectId } },
          body: { rescan },
        }),
      ),
    onSuccess: () =>
      Promise.all([qc.invalidateQueries({ queryKey: qk.jobs }), qc.invalidateQueries({ queryKey: qk.contactStats(projectId) })]),
  });
}

// --- Contactability weights (ТЗ §19) ---------------------------------------------------------------

export function useContactabilityProfile() {
  return useQuery({
    queryKey: qk.contactabilityProfile,
    queryFn: () => unwrap(api.GET("/api/scoring-profiles/contactability")),
  });
}

export function useSetContactabilityProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ContactabilityWeights) => unwrap(api.PUT("/api/scoring-profiles/contactability", { body })),
    onSuccess: (saved) => {
      qc.setQueryData(qk.contactabilityProfile, saved);
      return Promise.all([qc.invalidateQueries({ queryKey: qk.leads }), qc.invalidateQueries({ queryKey: ["contacts"] })]);
    },
  });
}
