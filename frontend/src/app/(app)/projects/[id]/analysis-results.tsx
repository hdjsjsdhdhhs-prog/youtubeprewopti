"use client";

import { useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, ImageOff } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { JobStatusBadge } from "@/components/status-badges";
import { Badge, Card, Label, Pager, Select } from "@/components/ui";
import {
  ANALYSES_PAGE,
  qk,
  useJobs,
  useThumbnailAnalyses,
  type AnalysisQuery,
  type AnalyzedVideo,
  type Job,
  type Project,
  type ThumbnailAnalysis,
} from "@/lib/api/hooks";
import {
  LEVEL_LABELS,
  PROBLEM_CODES,
  SUBSCORE_LABELS,
  SUBSCORES,
  formatScore,
  opportunityTone,
  problemLabel,
  scoreTone,
  type Level,
  type ProblemCode,
} from "@/lib/analysis";
import { formatCount, formatDate, formatDateTime } from "@/lib/format";
import { formatUsd } from "@/lib/prefilter";
import { cn } from "@/lib/utils";

const RUNS_SHOWN = 3;
const SORTS: { value: string; label: string; query: Pick<AnalysisQuery, "sort" | "order"> }[] = [
  { value: "overall-asc", label: "Сначала слабые (лучшие кандидаты на редизайн)", query: { sort: "overall", order: "asc" } },
  { value: "overall-desc", label: "Сначала сильные", query: { sort: "overall", order: "desc" } },
  { value: "redesign-desc", label: "Потенциал редизайна ↓", query: { sort: "redesign_potential", order: "desc" } },
  { value: "views-desc", label: "Просмотры ↓", query: { sort: "views", order: "desc" } },
  { value: "published-desc", label: "Сначала новые видео", query: { sort: "published_at", order: "desc" } },
];

/** Phase 3.5: AI thumbnail audit runs and results (structured, filterable; overall score computed by code). */
export function AnalysisResults({ project: p }: { project: Project }) {
  const qc = useQueryClient();
  const [sortKey, setSortKey] = useState(SORTS[0].value);
  const [opportunity, setOpportunity] = useState<Level | "">("");
  const [problem, setProblem] = useState<ProblemCode | "">("");
  const [offset, setOffset] = useState(0);
  const query: AnalysisQuery = {
    ...SORTS.find((s) => s.value === sortKey)!.query,
    opportunity: opportunity || undefined,
    problem: problem || undefined,
    offset,
  };
  const list = useThumbnailAnalyses(p.id, query);
  const runs = useJobs({ type: "thumbnail_analysis", project_id: p.id, offset: 0 }, RUNS_SHOWN);

  // Refresh the results whenever an analysis run of this project changes state (also shows partial progress).
  const runKey = (runs.data?.items ?? []).map((j) => `${j.id}:${j.status}:${j.progress_done}`).join(",");
  const previous = useRef(runKey);
  useEffect(() => {
    if (previous.current && previous.current !== runKey) {
      void qc.invalidateQueries({ queryKey: qk.thumbnailAnalyses(p.id) });
    }
    previous.current = runKey;
  }, [runKey, p.id, qc]);

  const resetPage = <T,>(set: (v: T) => void) => (v: T) => {
    set(v);
    setOffset(0);
  };
  const d = list.data;
  const s = d?.summary;
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">Результаты AI-аудита превью</h2>
          <p className="mt-0.5 text-xs text-zinc-500">
            Оценки 1–10 по компонентам; общий балл считает код по весам профиля
            {d ? ` (версия ${d.scoring.version}${d.scoring.is_default ? ", по умолчанию" : ""})` : ""}, а не AI. Не
            прогноз CTR — оценка визуального потенциала улучшения.
          </p>
        </div>
        {s && s.mock_results > 0 ? <Badge tone="violet">mock: {formatCount(s.mock_results)} демо-результатов</Badge> : null}
      </div>

      <AnalysisRuns runs={runs.data?.items ?? []} />

      {s && s.videos_analyzed > 0 ? (
        <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
          <span>
            Проанализировано видео: <b>{formatCount(s.videos_analyzed)}</b>
          </span>
          <span>
            Средний балл: <b>{formatScore(s.avg_overall)}</b>
          </span>
          <span className="flex items-center gap-1">
            Потенциал улучшения:
            {(["high", "medium", "low"] as const).map((l) => (
              <Badge key={l} tone={opportunityTone(l)}>
                {LEVEL_LABELS[l]} {formatCount(s.opportunity[l] ?? 0)}
              </Badge>
            ))}
          </span>
        </div>
      ) : null}
      {s && s.top_problems.length > 0 ? (
        <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs">
          <span className="text-zinc-500">Частые проблемы:</span>
          {s.top_problems.map((t) => (
            <button
              key={t.code}
              type="button"
              onClick={() => resetPage(setProblem)(problem === t.code ? "" : (t.code as ProblemCode))}
              className={cn(
                "rounded border px-1.5 py-0.5",
                problem === t.code
                  ? "border-blue-500 bg-blue-50 text-blue-800 dark:bg-blue-950 dark:text-blue-200"
                  : "border-zinc-200 hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-800",
              )}
            >
              {problemLabel(t.code)} · {t.count}
            </button>
          ))}
        </div>
      ) : null}

      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div>
          <Label htmlFor="ar-sort">Сортировка</Label>
          <Select id="ar-sort" value={sortKey} onChange={(e) => resetPage(setSortKey)(e.target.value)}>
            {SORTS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor="ar-opportunity">Потенциал улучшения</Label>
          <Select
            id="ar-opportunity"
            value={opportunity}
            onChange={(e) => resetPage(setOpportunity)(e.target.value as Level | "")}
          >
            <option value="">любой</option>
            {(["high", "medium", "low"] as const).map((l) => (
              <option key={l} value={l}>
                {LEVEL_LABELS[l]}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor="ar-problem">Проблема</Label>
          <Select id="ar-problem" value={problem} onChange={(e) => resetPage(setProblem)(e.target.value as ProblemCode | "")}>
            <option value="">любая</option>
            {PROBLEM_CODES.map((c) => (
              <option key={c} value={c}>
                {problemLabel(c)}
              </option>
            ))}
          </Select>
        </div>
      </div>

      <div className="mt-3">
        {list.isPending ? (
          <LoadingState />
        ) : list.isError ? (
          <ErrorState error={list.error} onRetry={() => void list.refetch()} />
        ) : d!.items.length === 0 ? (
          <EmptyState
            title={s && s.videos_analyzed > 0 ? "Нет результатов под выбранные фильтры" : "AI-аудитов пока нет"}
            hint={
              s && s.videos_analyzed > 0
                ? "Сбросьте фильтры по проблеме или потенциалу."
                : "Оцените отбор выше и подтвердите запуск — результаты появятся здесь по мере анализа."
            }
          />
        ) : (
          <>
            <ul className="space-y-2">
              {d!.items.map((item) => (
                <ResultRow key={item.video_id} item={item} />
              ))}
            </ul>
            <Pager offset={offset} limit={ANALYSES_PAGE} total={d!.total} onChange={setOffset} />
          </>
        )}
      </div>
    </Card>
  );
}

function AnalysisRuns({ runs }: { runs: Job[] }) {
  if (runs.length === 0) return null;
  return (
    <ul className="mt-3 divide-y divide-zinc-100 border-y border-zinc-100 text-xs dark:divide-zinc-800 dark:border-zinc-800">
      {runs.map((j) => {
        const r = (j.result ?? {}) as Partial<Record<string, number>>;
        return (
          <li key={j.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
            <JobStatusBadge status={j.status} />
            <span className="tabular-nums text-zinc-500">
              {formatDateTime(j.created_at)} · {j.progress_done}/{j.progress_total}
            </span>
            {j.status === "completed" ? (
              <span>
                новых {formatCount(r.analyzed)} · из кэша {formatCount(r.cached)} · похожих (pHash) {formatCount(r.reused)}
                {r.failed ? <span className="text-red-600"> · ошибок {r.failed}</span> : null}
              </span>
            ) : null}
            <span className="text-zinc-500">потрачено {formatUsd(j.actual_cost_usd)}</span>
            {j.error_human ? <span className="text-red-700 dark:text-red-400">{j.error_human}</span> : null}
          </li>
        );
      })}
    </ul>
  );
}

function ResultRow({ item }: { item: AnalyzedVideo }) {
  const [open, setOpen] = useState(false);
  const a = item.analysis;
  return (
    <li className="rounded-md border border-zinc-200 dark:border-zinc-800">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-start gap-3 p-2 text-left hover:bg-zinc-50 dark:hover:bg-zinc-800/50"
      >
        <Thumb assetId={a.image_asset_id} title={item.title} className="w-40 shrink-0" />
        <div className="min-w-0 flex-1">
          <p className="line-clamp-2 text-sm font-medium" title={item.title}>
            {item.title}
          </p>
          <p className="mt-0.5 text-xs text-zinc-500">
            {item.channel_title} · {formatCount(item.view_count)} просм. · {formatDate(item.published_at)}
          </p>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            <Badge tone={scoreTone(a.overall_score)} title="Общий балл: взвешенное среднее компонентов (считает код)">
              {formatScore(a.overall_score)} / 10
            </Badge>
            <Badge tone={opportunityTone(a.improvement_opportunity)} title="Вероятный визуальный потенциал улучшения">
              потенциал: {LEVEL_LABELS[a.improvement_opportunity]}
            </Badge>
            {a.problems.slice(0, 3).map((pr, i) => (
              <Badge key={`${pr.code}-${i}`} tone={pr.severity === "high" ? "red" : "neutral"}>
                {problemLabel(pr.code)}
              </Badge>
            ))}
            {a.provider === "mock" ? <Badge tone="violet">mock</Badge> : null}
          </div>
        </div>
        {open ? (
          <ChevronDown className="size-4 shrink-0 text-zinc-400" aria-hidden />
        ) : (
          <ChevronRight className="size-4 shrink-0 text-zinc-400" aria-hidden />
        )}
      </button>
      {open ? <AuditDetails a={a} channelId={item.channel_id} /> : null}
    </li>
  );
}

function Thumb({ assetId, title, className }: { assetId: number; title: string; className?: string }) {
  const [broken, setBroken] = useState(false);
  return (
    <div className={cn("aspect-video overflow-hidden rounded bg-zinc-100 dark:bg-zinc-800", className)}>
      {broken ? (
        <div className="flex size-full items-center justify-center text-zinc-400">
          <ImageOff className="size-5" aria-hidden />
        </div>
      ) : (
        // Served by our API with the session cookie and an access check (see channel page).
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={`/api/images/${assetId}`}
          alt={`Превью: ${title}`}
          loading="lazy"
          onError={() => setBroken(true)}
          className="size-full object-cover"
        />
      )}
    </div>
  );
}

function AuditDetails({ a, channelId }: { a: ThumbnailAnalysis; channelId: number }) {
  return (
    <div className="grid gap-4 border-t border-zinc-200 p-3 text-sm md:grid-cols-2 dark:border-zinc-800">
      <div>
        <p>{a.visual_summary}</p>
        <dl className="mt-3 space-y-1">
          {SUBSCORES.map((k) => {
            const v = Number(a[k]);
            return (
              <div key={k} className="flex items-center gap-2 text-xs">
                <dt className="w-44 shrink-0 text-zinc-600 dark:text-zinc-400">
                  {SUBSCORE_LABELS[k]}
                  {k === "redesign_potential" ? <span title="Чем выше, тем больше выигрыш от редизайна"> *</span> : null}
                </dt>
                <dd className="flex flex-1 items-center gap-2">
                  <span className="h-1.5 flex-1 overflow-hidden rounded bg-zinc-100 dark:bg-zinc-800">
                    <span
                      className={cn(
                        "block h-full",
                        k === "redesign_potential"
                          ? "bg-violet-500"
                          : { red: "bg-red-500", amber: "bg-amber-500", green: "bg-emerald-500" }[scoreTone(v)],
                      )}
                      style={{ width: `${(v / 10) * 100}%` }}
                    />
                  </span>
                  <span className="w-8 text-right tabular-nums">{formatScore(v)}</span>
                </dd>
              </div>
            );
          })}
        </dl>
        <p className="mt-2 text-xs text-zinc-500">
          Лицо: {a.has_face ? `да${a.face_emotion ? ` (${a.face_emotion})` : ""}` : "нет"} · текст:{" "}
          {a.text_present ? `да, ≈${a.text_word_count_est} сл.` : "нет"} · фокус: {a.focal_point} · отделение от фона:{" "}
          {LEVEL_LABELS[a.background_separation]} · перегруженность: {LEVEL_LABELS[a.clutter_level]}
        </p>
      </div>
      <div className="space-y-3">
        {a.problems.length > 0 ? (
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">Проблемы</h3>
            <ul className="mt-1 space-y-1">
              {a.problems.map((pr, i) => (
                <li key={i}>
                  <Badge tone={pr.severity === "high" ? "red" : pr.severity === "medium" ? "amber" : "neutral"}>
                    {problemLabel(pr.code)}
                  </Badge>{" "}
                  {pr.description}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {a.recommendations.length > 0 ? (
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">Что изменить</h3>
            <ul className="mt-1 list-inside list-disc space-y-1">
              {a.recommendations.map((r, i) => (
                <li key={i}>
                  <b>{r.action}</b> <span className="text-zinc-500">— {r.rationale}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        <p className="text-xs text-zinc-500">
          {a.provider} · <code>{a.model_key}</code> · detail={a.detail} · {formatDateTime(a.created_at)}
          {a.reused_from_id !== null
            ? ` · взято из аудита визуально идентичного превью (pHash, расстояние ${a.phash_distance ?? "?"})`
            : ""}
          {" · "}
          <Link href={`/channels/${channelId}`} className="underline">
            канал
          </Link>
        </p>
      </div>
    </div>
  );
}
