"use client";

import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { api, unwrap, type Schemas } from "./client";

export type Me = Schemas["MeResponse"];
export type Project = Schemas["ProjectOut"];
export type ProjectCreate = Schemas["ProjectCreate"];
export type ProjectUpdate = Schemas["ProjectUpdate"];
export type ProjectStatus = Schemas["ProjectStatus"];
export type SearchQuery = Schemas["QueryOut"];
export type QueryBulkResult = Schemas["QueryBulkResult"];
export type Channel = Schemas["ChannelOut"];
export type ChannelDetail = Schemas["ChannelDetail"];
export type Video = Schemas["VideoOut"];
export type ChannelSort = Schemas["ChannelSort"];
export type SortOrder = Schemas["SortOrder"];
export type Job = Schemas["JobRunOut"];
export type JobStatus = Schemas["JobStatus"];
export type ChannelMetrics = Schemas["ChannelMetricsOut"];
export type ChannelFilterSet = Schemas["ChannelFilterSet"];
export type TaxonomyNode = Schemas["TaxonomyNodeOut"];
export type TaxonomyLevel = Schemas["TaxonomyLevel"];
export type SearchType = Schemas["SearchType"];
export type Quota = Schemas["QuotaOut"];
export type DiscoveryStartResult = Schemas["DiscoveryStartResult"];
export type ThumbnailStats = Schemas["ThumbnailStatsOut"];
export type ThumbnailIngestResult = Schemas["ThumbnailIngestResult"];
export type ImageMetrics = Schemas["ImageMetricsOut"];
export type PrefilterPreview = Schemas["PrefilterPreview"];
export type AnalysisEstimate = Schemas["AnalysisEstimate"];
export type AnalysisEstimateRequest = Schemas["AnalysisEstimateRequest"];
export type AIStatus = Schemas["AIStatusOut"];

export const ACTIVE_JOB_STATUSES: ReadonlySet<JobStatus> = new Set(["queued", "running", "retrying"]);

/** Query keys: one tree per resource so mutations can invalidate a whole branch. */
export const qk = {
  me: ["me"] as const,
  projects: ["projects"] as const,
  projectList: (p: { status?: ProjectStatus; offset: number }) => ["projects", "list", p] as const,
  project: (id: number) => ["projects", "detail", id] as const,
  projectQueries: (id: number, offset: number) => ["projects", "detail", id, "queries", offset] as const,
  channels: ["channels"] as const,
  channelList: (f: ChannelFilters) => ["channels", "list", f] as const,
  channel: (id: number) => ["channels", "detail", id] as const,
  channelVideos: (id: number, offset: number) => ["channels", "detail", id, "videos", offset] as const,
  projectNiches: (id: number) => ["projects", "detail", id, "niches"] as const,
  jobs: ["jobs"] as const,
  jobList: (p: { status?: JobStatus; type?: string; project_id?: number; offset: number; limit: number }) =>
    ["jobs", "list", p] as const,
  taxonomy: ["taxonomy"] as const,
  quota: ["youtube", "quota"] as const,
  thumbnailStats: (id: number) => ["projects", "detail", id, "thumbnail-stats"] as const,
  aiStatus: ["ai", "status"] as const,
};

// --- auth -------------------------------------------------------------------------------------

export function useMe() {
  return useQuery({
    queryKey: qk.me,
    queryFn: () => unwrap(api.GET("/api/auth/me")),
    retry: false,
    staleTime: 5 * 60_000,
  });
}

export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["LoginRequest"]) => unwrap(api.POST("/api/auth/login", { body })),
    onSuccess: (me) => {
      qc.clear();
      qc.setQueryData(qk.me, me);
    },
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/auth/logout")),
    onSettled: () => qc.clear(),
  });
}

// --- projects ---------------------------------------------------------------------------------

export const PROJECTS_PAGE = 50;

export function useProjects(p: { status?: ProjectStatus; offset: number }) {
  return useQuery({
    queryKey: qk.projectList(p),
    queryFn: () =>
      unwrap(api.GET("/api/projects", { params: { query: { ...p, limit: PROJECTS_PAGE } } })),
    placeholderData: keepPreviousData,
  });
}

export function useProject(id: number, enabled = true) {
  return useQuery({
    queryKey: qk.project(id),
    queryFn: () => unwrap(api.GET("/api/projects/{project_id}", { params: { path: { project_id: id } } })),
    enabled,
  });
}

export function useCreateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ProjectCreate) => unwrap(api.POST("/api/projects", { body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.projects }),
  });
}

export function useUpdateProject(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ProjectUpdate) =>
      unwrap(api.PATCH("/api/projects/{project_id}", { params: { path: { project_id: id } }, body })),
    onSuccess: (project) => {
      qc.setQueryData(qk.project(id), project);
      return qc.invalidateQueries({ queryKey: qk.projects });
    },
  });
}

export function useDeleteProject(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.DELETE("/api/projects/{project_id}", { params: { path: { project_id: id } } })),
    onSuccess: () => {
      qc.removeQueries({ queryKey: qk.project(id) });
      return Promise.all([
        qc.invalidateQueries({ queryKey: qk.projects }),
        qc.invalidateQueries({ queryKey: qk.channels }),
      ]);
    },
  });
}

export const QUERIES_PAGE = 100;

export function useProjectQueries(id: number, offset: number) {
  return useQuery({
    queryKey: qk.projectQueries(id, offset),
    queryFn: () =>
      unwrap(
        api.GET("/api/projects/{project_id}/queries", {
          params: { path: { project_id: id }, query: { limit: QUERIES_PAGE, offset } },
        }),
      ),
    placeholderData: keepPreviousData,
  });
}

export type QueryImport = { text: string; taxonomy_node_id?: number | null; search_type: SearchType };

export function useImportQueries(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: QueryImport) =>
      unwrap(
        api.POST("/api/projects/{project_id}/queries/bulk", {
          params: { path: { project_id: id } },
          body,
        }),
      ),
    // Refreshes both the queries list and queries_count on the project.
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.projects }),
  });
}

export function useUpdateQuery(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ queryId, body }: { queryId: number; body: Schemas["QueryUpdate"] }) =>
      unwrap(
        api.PATCH("/api/projects/{project_id}/queries/{query_id}", {
          params: { path: { project_id: projectId, query_id: queryId } },
          body,
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.projects }),
  });
}

// --- niches (taxonomy) ------------------------------------------------------------------------

export function useTaxonomy() {
  return useQuery({
    queryKey: qk.taxonomy,
    queryFn: () => unwrap(api.GET("/api/taxonomy")),
    staleTime: 60_000,
  });
}

export function useCreateTaxonomy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["TaxonomyBulkImport"]) => unwrap(api.POST("/api/taxonomy/bulk", { body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.taxonomy }),
  });
}

export function useProjectNiches(id: number) {
  return useQuery({
    queryKey: qk.projectNiches(id),
    queryFn: () => unwrap(api.GET("/api/projects/{project_id}/niches", { params: { path: { project_id: id } } })),
  });
}

export function useSetProjectNiches(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (taxonomy_node_ids: number[]) =>
      unwrap(
        api.PUT("/api/projects/{project_id}/niches", {
          params: { path: { project_id: id } },
          body: { taxonomy_node_ids },
        }),
      ),
    onSuccess: (niches) => qc.setQueryData(qk.projectNiches(id), niches),
  });
}

// --- discovery --------------------------------------------------------------------------------

export function useYoutubeQuota() {
  return useQuery({ queryKey: qk.quota, queryFn: () => unwrap(api.GET("/api/youtube/quota")), staleTime: 10_000 });
}

export function useStartDiscovery(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["DiscoveryStart"]) =>
      unwrap(api.POST("/api/projects/{project_id}/discovery", { params: { path: { project_id: projectId } }, body })),
    onSuccess: () =>
      Promise.all([
        qc.invalidateQueries({ queryKey: qk.jobs }),
        qc.invalidateQueries({ queryKey: qk.quota }),
        qc.invalidateQueries({ queryKey: qk.project(projectId) }),
      ]),
  });
}

export function useDeleteQuery(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (queryId: number) =>
      unwrap(
        api.DELETE("/api/projects/{project_id}/queries/{query_id}", {
          params: { path: { project_id: projectId, query_id: queryId } },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.projects }),
  });
}

// --- channels ---------------------------------------------------------------------------------

/** List filters = the §3 filter set (also stored per project) + search, project scope and sorting. */
export type ChannelFilters = Partial<ChannelFilterSet> & {
  project_id?: number;
  q?: string;
  sort: ChannelSort;
  order: SortOrder;
};

export const CHANNELS_PAGE = 200;

/** Offset-paged infinite list; the table virtualizes whatever pages are loaded. */
export function useChannels(filters: ChannelFilters) {
  return useInfiniteQuery({
    queryKey: qk.channelList(filters),
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/channels", {
          params: { query: { ...filters, limit: CHANNELS_PAGE, offset: pageParam } },
        }),
      ),
    getNextPageParam: (last) => {
      const next = last.offset + last.items.length;
      return next < last.total && last.items.length > 0 ? next : undefined;
    },
    placeholderData: keepPreviousData,
  });
}

export function useChannel(id: number) {
  return useQuery({
    queryKey: qk.channel(id),
    queryFn: () => unwrap(api.GET("/api/channels/{channel_id}", { params: { path: { channel_id: id } } })),
  });
}

export const VIDEOS_PAGE = 48;

export function useChannelVideos(id: number, offset: number) {
  return useQuery({
    queryKey: qk.channelVideos(id, offset),
    queryFn: () =>
      unwrap(
        api.GET("/api/channels/{channel_id}/videos", {
          params: { path: { channel_id: id }, query: { limit: VIDEOS_PAGE, offset } },
        }),
      ),
    placeholderData: keepPreviousData,
  });
}

export function useDownloadChannelThumbnails(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/channels/{channel_id}/thumbnails/download", { params: { path: { channel_id: id } } }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.jobs }),
  });
}

// --- thumbnails: ingestion, prefilter, AI estimate (Phase 3) ------------------------------------

export function useThumbnailStats(projectId: number) {
  return useQuery({
    queryKey: qk.thumbnailStats(projectId),
    queryFn: () =>
      unwrap(api.GET("/api/projects/{project_id}/thumbnails/stats", { params: { path: { project_id: projectId } } })),
  });
}

export function useIngestProjectThumbnails(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/projects/{project_id}/thumbnails/download", { params: { path: { project_id: projectId } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.jobs }),
  });
}

/** Read-only preview of the prefilter selection (POST because the unsaved settings travel in the body). */
export function usePrefilterPreview(projectId: number) {
  return useMutation({
    mutationFn: (body: Schemas["ThumbnailPrefilter"]) =>
      unwrap(
        api.POST("/api/projects/{project_id}/prefilter/preview", { params: { path: { project_id: projectId } }, body }),
      ),
  });
}

/** Pre-flight cost estimate of the AI thumbnail audit (budget gate). Nothing is charged. */
export function useAnalysisEstimate(projectId: number) {
  return useMutation({
    mutationFn: (body: AnalysisEstimateRequest) =>
      unwrap(
        api.POST("/api/projects/{project_id}/thumbnail-analysis/estimate", {
          params: { path: { project_id: projectId } },
          body,
        }),
      ),
  });
}

export function useAIStatus() {
  return useQuery({ queryKey: qk.aiStatus, queryFn: () => unwrap(api.GET("/api/ai/status")), staleTime: 60_000 });
}

// --- jobs -------------------------------------------------------------------------------------

export const JOBS_PAGE = 50;
const JOBS_POLL_MS = 2000;

export function useJobs(p: { status?: JobStatus; type?: string; project_id?: number; offset: number }, limit = JOBS_PAGE) {
  return useQuery({
    queryKey: qk.jobList({ ...p, limit }),
    queryFn: () => unwrap(api.GET("/api/jobs", { params: { query: { ...p, limit } } })),
    placeholderData: keepPreviousData,
    // Poll only while something on the page can still change.
    refetchInterval: (query) =>
      query.state.data?.items.some((j) => ACTIVE_JOB_STATUSES.has(j.status)) ? JOBS_POLL_MS : false,
  });
}

export function useCancelJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (jobId: number) =>
      unwrap(api.POST("/api/jobs/{job_id}/cancel", { params: { path: { job_id: jobId } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.jobs }),
  });
}
