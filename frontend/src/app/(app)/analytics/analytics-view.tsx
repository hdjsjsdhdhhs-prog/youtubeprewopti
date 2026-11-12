"use client";

import Link from "next/link";
import type { ReactNode } from "react";

import { AnalyticsFilters, OutcomeCells, OutcomeHeader, Table } from "@/components/analytics";
import { ErrorState, LoadingState } from "@/components/states";
import { Badge, Card, PageHeader, Select } from "@/components/ui";
import {
  AB_DIMENSIONS,
  VERDICT_LABELS,
  analyticsQueryFromParams,
  formatUnitCost,
  taskLabel,
  verdictText,
  verdictTone,
  type ABDimension,
  type AnalyticsQuery,
  type PeriodKey,
} from "@/lib/analytics";
import { formatScore } from "@/lib/analysis";
import {
  useABReport,
  useAIFeedbackReport,
  useCampaignReport,
  useCostReport,
  useNicheReport,
  useScoreRangeReport,
} from "@/lib/api/analytics-hooks";
import { useCampaigns } from "@/lib/api/campaigns-hooks";
import { CAMPAIGN_STATUS_LABELS, formatRate } from "@/lib/campaigns";
import { formatCount } from "@/lib/format";
import { strategyLabel } from "@/lib/generation";
import { STYLE_LABELS, formatPersonalization } from "@/lib/offers";
import { formatUsd } from "@/lib/prefilter";
import { enumParam, intParam, useUrlState } from "@/lib/url-state";
import { cn } from "@/lib/utils";

const TABS = [
  { key: "niches", label: "Ниши" },
  { key: "scores", label: "Балл превью" },
  { key: "campaigns", label: "Кампании" },
  { key: "ab", label: "A/B" },
  { key: "costs", label: "Расходы" },
  { key: "feedback", label: "AI vs человек" },
] as const;
type Tab = (typeof TABS)[number]["key"];
const TAB_KEYS = TABS.map((t) => t.key);

function Section({
  title,
  note,
  query,
  children,
}: {
  title: string;
  note?: string;
  query: { isPending: boolean; isError: boolean; error: unknown; refetch: () => unknown };
  children: () => ReactNode;
}) {
  return (
    <Card>
      <h2 className="text-sm font-semibold">{title}</h2>
      {note ? <p className="mb-2 text-xs text-zinc-500">{note}</p> : null}
      {query.isPending ? (
        <LoadingState />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : (
        children()
      )}
    </Card>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return <p className="py-2 text-sm text-zinc-500">{children}</p>;
}

// --- niches (ТЗ §51) -------------------------------------------------------------------------------------------
function Niches({ q }: { q: AnalyticsQuery }) {
  const r = useNicheReport(q);
  return (
    <Section title="Какие ниши дают лучших клиентов" note={r.data?.note} query={r}>
      {() =>
        r.data!.items.length === 0 ? (
          <Empty>За период лидов нет.</Empty>
        ) : (
          <Table
            head={
              <>
                <th className="py-1 font-medium">Ниша</th>
                <th className="py-1 text-right font-medium">Лидов</th>
                <th className="py-1 text-right font-medium">Выбрано</th>
                <th className="py-1 text-right font-medium">Ср. балл превью</th>
                <th className="py-1 text-right font-medium" title="Средний балл превью ниже порога «weak-thumbnails»">
                  Слабые превью
                </th>
                <th className="py-1 text-right font-medium" title="Lead Score не ниже порога «high-potential»">
                  High-opportunity
                </th>
                <th className="py-1 text-right font-medium">С контактом</th>
                <OutcomeHeader />
              </>
            }
          >
            {r.data!.items.map((n) => (
              <tr key={n.niche_id ?? "none"}>
                <td className={cn("py-1.5", n.niche_id === null && "text-zinc-500")}>{n.name}</td>
                <td className="py-1.5 text-right tabular-nums">{formatCount(n.leads)}</td>
                <td className="py-1.5 text-right tabular-nums">{formatCount(n.selected)}</td>
                <td className="py-1.5 text-right tabular-nums">{formatScore(n.avg_thumbnail_score)}</td>
                <td className="py-1.5 text-right tabular-nums">{formatCount(n.weak_thumbnails)}</td>
                <td className="py-1.5 text-right tabular-nums">{formatCount(n.high_opportunity)}</td>
                <td className="py-1.5 text-right tabular-nums">{formatCount(n.with_contacts)}</td>
                <OutcomeCells outcomes={n.outcomes} />
              </tr>
            ))}
          </Table>
        )
      }
    </Section>
  );
}

// --- thumbnail score ranges (ТЗ §39) ---------------------------------------------------------------------------
function ScoreRanges({ q }: { q: AnalyticsQuery }) {
  const r = useScoreRangeReport(q);
  return (
    <Section title="Лиды по среднему баллу превью канала" note={r.data?.note} query={r}>
      {() => (
        <Table
          head={
            <>
              <th className="py-1 font-medium">Балл</th>
              <th className="py-1 text-right font-medium">Лидов</th>
              <th className="py-1 text-right font-medium">Выбрано</th>
              <th className="py-1 text-right font-medium">Доля выбранных</th>
              <OutcomeHeader />
            </>
          }
        >
          {r.data!.items.map((b) => (
            <tr key={b.key} className={cn(b.leads === 0 && "text-zinc-400")}>
              <td className="py-1.5">{b.label}</td>
              <td className="py-1.5 text-right tabular-nums">{formatCount(b.leads)}</td>
              <td className="py-1.5 text-right tabular-nums">{formatCount(b.selected)}</td>
              <td className="py-1.5 text-right tabular-nums">{formatRate(b.selection_rate)}</td>
              <OutcomeCells outcomes={b.outcomes} />
            </tr>
          ))}
        </Table>
      )}
    </Section>
  );
}

// --- campaigns (ТЗ §29) ----------------------------------------------------------------------------------------
function Campaigns({ q }: { q: AnalyticsQuery }) {
  const r = useCampaignReport(q);
  return (
    <Section title="Кампании рядом" note={r.data?.note} query={r}>
      {() =>
        r.data!.items.length === 0 ? (
          <Empty>
            Кампаний нет — <Link href="/campaigns" className="text-blue-600 hover:underline">создать</Link>.
          </Empty>
        ) : (
          <Table
            head={
              <>
                <th className="py-1 font-medium">Кампания</th>
                <th className="py-1 font-medium">Статус</th>
                <th className="py-1 text-right font-medium">Лидов</th>
                <th className="py-1 text-right font-medium">Подтверждено</th>
                <OutcomeHeader />
                <th className="py-1 text-right font-medium">Офферы, $</th>
                <th className="py-1 text-right font-medium">$ за ответ</th>
              </>
            }
          >
            {r.data!.items.map((c) => (
              <tr key={c.id}>
                <td className="py-1.5">
                  <Link href={`/campaigns/${c.id}`} className="hover:underline">
                    {c.name}
                  </Link>
                </td>
                <td className="py-1.5">
                  <Badge>{CAMPAIGN_STATUS_LABELS[c.status]}</Badge>
                </td>
                <td className="py-1.5 text-right tabular-nums">{formatCount(c.leads)}</td>
                <td className="py-1.5 text-right tabular-nums">{formatCount(c.approved)}</td>
                <OutcomeCells outcomes={c.outcomes} />
                <td className="py-1.5 text-right tabular-nums">{formatUsd(c.cost_usd)}</td>
                <td className="py-1.5 text-right tabular-nums">{formatUnitCost(c.cost_per_reply_usd)}</td>
              </tr>
            ))}
          </Table>
        )
      }
    </Section>
  );
}

// --- A/B (ТЗ §28) ----------------------------------------------------------------------------------------------
function AB({ q, dimension, campaignId, onChange }: {
  q: AnalyticsQuery;
  dimension: ABDimension;
  campaignId?: number;
  onChange: (patch: { dim?: ABDimension; campaign?: number | null }) => void;
}) {
  const r = useABReport(q, dimension, campaignId);
  const campaigns = useCampaigns(true);
  const leader = r.data?.groups.find((g) => g.key === r.data?.leader);
  return (
    <Card>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">A/B: что отвечают чаще</h2>
        <div className="flex flex-wrap gap-2">
          <Select
            aria-label="Сравнивать по"
            className="h-7 w-40 text-xs"
            value={dimension}
            onChange={(e) => onChange({ dim: e.target.value as ABDimension })}
          >
            {AB_DIMENSIONS.map((d) => (
              <option key={d.key} value={d.key}>
                {d.label}
              </option>
            ))}
          </Select>
          <Select
            aria-label="Кампания"
            className="h-7 w-48 text-xs"
            value={campaignId ?? ""}
            onChange={(e) => onChange({ campaign: e.target.value ? Number(e.target.value) : null })}
          >
            <option value="">Все письма</option>
            {campaigns.data?.items.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </Select>
        </div>
      </div>
      {r.isPending ? (
        <LoadingState />
      ) : r.isError ? (
        <ErrorState error={r.error} onRetry={() => void r.refetch()} />
      ) : (
        <>
          <div className="mb-2 flex items-start gap-2 text-sm">
            <Badge tone={verdictTone(r.data.verdict)}>{VERDICT_LABELS[r.data.verdict]}</Badge>
            <span>{verdictText(r.data, leader?.label)}</span>
          </div>
          <Table
            head={
              <>
                <th className="py-1 font-medium">Группа</th>
                <th className="py-1 text-right font-medium">Отправлено</th>
                <th className="py-1 text-right font-medium">Доставлено</th>
                <th className="py-1 text-right font-medium" title="Только отмеченное вручную">
                  Открыто*
                </th>
                <th className="py-1 text-right font-medium">Ответы</th>
                <th className="py-1 text-right font-medium">Положит.</th>
                <th className="py-1 text-right font-medium">Отрицат.</th>
                <th className="py-1 text-right font-medium">Без ответа</th>
                <th className="py-1 text-right font-medium">Opt-out</th>
                <th className="py-1 text-right font-medium">Доля ответов</th>
              </>
            }
          >
            {r.data.groups.map((g) => (
              <tr key={g.key} className={cn(g.key === r.data.leader && r.data.verdict === "leader" && "font-medium")}>
                <td className="py-1.5">{g.style ? `${g.label.split(" ")[0]} · ${STYLE_LABELS[g.style]}` : g.label}</td>
                <td className="py-1.5 text-right tabular-nums">{formatCount(g.outcomes.sent)}</td>
                <td className="py-1.5 text-right tabular-nums">{formatCount(g.outcomes.delivered)}</td>
                <td className="py-1.5 text-right tabular-nums">{formatCount(g.outcomes.opened)}</td>
                <td className="py-1.5 text-right tabular-nums">{formatCount(g.outcomes.replied)}</td>
                <td className="py-1.5 text-right tabular-nums">{formatCount(g.outcomes.positive_reply)}</td>
                <td className="py-1.5 text-right tabular-nums">{formatCount(g.outcomes.negative_reply)}</td>
                <td className="py-1.5 text-right tabular-nums">{formatCount(g.outcomes.no_reply)}</td>
                <td className="py-1.5 text-right tabular-nums">{formatCount(g.outcomes.opt_out)}</td>
                <td className="py-1.5 text-right tabular-nums">
                  {formatRate(g.outcomes.reply_rate)}
                  {g.outcomes.small_sample && g.outcomes.sent > 0 ? (
                    <span className="ml-1 text-[10px] uppercase text-zinc-400">мало</span>
                  ) : null}
                </td>
              </tr>
            ))}
          </Table>
          <p className="mt-2 text-xs text-zinc-500">{r.data.note}</p>
        </>
      )}
    </Card>
  );
}

// --- costs (ТЗ §75) --------------------------------------------------------------------------------------------
function Costs({ q }: { q: AnalyticsQuery }) {
  const r = useCostReport(q);
  return (
    <Section
      title="Расходы на AI"
      note="Фактическая стоимость, где она известна, иначе оценка (как в бюджетах). Вызовы без цены не входят в сумму."
      query={r}
    >
      {() => {
        const s = r.data!.summary;
        const units: [string, string | null][] = [
          ["Найденный лид", s.per_lead_usd],
          ["Проанализированный лид", s.per_analyzed_lead_usd],
          ["Отправленное письмо", s.per_sent_usd],
          ["Ответ", s.per_reply_usd],
          ["Положительный ответ", s.per_positive_usd],
        ];
        return (
          <div className="space-y-4">
            <div className="flex flex-wrap gap-6 text-sm">
              <div>
                <p className="text-xs text-zinc-500">Всего</p>
                <p className="text-xl font-semibold tabular-nums">{formatUsd(s.total_usd)}</p>
                <p className="text-xs text-zinc-500">
                  {formatCount(s.calls)} вызовов
                  {s.unpriced_calls ? ` · ${formatCount(s.unpriced_calls)} без цены` : ""}
                </p>
              </div>
              {units.map(([k, v]) => (
                <div key={k}>
                  <p className="text-xs text-zinc-500">{k}</p>
                  <p className="text-xl font-semibold tabular-nums">{formatUnitCost(v)}</p>
                </div>
              ))}
            </div>
            {r.data!.by_task.length === 0 ? (
              <Empty>За период вызовов AI не было.</Empty>
            ) : (
              <>
                <CostTable title="По задачам" rows={r.data!.by_task} label={taskLabel} />
                <CostTable title="По моделям" rows={r.data!.by_model} label={(k) => k} showProvider />
              </>
            )}
          </div>
        );
      }}
    </Section>
  );
}

function CostTable({
  title,
  rows,
  label,
  showProvider = false,
}: {
  title: string;
  rows: NonNullable<ReturnType<typeof useCostReport>["data"]>["by_task"];
  label: (key: string) => string;
  showProvider?: boolean;
}) {
  return (
    <div>
      <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-zinc-500">{title}</h3>
      <Table
        head={
          <>
            <th className="py-1 font-medium">{showProvider ? "Модель" : "Задача"}</th>
            {showProvider ? <th className="py-1 font-medium">Провайдер</th> : null}
            <th className="py-1 text-right font-medium">Вызовов</th>
            <th className="py-1 text-right font-medium">Ошибок</th>
            <th className="py-1 text-right font-medium">Токены in / out</th>
            <th className="py-1 text-right font-medium">Изобр. in / out</th>
            <th className="py-1 text-right font-medium">Ср. время</th>
            <th className="py-1 text-right font-medium">Стоимость</th>
          </>
        }
      >
        {rows.map((c) => (
          <tr key={`${c.key}-${c.provider ?? ""}`}>
            <td className="py-1.5">{label(c.key)}</td>
            {showProvider ? <td className="py-1.5 text-zinc-500">{c.provider}</td> : null}
            <td className="py-1.5 text-right tabular-nums">{formatCount(c.calls)}</td>
            <td className="py-1.5 text-right tabular-nums">{formatCount(c.failed_calls)}</td>
            <td className="py-1.5 text-right tabular-nums">
              {formatCount(c.input_tokens)} / {formatCount(c.output_tokens)}
            </td>
            <td className="py-1.5 text-right tabular-nums">
              {formatCount(c.image_inputs)} / {formatCount(c.image_outputs)}
            </td>
            <td className="py-1.5 text-right tabular-nums">
              {c.avg_duration_ms !== null ? `${(c.avg_duration_ms / 1000).toLocaleString("ru-RU")} с` : "—"}
            </td>
            <td className="py-1.5 text-right tabular-nums">
              {formatUsd(c.cost_usd)}
              {c.unpriced_calls ? (
                <span className="ml-1 text-[10px] text-amber-600" title="Вызовы без цены">
                  +{c.unpriced_calls}?
                </span>
              ) : null}
            </td>
          </tr>
        ))}
      </Table>
    </div>
  );
}

// --- AI vs human (ТЗ §46) --------------------------------------------------------------------------------------
function Feedback({ q }: { q: AnalyticsQuery }) {
  const r = useAIFeedbackReport(q);
  return (
    <Section
      title="AI vs человек"
      note="Что AI / код предлагали и что я решил. Ручные правки баллов подробно — на странице «AI vs человек»."
      query={r}
    >
      {() => {
        const d = r.data!;
        return (
          <div className="space-y-4">
            <div>
              <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-zinc-500">
                Варианты превью по стратегии: какие я выбираю
              </h3>
              {d.generation_by_strategy.length === 0 ? (
                <Empty>Сгенерированных вариантов за период нет.</Empty>
              ) : (
                <Table
                  head={
                    <>
                      <th className="py-1 font-medium">Стратегия</th>
                      <th className="py-1 text-right font-medium">Вариантов</th>
                      <th className="py-1 text-right font-medium">Одобрено</th>
                      <th className="py-1 text-right font-medium">Финал</th>
                      <th className="py-1 text-right font-medium">Отклонено</th>
                      <th className="py-1 text-right font-medium">Без решения</th>
                      <th className="py-1 text-right font-medium">Доля одобренных</th>
                    </>
                  }
                >
                  {d.generation_by_strategy.map((g) => (
                    <tr key={g.key}>
                      <td className="py-1.5">{strategyLabel(g.key)}</td>
                      <td className="py-1.5 text-right tabular-nums">{formatCount(g.generated)}</td>
                      <td className="py-1.5 text-right tabular-nums">{formatCount(g.approved)}</td>
                      <td className="py-1.5 text-right tabular-nums">{formatCount(g.final)}</td>
                      <td className="py-1.5 text-right tabular-nums">{formatCount(g.rejected)}</td>
                      <td className="py-1.5 text-right tabular-nums">{formatCount(g.undecided)}</td>
                      <td className="py-1.5 text-right tabular-nums">{formatRate(g.approval_rate)}</td>
                    </tr>
                  ))}
                </Table>
              )}
            </div>
            <div>
              <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-zinc-500">Офферы по стилю</h3>
              {d.offers_by_style.length === 0 ? (
                <Empty>Решений по офферам за период нет.</Empty>
              ) : (
                <Table
                  head={
                    <>
                      <th className="py-1 font-medium">Стиль</th>
                      <th className="py-1 text-right font-medium">Approve</th>
                      <th className="py-1 text-right font-medium">Reject</th>
                      <th className="py-1 text-right font-medium">Skip</th>
                      <th className="py-1 text-right font-medium">Доля approve</th>
                      <th className="py-1 text-right font-medium" title="Personalization score">
                        Персонализация: approve / reject
                      </th>
                      <th className="py-1 text-right font-medium">Правок (офферов)</th>
                    </>
                  }
                >
                  {d.offers_by_style.map((o) => (
                    <tr key={o.style ?? "unknown"}>
                      <td className="py-1.5">{o.style ? `${o.label} · ${STYLE_LABELS[o.style]}` : "Неизвестно"}</td>
                      <td className="py-1.5 text-right tabular-nums">{formatCount(o.approve)}</td>
                      <td className="py-1.5 text-right tabular-nums">{formatCount(o.reject)}</td>
                      <td className="py-1.5 text-right tabular-nums">{formatCount(o.skip)}</td>
                      <td className="py-1.5 text-right tabular-nums">{formatRate(o.approval_rate)}</td>
                      <td className="py-1.5 text-right tabular-nums">
                        {formatPersonalization(o.avg_personalization_approved)} /{" "}
                        {formatPersonalization(o.avg_personalization_rejected)}
                      </td>
                      <td className="py-1.5 text-right tabular-nums">
                        {formatCount(o.edits)} ({formatCount(o.edited_offers)})
                      </td>
                    </tr>
                  ))}
                </Table>
              )}
            </div>
            <div>
              <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-zinc-500">
                Мои решения по лидам и Lead Score от кода
              </h3>
              <p className="mb-1 text-xs text-zinc-500">
                Если лиды с высоким баллом я часто отклоняю — код их переоценивает; если выбираю лиды с низким — недооценивает.
              </p>
              <Table
                head={
                  <>
                    <th className="py-1 font-medium">Lead Score</th>
                    <th className="py-1 text-right font-medium">Выбрал / шорт-лист</th>
                    <th className="py-1 text-right font-medium">Отклонил</th>
                    <th className="py-1 text-right font-medium">Доля выбранных</th>
                  </>
                }
              >
                {d.lead_decisions_by_score.map((b) => (
                  <tr key={b.key}>
                    <td className="py-1.5">{b.label}</td>
                    <td className="py-1.5 text-right tabular-nums">{formatCount(b.selected)}</td>
                    <td className="py-1.5 text-right tabular-nums">{formatCount(b.rejected)}</td>
                    <td className="py-1.5 text-right tabular-nums">{formatRate(b.selection_rate)}</td>
                  </tr>
                ))}
              </Table>
            </div>
            <p className="text-xs text-zinc-500">
              Ручных правок баллов: {formatCount(d.scores.overrides.reduce((n, o) => n + o.overrides, 0))} ·{" "}
              <Link href="/feedback" className="text-blue-600 hover:underline dark:text-blue-400">
                где AI завышает и занижает
              </Link>
            </p>
          </div>
        );
      }}
    </Section>
  );
}

/** ТЗ §87 Phase 9: conversion, A/B, campaigns, niches, cost, AI feedback. */
export function AnalyticsView() {
  const [params, setParams] = useUrlState();
  const { period, query } = analyticsQueryFromParams(params);
  const tab: Tab = enumParam(params, "tab", TAB_KEYS) ?? "niches";
  const dimension = enumParam(params, "dim", AB_DIMENSIONS.map((d) => d.key)) ?? "style";
  const campaignId = intParam(params, "campaign");
  const onFilters = (patch: { period?: PeriodKey; project?: number | null }) => setParams(patch);

  return (
    <div className="max-w-6xl">
      <PageHeader
        title="Аналитика"
        subtitle="Какие сегменты, варианты и кампании дают ответы — и сколько это стоит"
        actions={<AnalyticsFilters period={period} projectId={query.project_id} onChange={onFilters} />}
      />
      <nav aria-label="Разделы аналитики" className="mb-4 flex flex-wrap gap-1 border-b border-zinc-200 dark:border-zinc-800">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            aria-current={t.key === tab ? "page" : undefined}
            onClick={() => setParams({ tab: t.key === "niches" ? null : t.key })}
            className={cn(
              "-mb-px border-b-2 px-3 py-1.5 text-sm",
              t.key === tab
                ? "border-zinc-900 font-medium dark:border-zinc-100"
                : "border-transparent text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100",
            )}
          >
            {t.label}
          </button>
        ))}
      </nav>
      {tab === "niches" ? <Niches q={query} /> : null}
      {tab === "scores" ? <ScoreRanges q={query} /> : null}
      {tab === "campaigns" ? <Campaigns q={query} /> : null}
      {tab === "ab" ? (
        <AB
          q={query}
          dimension={dimension}
          campaignId={campaignId}
          onChange={(p) => setParams({ ...(p.dim ? { dim: p.dim } : {}), ...("campaign" in p ? { campaign: p.campaign } : {}) })}
        />
      ) : null}
      {tab === "costs" ? <Costs q={query} /> : null}
      {tab === "feedback" ? <Feedback q={query} /> : null}
    </div>
  );
}
