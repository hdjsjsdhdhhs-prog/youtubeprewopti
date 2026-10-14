"use client";

import { ImageOff } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui";
import {
  LEVEL_LABELS,
  bandWidths,
  formatPercent,
  formatScore,
  inverseScoreTone,
  opportunityTone,
  problemLabel,
  scoreTone,
} from "@/lib/analysis";
import type { ChannelAggregates, ChannelAnalysis, ChannelVisualReport } from "@/lib/api/hooks";
import { formatCount, formatDate, formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

type ReportVideo = ChannelVisualReport["videos"][number];

/** Headline of a channel audit (ТЗ §17): AI judgements + code-computed aggregates over the compared thumbnails. */
export function ChannelAuditHeadline({ analysis: a, aggregates: g }: { analysis: ChannelAnalysis; aggregates: ChannelAggregates }) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge tone={opportunityTone(a.visual_opportunity)} title="Вероятный визуальный потенциал редизайна канала">
          потенциал: {LEVEL_LABELS[a.visual_opportunity]}
        </Badge>
        <Badge tone={scoreTone(a.visual_consistency)} title="Единство стиля превью канала (AI), 1–10">
          единство стиля {formatScore(a.visual_consistency)}
        </Badge>
        <Badge tone={inverseScoreTone(a.outdated_style_score)} title="Устаревший визуал (AI), 1–10: чем выше, тем старее">
          устаревание {formatScore(a.outdated_style_score)}
        </Badge>
        {a.repeated_template ? <Badge tone="neutral">один шаблон</Badge> : null}
        <Badge tone={a.has_design_system ? "green" : "amber"}>
          {a.has_design_system ? "есть дизайн-система" : "нет дизайн-системы"}
        </Badge>
        {a.provider === "mock" ? <Badge tone="violet">mock</Badge> : null}
      </div>
      <Aggregates g={g} />
    </div>
  );
}

function Aggregates({ g }: { g: ChannelAggregates }) {
  if (g.thumbnails_analyzed === 0) {
    return <p className="text-xs text-zinc-500">У сравнённых видео больше нет текущих аудитов превью.</p>;
  }
  const [weak, medium, strong] = bandWidths(g);
  return (
    <div className="text-xs">
      <p className="text-zinc-600 dark:text-zinc-300">
        Средний балл превью <b className="tabular-nums">{formatScore(g.avg_score)}</b> · медиана{" "}
        <b className="tabular-nums">{formatScore(g.median_score)}</b> · превью: {formatCount(g.thumbnails_analyzed)}{" "}
        <span className="text-zinc-500">(считает код по весам профиля)</span>
      </p>
      <div
        className="mt-1 flex h-2 max-w-md overflow-hidden rounded bg-zinc-100 dark:bg-zinc-800"
        role="img"
        aria-label={`Слабые ${formatPercent(g.pct_weak)}, средние ${formatPercent(g.pct_medium)}, сильные ${formatPercent(g.pct_strong)}`}
      >
        <span className="bg-red-500" style={{ width: `${weak}%` }} />
        <span className="bg-amber-500" style={{ width: `${medium}%` }} />
        <span className="bg-emerald-500" style={{ width: `${strong}%` }} />
      </div>
      <p className="mt-1 text-zinc-500">
        слабые (&lt; 5) {formatPercent(g.pct_weak)} · средние {formatPercent(g.pct_medium)} · сильные (≥ 7){" "}
        {formatPercent(g.pct_strong)}
      </p>
    </div>
  );
}

/** Full channel audit: summary, recurring problems, patterns, weakest videos and redesign candidates. */
export function ChannelAuditDetails({ analysis: a, videos = [] }: { analysis: ChannelAnalysis; videos?: ReportVideo[] }) {
  const byId = new Map(videos.map((v) => [v.video_id, v]));
  const name = (id: number) => byId.get(id)?.title ?? `видео #${id}`;
  return (
    <div className="grid gap-4 text-sm md:grid-cols-2">
      <div className="space-y-3">
        <p>{a.summary}</p>
        {a.recurring_problems.length > 0 ? (
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">Повторяющиеся проблемы</h3>
            <ul className="mt-1 space-y-1">
              {a.recurring_problems.map((p, i) => (
                <li key={`${p.code}-${i}`}>
                  <Badge tone={p.frequency === "high" ? "red" : p.frequency === "medium" ? "amber" : "neutral"}>
                    {problemLabel(p.code)}
                  </Badge>{" "}
                  <span className="text-xs text-zinc-500">{FREQUENCY_LABELS[p.frequency]}</span> {p.description}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {a.patterns.length > 0 ? (
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
              Что чаще встречается у видео с бо́льшими просмотрами
            </h3>
            <ul className="mt-1 list-inside list-disc space-y-1">
              {a.patterns.map((p, i) => (
                <li key={i}>
                  <b>{p.pattern}</b> <span className="text-zinc-500">— {p.evidence}</span>
                </li>
              ))}
            </ul>
            <p className="mt-1 text-xs text-zinc-500">Наблюдение на небольшой выборке, не причина и не прогноз.</p>
          </div>
        ) : null}
      </div>
      <div className="space-y-3">
        {a.redesign_candidates.length > 0 ? (
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">Лучшие кандидаты на редизайн</h3>
            <ul className="mt-1 space-y-1">
              {a.redesign_candidates.map((c) => (
                <li key={c.video_id}>
                  <b>{name(c.video_id)}</b> <span className="text-zinc-500">— {c.why}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {a.weakest_video_ids.length > 0 ? (
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">Самые слабые превью</h3>
            <ul className="mt-1 list-inside list-disc">
              {a.weakest_video_ids.map((id) => (
                <li key={id}>{name(id)}</li>
              ))}
            </ul>
          </div>
        ) : null}
        <p className="text-xs text-zinc-500">
          Сравнено превью: {a.video_ids.length} · {a.provider} · <code>{a.model_key}</code> · detail={a.detail} ·{" "}
          {formatDateTime(a.created_at)}
        </p>
      </div>
    </div>
  );
}

const FREQUENCY_LABELS: Record<"low" | "medium" | "high", string> = {
  low: "у некоторых",
  medium: "примерно у половины",
  high: "у большинства",
};

/** The compared thumbnails in the order the model saw them, with their current overall score. */
export function ComparedThumbnails({ videos, analysis }: { videos: ReportVideo[]; analysis: ChannelAnalysis }) {
  const weakest = new Set(analysis.weakest_video_ids);
  const candidates = new Set(analysis.redesign_candidates.map((c) => c.video_id));
  return (
    <ul className="grid grid-cols-[repeat(auto-fill,minmax(10rem,1fr))] gap-3">
      {videos.map((v, i) => (
        <li key={v.video_id} className="min-w-0">
          <div className="relative">
            <Thumb assetId={v.image_asset_id} title={v.title} />
            <span className="absolute left-1 top-1 rounded bg-black/70 px-1 text-[11px] font-medium text-white">{i + 1}</span>
          </div>
          <p className="mt-1 line-clamp-2 text-xs font-medium" title={v.title}>
            {v.title}
          </p>
          <p className="mt-0.5 flex flex-wrap items-center gap-1 text-[11px] text-zinc-500">
            <span>
              {formatCount(v.view_count)} просм. · {formatDate(v.published_at)}
            </span>
            {v.overall_score !== null ? <Badge tone={scoreTone(v.overall_score)}>{formatScore(v.overall_score)}</Badge> : null}
            {weakest.has(v.video_id) ? <Badge tone="red">слабое</Badge> : null}
            {candidates.has(v.video_id) ? <Badge tone="green">кандидат</Badge> : null}
          </p>
        </li>
      ))}
    </ul>
  );
}

function Thumb({ assetId, title }: { assetId: number | null; title: string }) {
  const [broken, setBroken] = useState(false);
  return (
    <div className={cn("aspect-video overflow-hidden rounded bg-zinc-100 dark:bg-zinc-800")}>
      {assetId === null || broken ? (
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
