"use client";

import Link from "next/link";

import { AnalyticsFilters, BestLine } from "@/components/analytics";
import { ErrorState, LoadingState } from "@/components/states";
import { Card, PageHeader } from "@/components/ui";
import { analyticsQueryFromParams, formatUnitCost, funnelWidth, type PeriodKey } from "@/lib/analytics";
import { useOverview, type Overview } from "@/lib/api/analytics-hooks";
import { formatRate } from "@/lib/campaigns";
import { formatCount } from "@/lib/format";
import { formatUsd } from "@/lib/prefilter";
import { useUrlState } from "@/lib/url-state";

function Tile({ label, value, hint, href }: { label: string; value: string; hint?: string; href?: string }) {
  const body = (
    <>
      <p className="text-xs text-zinc-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
      {hint ? <p className="mt-0.5 text-xs text-zinc-500">{hint}</p> : null}
    </>
  );
  const cls = "block rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900";
  return href ? (
    <Link href={href} className={`${cls} hover:border-zinc-400 dark:hover:border-zinc-600`}>
      {body}
    </Link>
  ) : (
    <div className={cls}>{body}</div>
  );
}

function Tiles({ o }: { o: Overview }) {
  const t = o.tiles;
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
      <Tile label="Каналов всего" value={formatCount(t.total_channels)} href="/leads" />
      <Tile label="Новых за период" value={formatCount(t.new_channels)} />
      <Tile label="Проанализировано" value={formatCount(t.analyzed)} />
      <Tile label="Выбрано" value={formatCount(t.selected)} hint="дошли до выбора" />
      <Tile label="Образцов превью" value={formatCount(t.generated_samples)} hint="лидов с образцом" />
      <Tile label="Ждут подтверждения" value={formatCount(t.offers_pending_approval)} hint="сейчас" href="/approvals" />
      <Tile label="Отправлено" value={formatCount(t.sent)} href="/outreach" />
      <Tile label="Ответов" value={formatCount(t.replies)} hint={`доля ${formatRate(t.reply_rate)}`} />
      <Tile label="Положительных" value={formatCount(t.positive_replies)} />
      <Tile label="Конверсия" value={formatRate(t.conversion)} hint="положительные / отправлено" />
    </div>
  );
}

function Funnel({ o }: { o: Overview }) {
  const start = o.funnel[0]?.leads ?? 0;
  return (
    <Card>
      <h2 className="mb-2 text-sm font-semibold">Воронка лидов</h2>
      {start === 0 ? (
        <p className="text-sm text-zinc-500">За период лидов не найдено.</p>
      ) : (
        <ol className="space-y-1.5" aria-label="Воронка">
          {o.funnel.map((s) => (
            <li key={s.key} className="grid grid-cols-[9rem_1fr_6rem] items-center gap-2 text-sm">
              <span className="truncate">{s.label}</span>
              <span className="h-4 rounded bg-zinc-100 dark:bg-zinc-800">
                <span
                  className="block h-4 rounded bg-blue-500/70"
                  style={{ width: `${funnelWidth(s.leads, start)}%` }}
                  aria-hidden
                />
              </span>
              <span className="text-right tabular-nums">
                {formatCount(s.leads)}
                {s.rate_from_previous !== null ? (
                  <span className="ml-1 text-xs text-zinc-500" title="От предыдущего шага">
                    {formatRate(s.rate_from_previous)}
                  </span>
                ) : null}
              </span>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

function Costs({ o }: { o: Overview }) {
  const c = o.costs;
  const rows: [string, string][] = [
    ["Анализ (превью, каналы)", formatUsd(c.analysis_usd)],
    ["Генерация превью", formatUsd(c.generation_usd)],
    ["Тексты офферов", formatUsd(c.offers_usd)],
    ...(Number(c.other_usd) > 0 ? ([["Прочее", formatUsd(c.other_usd)]] as [string, string][]) : []),
  ];
  return (
    <Card>
      <div className="mb-2 flex items-baseline justify-between">
        <h2 className="text-sm font-semibold">Расходы на AI</h2>
        <Link href="/analytics?tab=costs" className="text-xs text-blue-600 hover:underline dark:text-blue-400">
          Подробнее
        </Link>
      </div>
      <p className="text-2xl font-semibold tabular-nums">{formatUsd(c.total_usd)}</p>
      <p className="mb-2 text-xs text-zinc-500">{formatCount(c.calls)} вызовов</p>
      <dl className="space-y-1 text-sm">
        {rows.map(([k, v]) => (
          <div key={k} className="flex justify-between">
            <dt className="text-zinc-500">{k}</dt>
            <dd className="tabular-nums">{v}</dd>
          </div>
        ))}
        <div className="flex justify-between border-t border-zinc-100 pt-1 dark:border-zinc-800">
          <dt className="text-zinc-500">На найденный лид</dt>
          <dd className="tabular-nums">{formatUnitCost(c.per_lead_usd)}</dd>
        </div>
        <div className="flex justify-between">
          <dt className="text-zinc-500">На отправленное письмо</dt>
          <dd className="tabular-nums">{formatUnitCost(c.per_sent_usd)}</dd>
        </div>
        <div className="flex justify-between">
          <dt className="text-zinc-500">На ответ</dt>
          <dd className="tabular-nums">{formatUnitCost(c.per_reply_usd)}</dd>
        </div>
      </dl>
    </Card>
  );
}

function Bests({ o }: { o: Overview }) {
  return (
    <Card>
      <div className="mb-1 flex items-baseline justify-between">
        <h2 className="text-sm font-semibold">Что работает лучше</h2>
        <Link href="/analytics" className="text-xs text-blue-600 hover:underline dark:text-blue-400">
          Аналитика
        </Link>
      </div>
      <BestLine title="Ниша" best={o.best_niche} minSample={o.min_sample} />
      <BestLine title="Диапазон балла превью" best={o.best_score_range} minSample={o.min_sample} />
      <BestLine title="Кампания" best={o.best_campaign} minSample={o.min_sample} />
      <BestLine title="Вариант оффера" best={o.best_offer_variant} minSample={o.min_sample} />
    </Card>
  );
}

/** ТЗ §39: the main dashboard. */
export function DashboardView() {
  const [params, setParams] = useUrlState();
  const { period, query } = analyticsQueryFromParams(params);
  const overview = useOverview(query);
  const onChange = (patch: { period?: PeriodKey; project?: number | null }) => setParams(patch);

  return (
    <div className="max-w-6xl">
      <PageHeader
        title="Дашборд"
        subtitle="Лиды, найденные за период, и что с ними стало; расходы на AI за тот же период"
        actions={<AnalyticsFilters period={period} projectId={query.project_id} onChange={onChange} />}
      />
      {overview.isPending ? (
        <LoadingState />
      ) : overview.isError ? (
        <ErrorState error={overview.error} onRetry={() => void overview.refetch()} />
      ) : (
        <div className="space-y-4">
          <Tiles o={overview.data} />
          <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
            <Funnel o={overview.data} />
            <Costs o={overview.data} />
          </div>
          <Bests o={overview.data} />
          <ul className="list-disc space-y-0.5 pl-5 text-xs text-zinc-500">
            {overview.data.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
