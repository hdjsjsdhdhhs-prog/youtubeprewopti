"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Calculator, ChevronDown, ChevronRight, Play } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { ChannelAuditDetails, ChannelAuditHeadline } from "@/components/channel-audit";
import { EmptyState, ErrorState, InlineError, LoadingState } from "@/components/states";
import { JobStatusBadge } from "@/components/status-badges";
import { Badge, Button, Card, Label, Pager, Select } from "@/components/ui";
import { LEVEL_LABELS, PROBLEM_CODES, opportunityTone, problemLabel, type Level, type ProblemCode } from "@/lib/analysis";
import { ApiError } from "@/lib/api/client";
import {
  CHANNEL_ANALYSES_PAGE,
  qk,
  useAIStatus,
  useChannelAnalyses,
  useChannelAnalysisEstimate,
  useJobs,
  useStartChannelAnalysis,
  type AnalysisStartResult,
  type AnalyzedChannel,
  type ChannelAnalysisEstimate,
  type ChannelAnalysisQuery,
  type ChannelAnalysisRequest,
  type Job,
  type Project,
} from "@/lib/api/hooks";
import { formatCount, formatDateTime } from "@/lib/format";
import { formatUsd } from "@/lib/prefilter";

import { AIProviderBadge } from "./thumbnail-analysis";

const RUNS_SHOWN = 3;
const PER_CHANNEL = [3, 4, 5, 6, 8, 10, 12];
const SORTS: { value: string; label: string; query: Pick<ChannelAnalysisQuery, "sort" | "order"> }[] = [
  { value: "avg-asc", label: "Сначала со слабыми превью", query: { sort: "avg_score", order: "asc" } },
  { value: "consistency-asc", label: "Меньше всего единства стиля", query: { sort: "visual_consistency", order: "asc" } },
  { value: "outdated-desc", label: "Самый устаревший визуал", query: { sort: "outdated_style", order: "desc" } },
  { value: "subs-desc", label: "Подписчики ↓", query: { sort: "subscribers", order: "desc" } },
  { value: "analyzed-desc", label: "Сначала новые аудиты", query: { sort: "analyzed_at", order: "desc" } },
];

const isConfirmationError = (e: unknown) => e instanceof ApiError && e.code === "confirmation_required";

/** Phase 3.6: comparative AI audit of the project's channels (ТЗ §17) — estimate, confirmed start, results. */
export function ChannelAudit({ project: p }: { project: Project }) {
  const qc = useQueryClient();
  const [perChannel, setPerChannel] = useState(8);
  const [detail, setDetail] = useState<"low" | "high">("low");
  const [estimated, setEstimated] = useState<ChannelAnalysisRequest | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const estimate = useChannelAnalysisEstimate(p.id);
  const start = useStartChannelAnalysis(p.id);
  const ai = useAIStatus();
  const runs = useJobs({ type: "channel_analysis", project_id: p.id, offset: 0 }, RUNS_SHOWN);

  // Refresh the results whenever a run of this project changes state (also shows partial progress).
  const runKey = (runs.data?.items ?? []).map((j) => `${j.id}:${j.status}:${j.progress_done}`).join(",");
  const previous = useRef(runKey);
  useEffect(() => {
    if (previous.current && previous.current !== runKey) {
      void qc.invalidateQueries({ queryKey: qk.channelAnalyses(p.id) });
      void qc.invalidateQueries({ queryKey: qk.channels });
    }
    previous.current = runKey;
  }, [runKey, p.id, qc]);

  const runEstimate = (req: ChannelAnalysisRequest) => {
    setEstimated(req);
    estimate.mutate(req);
  };
  const confirmAndStart = () => {
    if (!estimate.data || !estimated) return;
    setNotice(null);
    start.mutate(
      { ...estimated, confirm_token: estimate.data.confirm_token },
      {
        onError: (err) => {
          if (!isConfirmationError(err)) return;
          setNotice("Каналы, модель или цена изменились после оценки. Проверьте новую оценку и подтвердите снова.");
          runEstimate(estimated);
        },
      },
    );
  };

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">Аудит каналов: сравнение превью</h2>
          <p className="mt-0.5 text-xs text-zinc-500">
            AI смотрит последние превью канала вместе: повторяющийся шаблон, устаревший визуал, единый стиль, частые
            проблемы, самые слабые видео. Нужны минимум 3 превью канала с AI-аудитом (блок выше).
          </p>
        </div>
        <AIProviderBadge provider={ai.data?.provider ?? null} loading={ai.isPending} />
      </div>
      <form
        className="mt-3 flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          setNotice(null);
          start.reset();
          runEstimate({ videos_per_channel: perChannel, detail });
        }}
      >
        <div>
          <Label htmlFor="ca-per-channel">Превью на канал (новейшие)</Label>
          <Select id="ca-per-channel" value={perChannel} onChange={(e) => setPerChannel(Number(e.target.value))}>
            {PER_CHANNEL.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor="ca-detail">Детализация изображений</Label>
          <Select id="ca-detail" value={detail} onChange={(e) => setDetail(e.target.value as "low" | "high")}>
            <option value="low">low — дешевле</option>
            <option value="high">high — точнее, дороже</option>
          </Select>
        </div>
        <Button type="submit" disabled={estimate.isPending}>
          <Calculator className="size-4" aria-hidden /> {estimate.isPending ? "Считаем…" : "Оценить каналы и стоимость"}
        </Button>
      </form>
      <InlineError error={estimate.error} />
      {estimate.data ? (
        <EstimateView
          e={estimate.data}
          archived={p.status === "archived"}
          starting={start.isPending || estimate.isPending}
          started={start.data}
          notice={notice}
          startError={isConfirmationError(start.error) ? null : start.error}
          onStart={confirmAndStart}
        />
      ) : null}
      <Runs runs={runs.data?.items ?? []} />
      <Results projectId={p.id} />
    </Card>
  );
}

function EstimateView({
  e,
  archived,
  starting,
  started,
  notice,
  startError,
  onStart,
}: {
  e: ChannelAnalysisEstimate;
  archived: boolean;
  starting: boolean;
  started: AnalysisStartResult | undefined;
  notice: string | null;
  startError: unknown;
  onStart: () => void;
}) {
  const pv = e.preview;
  const canStart = e.model_key !== null && e.items > 0 && !archived && !starting;
  return (
    <div role="status" className="mt-3 space-y-2 rounded-md bg-zinc-50 p-3 text-sm dark:bg-zinc-800/60">
      <p>
        Каналов в проекте: <b>{formatCount(pv.channels_in_project)}</b> · готовы к аудиту{" "}
        <b>{formatCount(pv.channels_ready)}</b>
        {pv.channels_not_enough > 0 ? <> · мало аудитов превью (&lt; {pv.min_thumbnails}): {formatCount(pv.channels_not_enough)}</> : null}
        {pv.channels_without_audits > 0 ? <> · без аудитов превью: {formatCount(pv.channels_without_audits)}</> : null}
      </p>
      <p>
        К аудиту: <b>{formatCount(e.items)}</b> каналов, превью в запросах: {formatCount(e.images)}
        {e.already_analyzed > 0 ? (
          <> · уже проверены на тех же превью: {formatCount(e.already_analyzed)} — бесплатно, повторно не отправляются</>
        ) : null}
        .
      </p>
      {e.model_key ? (
        <p>
          Модель: <code>{e.model_key}</code> ({e.api_model_id}, detail={e.detail}) · за канал ≈{" "}
          <b>{formatUsd(e.cost_per_item_usd)}</b> · всего <b>{formatUsd(e.estimated_cost_usd)}</b>
          {e.pricing_verified ? null : (
            <Badge tone="amber" title="Цены модели не заданы/не проверены в реестре (Q-004)">
              цена не проверена
            </Badge>
          )}
        </p>
      ) : (
        <p className="text-amber-700 dark:text-amber-400">
          AI-провайдер не настроен: задайте <code>YTL_VIBECODE_API_KEY</code> (или <code>YTL_AI_PROVIDER=mock</code>{" "}
          для демо).
        </p>
      )}
      <p className="text-xs text-zinc-500">
        Оценка токенов: вход ≈ {formatCount(e.estimated_input_tokens)}, выход ≈ {formatCount(e.estimated_output_tokens)}.
        Фактическая стоимость считается по ответам API.
      </p>
      {e.budgets.length === 0 ? (
        <p className="text-xs text-zinc-500">Лимиты бюджета не заданы — запуск только после вашего подтверждения.</p>
      ) : (
        <ul className="text-xs">
          {e.budgets.map((b) => (
            <li key={b.budget_id} className={b.would_exceed ? "text-red-700 dark:text-red-400" : "text-zinc-600"}>
              {b.description}: потрачено {formatUsd(b.spent_usd)}
              {b.limit_usd !== null ? ` из ${formatUsd(b.limit_usd)}` : ""}, операций {b.operations}
              {b.max_ai_operations !== null ? ` из ${b.max_ai_operations}` : ""}
              {b.would_exceed ? ` — превысит: ${b.reason}` : ""}
            </li>
          ))}
        </ul>
      )}
      {notice ? (
        <p role="alert" className="text-xs text-amber-700 dark:text-amber-400">
          {notice}
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-2 border-t border-zinc-200 pt-2 dark:border-zinc-700">
        <Button size="sm" disabled={!canStart} onClick={onStart}>
          <Play className="size-3.5" aria-hidden />
          {starting
            ? "Запуск…"
            : e.items === 0
              ? "Новых каналов для аудита нет"
              : `Подтвердить и запустить: ${formatCount(e.items)} каналов · ${formatUsd(e.estimated_cost_usd)}`}
        </Button>
        <span className="text-xs text-zinc-500">
          Запускается ровно эта оценка (код <code>{e.confirm_token.slice(0, 8)}</code>){" · "}
          <Link href="/jobs?type=channel_analysis" className="underline">
            Задачи
          </Link>
        </span>
      </div>
      {started ? (
        <p role="status" className="text-xs text-zinc-600 dark:text-zinc-300">
          {started.job
            ? `${started.created ? "Задача поставлена" : "Такая задача уже идёт"}: ${formatCount(started.items)} каналов. Результаты появятся ниже.`
            : "Все готовые каналы уже проверены на этих превью — результаты ниже."}
        </p>
      ) : null}
      <InlineError error={startError} />
    </div>
  );
}

function Runs({ runs }: { runs: Job[] }) {
  if (runs.length === 0) return null;
  return (
    <ul className="mt-3 divide-y divide-zinc-100 border-y border-zinc-100 text-xs dark:divide-zinc-800 dark:border-zinc-800">
      {runs.map((j) => {
        const r = (j.result ?? {}) as Partial<Record<string, number>>;
        return (
          <li key={j.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
            <JobStatusBadge status={j.status} />
            <span className="tabular-nums text-zinc-500">
              {formatDateTime(j.created_at)} · каналы {j.progress_done}/{j.progress_total}
            </span>
            {j.status === "completed" ? (
              <span>
                новых {formatCount(r.analyzed)} · из кэша {formatCount(r.cached)}
                {r.not_enough ? <> · мало превью {r.not_enough}</> : null}
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

function Results({ projectId }: { projectId: number }) {
  const [sortKey, setSortKey] = useState(SORTS[0].value);
  const [opportunity, setOpportunity] = useState<Level | "">("");
  const [problem, setProblem] = useState<ProblemCode | "">("");
  const [offset, setOffset] = useState(0);
  const list = useChannelAnalyses(projectId, {
    ...SORTS.find((s) => s.value === sortKey)!.query,
    opportunity: opportunity || undefined,
    problem: problem || undefined,
    offset,
  });
  const resetPage = <T,>(set: (v: T) => void) => (v: T) => {
    set(v);
    setOffset(0);
  };
  const d = list.data;
  const s = d?.summary;
  return (
    <div className="mt-3">
      {s && s.channels_analyzed > 0 ? (
        <div className="mb-3 flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
          <span>
            Каналов с аудитом: <b>{formatCount(s.channels_analyzed)}</b>
          </span>
          <span className="flex items-center gap-1">
            Потенциал:
            {(["high", "medium", "low"] as const).map((l) => (
              <Badge key={l} tone={opportunityTone(l)}>
                {LEVEL_LABELS[l]} {formatCount(s.opportunity[l] ?? 0)}
              </Badge>
            ))}
          </span>
          {s.mock_results > 0 ? <Badge tone="violet">mock: {formatCount(s.mock_results)}</Badge> : null}
        </div>
      ) : null}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div>
          <Label htmlFor="ca-sort">Сортировка</Label>
          <Select id="ca-sort" value={sortKey} onChange={(e) => resetPage(setSortKey)(e.target.value)}>
            {SORTS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor="ca-opportunity">Потенциал редизайна</Label>
          <Select id="ca-opportunity" value={opportunity} onChange={(e) => resetPage(setOpportunity)(e.target.value as Level | "")}>
            <option value="">любой</option>
            {(["high", "medium", "low"] as const).map((l) => (
              <option key={l} value={l}>
                {LEVEL_LABELS[l]}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor="ca-problem">Повторяющаяся проблема</Label>
          <Select id="ca-problem" value={problem} onChange={(e) => resetPage(setProblem)(e.target.value as ProblemCode | "")}>
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
            title={s && s.channels_analyzed > 0 ? "Нет каналов под выбранные фильтры" : "Аудитов каналов пока нет"}
            hint={
              s && s.channels_analyzed > 0
                ? "Сбросьте фильтры."
                : "Сначала AI-аудит превью, затем оценка и запуск аудита каналов выше."
            }
          />
        ) : (
          <>
            <ul className="space-y-2">
              {d!.items.map((item) => (
                <ChannelRow key={item.channel_id} item={item} />
              ))}
            </ul>
            <Pager offset={offset} limit={CHANNEL_ANALYSES_PAGE} total={d!.total} onChange={setOffset} />
          </>
        )}
      </div>
    </div>
  );
}

function ChannelRow({ item }: { item: AnalyzedChannel }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="rounded-md border border-zinc-200 dark:border-zinc-800">
      <div className="flex items-start gap-2 p-2">
        <button
          type="button"
          aria-expanded={open}
          aria-label={open ? `Свернуть аудит «${item.title}»` : `Развернуть аудит «${item.title}»`}
          onClick={() => setOpen((o) => !o)}
          className="mt-0.5 rounded p-0.5 text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800"
        >
          {open ? <ChevronDown className="size-4" aria-hidden /> : <ChevronRight className="size-4" aria-hidden />}
        </button>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">
            <Link href={`/channels/${item.channel_id}`} className="hover:underline">
              {item.title}
            </Link>{" "}
            <span className="text-xs font-normal text-zinc-500">{formatCount(item.subscriber_count)} подписчиков</span>
          </p>
          <div className="mt-1.5">
            <ChannelAuditHeadline analysis={item.analysis} aggregates={item.aggregates} />
          </div>
        </div>
      </div>
      {open ? (
        <div className="border-t border-zinc-200 p-3 dark:border-zinc-800">
          <ChannelAuditDetails analysis={item.analysis} />
        </div>
      ) : null}
    </li>
  );
}
