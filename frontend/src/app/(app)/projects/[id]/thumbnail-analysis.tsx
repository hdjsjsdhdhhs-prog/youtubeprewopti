"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Calculator, Download, Play, Save } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { InlineError } from "@/components/states";
import { JobStatusBadge } from "@/components/status-badges";
import { Badge, Button, Card, Input, Label, Select } from "@/components/ui";
import { ApiError } from "@/lib/api/client";
import {
  ACTIVE_JOB_STATUSES,
  qk,
  useAIStatus,
  useAnalysisEstimate,
  useIngestProjectThumbnails,
  useJobs,
  useStartThumbnailAnalysis,
  useThumbnailStats,
  useUpdateProject,
  type AnalysisEstimate,
  type AnalysisEstimateRequest,
  type AnalysisStartResult,
  type Project,
} from "@/lib/api/hooks";
import { formatCount, formatDateTime } from "@/lib/format";
import { METRIC_FIELDS, formatUsd, formFromPrefilter, prefilterFromForm, type PrefilterForm } from "@/lib/prefilter";
import { cn } from "@/lib/utils";

const RUNS_SHOWN = 3;

/** Phase 3.1–3.2: download the project's thumbnails and compute their deterministic metrics (free). */
export function ThumbnailIngestion({ project: p }: { project: Project }) {
  const qc = useQueryClient();
  const stats = useThumbnailStats(p.id);
  const ingest = useIngestProjectThumbnails(p.id);
  const runs = useJobs({ type: "thumbnail_download", project_id: p.id, offset: 0 }, RUNS_SHOWN);

  // Refresh the counters whenever a run of this project finishes.
  const activeKey = (runs.data?.items ?? [])
    .filter((j) => ACTIVE_JOB_STATUSES.has(j.status))
    .map((j) => j.id)
    .join(",");
  const previous = useRef(activeKey);
  useEffect(() => {
    if (previous.current && previous.current !== activeKey) {
      void qc.invalidateQueries({ queryKey: qk.thumbnailStats(p.id) });
      void qc.invalidateQueries({ queryKey: qk.channels });
    }
    previous.current = activeKey;
  }, [activeKey, p.id, qc]);

  const s = stats.data;
  const todo = s ? s.videos - s.with_metrics : 0;
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">Превью и объективные метрики</h2>
          <p className="mt-0.5 text-xs text-zinc-500">
            Загрузка превью с i.ytimg.com (квота YouTube не расходуется) и расчёт метрик без AI: яркость, контраст,
            цвет, резкость, детализация, композиция.
          </p>
        </div>
        <Button
          variant="secondary"
          disabled={p.status === "archived" || ingest.isPending || (s !== undefined && todo === 0)}
          onClick={() => ingest.mutate()}
        >
          <Download className="size-4" aria-hidden /> {ingest.isPending ? "Запуск…" : "Загрузить и посчитать"}
        </Button>
      </div>
      {s ? (
        <dl className="mt-3 grid grid-cols-3 gap-x-6 gap-y-2 text-sm sm:grid-cols-5">
          {(
            [
              ["Видео", s.videos],
              ["Загружено", s.downloaded],
              ["С метриками", s.with_metrics],
              ["Ожидают", s.pending],
              ["Ошибки", s.failed],
            ] as const
          ).map(([k, v]) => (
            <div key={k}>
              <dt className="text-xs text-zinc-500">{k}</dt>
              <dd className={cn("tabular-nums", k === "Ошибки" && v > 0 && "text-red-600")}>{formatCount(v)}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      <InlineError error={stats.error ?? ingest.error} />
      {ingest.data ? (
        <p role="status" className="mt-2 text-xs text-zinc-600">
          {ingest.data.job
            ? `${ingest.data.created ? "Задача поставлена" : "Такая задача уже идёт"}: ${formatCount(ingest.data.items)} превью.`
            : "Все превью уже загружены и посчитаны."}
          {ingest.data.remaining > 0
            ? ` Ещё ${formatCount(ingest.data.remaining)} — запустите снова после завершения задачи.`
            : null}
        </p>
      ) : null}
      {(runs.data?.items ?? []).length > 0 ? (
        <ul className="mt-3 divide-y divide-zinc-100 border-t border-zinc-100 text-xs dark:divide-zinc-800 dark:border-zinc-800">
          {runs.data!.items.map((j) => {
            const r = j.result as Partial<Record<string, number>>;
            return (
              <li key={j.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
                <JobStatusBadge status={j.status} />
                <span className="tabular-nums text-zinc-500">
                  {formatDateTime(j.created_at)} · {j.progress_done}/{j.progress_total}
                </span>
                {j.status === "completed" ? (
                  <span>
                    загружено {formatCount(r.downloaded)} · метрик {formatCount(r.metrics_computed)}
                    {r.failed ? <span className="text-red-600"> · ошибок {r.failed}</span> : null}
                  </span>
                ) : null}
                {j.error_human ? <span className="text-red-700 dark:text-red-400">{j.error_human}</span> : null}
              </li>
            );
          })}
        </ul>
      ) : null}
    </Card>
  );
}

const isConfirmationError = (e: unknown) => e instanceof ApiError && e.code === "confirmation_required";

/** Phase 3.2–3.5: which thumbnails go to the AI audit, what it would cost (budget gate) and the confirmed start. */
export function AnalysisPrefilter({ project: p }: { project: Project }) {
  const [form, setForm] = useState<PrefilterForm>(() => formFromPrefilter(p.prefilter_settings));
  const [detail, setDetail] = useState<"low" | "high">("low");
  const [formErrors, setFormErrors] = useState<string[]>([]);
  // The request of the estimate on screen: the start must confirm exactly that one, not later form edits.
  const [estimated, setEstimated] = useState<AnalysisEstimateRequest | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const estimate = useAnalysisEstimate(p.id);
  const start = useStartThumbnailAnalysis(p.id);
  const save = useUpdateProject(p.id);
  const ai = useAIStatus();
  const hasChannelFilters = Object.keys(p.filter_settings ?? {}).length > 0;

  const runEstimate = (req: AnalysisEstimateRequest) => {
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
          setNotice("Отбор, модель или цена изменились после оценки. Проверьте новую оценку и подтвердите снова.");
          runEstimate(estimated);
        },
      },
    );
  };

  const build = () => {
    const { value, errors } = prefilterFromForm(form);
    setFormErrors(errors);
    return errors.length ? null : value;
  };
  const set = <K extends keyof PrefilterForm>(key: K, value: PrefilterForm[K]) => setForm((f) => ({ ...f, [key]: value }));

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">Отбор превью для AI-анализа</h2>
          <p className="mt-0.5 text-xs text-zinc-500">
            AI-аудит платный, поэтому сначала отбор по объективным метрикам. Пустые поля — без ограничения.
          </p>
        </div>
        <AIProviderBadge provider={ai.data?.provider ?? null} loading={ai.isPending} />
      </div>
      <form
        className="mt-3 space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          const prefilter = build();
          if (prefilter) {
            setNotice(null);
            start.reset();
            runEstimate({ prefilter, detail });
          }
        }}
      >
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <NumberField id="pf-per-channel" label="Видео на канал (новейшие)" value={form.videos_per_channel}
            onChange={(v) => set("videos_per_channel", v)} />
          <NumberField id="pf-age" label="Не старше (дней)" value={form.max_video_age_days}
            onChange={(v) => set("max_video_age_days", v)} />
          <NumberField id="pf-views" label="Минимум просмотров" value={form.min_video_views}
            onChange={(v) => set("min_video_views", v)} />
          <div>
            <Label htmlFor="pf-detail">Детализация изображения</Label>
            <Select id="pf-detail" value={detail} onChange={(e) => setDetail(e.target.value as "low" | "high")}>
              <option value="low">low — дешевле, для массового</option>
              <option value="high">high — точнее, дороже</option>
            </Select>
          </div>
        </div>
        <div className="flex flex-wrap gap-x-5 gap-y-1 text-sm">
          <label className="flex items-center gap-1.5">
            <input type="checkbox" checked={form.exclude_shorts} onChange={(e) => set("exclude_shorts", e.target.checked)} />
            без Shorts
          </label>
          <label className="flex items-center gap-1.5" title={hasChannelFilters ? undefined : "У проекта нет сохранённых фильтров каналов"}>
            <input type="checkbox" checked={form.apply_channel_filters}
              onChange={(e) => set("apply_channel_filters", e.target.checked)} />
            учитывать сохранённые фильтры каналов проекта
            {hasChannelFilters ? null : <span className="text-xs text-zinc-500">(не заданы)</span>}
          </label>
        </div>
        <fieldset>
          <legend className="mb-1 text-xs font-medium text-zinc-600 dark:text-zinc-400">Диапазоны метрик превью</legend>
          <div className="grid gap-x-4 gap-y-1.5 sm:grid-cols-2 lg:grid-cols-3">
            {METRIC_FIELDS.map((m) => (
              <div key={m.key} className="flex items-center gap-1.5 text-sm" title={m.hint}>
                <span className="w-36 shrink-0 truncate">{m.label}</span>
                <Input aria-label={`${m.label}: от`} placeholder="от" inputMode="decimal" className="h-8"
                  value={form.metrics[m.key].min}
                  onChange={(e) => set("metrics", { ...form.metrics, [m.key]: { ...form.metrics[m.key], min: e.target.value } })} />
                <Input aria-label={`${m.label}: до`} placeholder="до" inputMode="decimal" className="h-8"
                  value={form.metrics[m.key].max}
                  onChange={(e) => set("metrics", { ...form.metrics, [m.key]: { ...form.metrics[m.key], max: e.target.value } })} />
              </div>
            ))}
          </div>
        </fieldset>
        {formErrors.length > 0 ? (
          <ul role="alert" className="list-inside list-disc text-xs text-red-600">
            {formErrors.map((e) => (
              <li key={e}>{e}</li>
            ))}
          </ul>
        ) : null}
        <div className="flex flex-wrap gap-2">
          <Button type="submit" disabled={estimate.isPending}>
            <Calculator className="size-4" aria-hidden /> {estimate.isPending ? "Считаем…" : "Оценить отбор и стоимость"}
          </Button>
          <Button
            variant="secondary"
            disabled={save.isPending || p.status === "archived"}
            onClick={() => {
              const prefilter = build();
              if (prefilter) save.mutate({ prefilter_settings: prefilter });
            }}
          >
            <Save className="size-4" aria-hidden /> Сохранить отбор
          </Button>
          {save.isSuccess ? <span role="status" className="self-center text-xs text-zinc-500">Сохранено</span> : null}
        </div>
        <InlineError error={estimate.error ?? save.error} />
      </form>
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
    </Card>
  );
}

function NumberField({ id, label, value, onChange }: { id: string; label: string; value: string; onChange: (v: string) => void }) {
  return (
    <div>
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} inputMode="numeric" value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

function AIProviderBadge({ provider, loading }: { provider: string | null; loading: boolean }) {
  if (loading) return null;
  if (provider === "mock") return <Badge tone="violet">AI: mock — демо-результаты</Badge>;
  if (provider === "openai") return <Badge tone="blue">AI: OpenAI</Badge>;
  if (provider === "vibecode") return <Badge tone="blue">AI: vibecode.moe</Badge>;
  return <Badge tone="amber">AI не настроен</Badge>;
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
  e: AnalysisEstimate;
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
        Каналов: <b>{formatCount(pv.channels_matched)}</b> из {formatCount(pv.channels_in_project)} · видео рассмотрено{" "}
        <b>{formatCount(pv.videos_considered)}</b> · отобрано превью <b>{formatCount(pv.thumbnails_selected)}</b>
        {pv.excluded_by_metrics ? <> · отсеяно по метрикам {formatCount(pv.excluded_by_metrics)}</> : null}
      </p>
      {pv.thumbnails_not_ready > 0 ? (
        <p className="text-amber-700 dark:text-amber-400">
          {formatCount(pv.thumbnails_not_ready)} превью ещё не загружены или без метрик — они не попадут в анализ. Запустите
          «Загрузить и посчитать».
        </p>
      ) : null}
      <p>
        К анализу: <b>{formatCount(e.items)}</b> уникальных изображений (одинаковые превью анализируются один раз)
        {e.already_analyzed > 0 ? (
          <>
            {" "}
            · уже проанализировано этой моделью и версией промпта: {formatCount(e.already_analyzed)} — бесплатно, повторно не
            отправляются
          </>
        ) : null}
        .
      </p>
      {e.model_key ? (
        <p>
          Модель: <code>{e.model_key}</code> ({e.api_model_id}, detail={e.detail}) · за превью{" "}
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
          для демо). Отбор уже посчитан.
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
      {!e.within_budgets && e.items > 0 ? (
        <p className="text-xs text-amber-700 dark:text-amber-400">
          Оценка превышает лимит бюджета: задача остановится, когда лимит будет достигнут. Уже проанализированные превью
          сохранятся.
        </p>
      ) : null}
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
              ? "Новых превью для анализа нет"
              : `Подтвердить и запустить: ${formatCount(e.items)} превью · ${formatUsd(e.estimated_cost_usd)}`}
        </Button>
        <span className="text-xs text-zinc-500">
          Запускается ровно эта оценка (код <code>{e.confirm_token.slice(0, 8)}</code>); бюджеты проверяются перед каждым
          вызовом AI{" · "}
          <Link href="/jobs" className="underline">
            Задачи
          </Link>
        </span>
      </div>
      {started ? (
        <p role="status" className="text-xs text-zinc-600 dark:text-zinc-300">
          {started.job
            ? `${started.created ? "Задача поставлена" : "Такая задача уже идёт"}: ${formatCount(started.items)} превью. Результаты появятся ниже по мере анализа.`
            : "Все отобранные превью уже проанализированы — результаты ниже."}
        </p>
      ) : null}
      <InlineError error={startError} />
    </div>
  );
}
