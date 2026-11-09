"use client";

import { Archive, ArrowLeft, Calculator, CheckCheck, Pause, Pencil, Play, Sparkles, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { EmptyState, ErrorState, LoadingState, errorMessage } from "@/components/states";
import { Badge, Button, Card, PageHeader, Pager } from "@/components/ui";
import {
  CAMPAIGN_LEADS_PAGE,
  useCampaign,
  useCampaignAction,
  useCampaignEstimate,
  useCampaignLeads,
  useCampaignStats,
  useRemoveCampaignLead,
  useStartCampaignOffers,
  type Campaign,
  type CampaignAction,
  type CampaignEstimate,
} from "@/lib/api/campaigns-hooks";
import { ApiError } from "@/lib/api/client";
import {
  CAMPAIGN_LEAD_STATUS_LABELS,
  CAMPAIGN_STATUS_LABELS,
  CAMPAIGN_STATUS_TONES,
  FUNNEL,
  GENERATION_SKIP_LABELS,
  ISSUE_LABELS,
  STAGE_LABELS,
  STAGE_TONES,
  campaignErrorMessage,
  canArchive,
  canComplete,
  canEditCampaign,
  canGenerate,
  canPause,
  canResume,
  canStart,
  formatRate,
  formatWindow,
  isCampaignLive,
  type CampaignLeadStatus,
} from "@/lib/campaigns";
import { formatDateTime } from "@/lib/format";
import { STATUS_LABELS } from "@/lib/leads";
import { CHANNEL_LABELS, STYLE_LABELS } from "@/lib/offers";
import { formatUsd } from "@/lib/prefilter";
import { cn } from "@/lib/utils";

import { CampaignFormView } from "../campaign-form";

const LEAD_TABS: readonly (CampaignLeadStatus | undefined)[] = [undefined, "active", "sent", "failed", "rejected", "skipped", "removed"];

function ActionError({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <p role="alert" className="text-sm text-red-600">
      {campaignErrorMessage(error) ?? errorMessage(error)}
    </p>
  );
}

export function CampaignDetail({ id }: { id: number }) {
  const campaign = useCampaign(id);
  if (campaign.isPending) return <LoadingState />;
  if (campaign.isError) {
    return (
      <>
        <BackLink />
        <ErrorState error={campaign.error} onRetry={() => void campaign.refetch()} />
      </>
    );
  }
  const c = campaign.data;
  const live = isCampaignLive(c.status);
  return (
    <div className="max-w-6xl space-y-4">
      <div>
        <BackLink />
        <PageHeader
          title={
            <span className="flex flex-wrap items-center gap-2">
              {c.name}
              <Badge tone={CAMPAIGN_STATUS_TONES[c.status]}>{CAMPAIGN_STATUS_LABELS[c.status]}</Badge>
            </span>
          }
          subtitle={c.description || undefined}
          actions={<Actions campaign={c} />}
        />
        <StatusNotes campaign={c} />
      </div>
      <Settings campaign={c} />
      <Progress campaign={c} />
      {canGenerate(c.status) ? <Generation campaign={c} /> : null}
      <Stats id={c.id} live={live} />
      <Leads campaign={c} live={live} />
    </div>
  );
}

function BackLink() {
  return (
    <Link href="/campaigns" className="mb-3 inline-flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100">
      <ArrowLeft className="size-4" aria-hidden /> Кампании
    </Link>
  );
}

const CONFIRM: Partial<Record<CampaignAction, string>> = {
  start:
    "Запустить кампанию? С этого момента каждый оффер кампании, который вы подтвердите, сам встанет в очередь и уйдёт в окно отправки с лимитами кампании.",
  complete: "Завершить кампанию? Письма в очереди будут отменены (офферы останутся подтверждёнными), лиды в работе освободятся.",
  archive: "Отправить кампанию в архив? Письма в очереди будут отменены, лиды в работе освободятся.",
};

function Actions({ campaign: c }: { campaign: Campaign }) {
  const action = useCampaignAction(c.id);
  const run = (a: CampaignAction) => {
    const text = CONFIRM[a];
    if (text && !window.confirm(text)) return;
    action.mutate(a);
  };
  const busy = action.isPending;
  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex flex-wrap items-center gap-2">
        {canStart(c.status) ? (
          <Button disabled={busy || c.issues.length > 0} title={c.issues.map((i) => ISSUE_LABELS[i]).join("; ") || undefined} onClick={() => run("start")}>
            <Play className="size-4" aria-hidden /> Запустить
          </Button>
        ) : null}
        {canPause(c.status) ? (
          <Button variant="secondary" disabled={busy} onClick={() => run("pause")}>
            <Pause className="size-4" aria-hidden /> Пауза
          </Button>
        ) : null}
        {canResume(c.status) ? (
          <Button disabled={busy} onClick={() => run("resume")}>
            <Play className="size-4" aria-hidden /> Возобновить
          </Button>
        ) : null}
        {canComplete(c.status) ? (
          <Button variant="secondary" disabled={busy} onClick={() => run("complete")}>
            <CheckCheck className="size-4" aria-hidden /> Завершить
          </Button>
        ) : null}
        {canArchive(c.status) ? (
          <Button variant="ghost" disabled={busy} onClick={() => run("archive")}>
            <Archive className="size-4" aria-hidden /> В архив
          </Button>
        ) : null}
      </div>
      <ActionError error={action.error} />
    </div>
  );
}

function StatusNotes({ campaign: c }: { campaign: Campaign }) {
  const notes: { text: string; tone: "amber" | "zinc" | "violet" }[] = [];
  if (c.status === "paused" && c.paused_reason) notes.push({ text: `Пауза: ${c.paused_reason}. Письма ждут в очереди.`, tone: "amber" });
  if ((c.status === "draft" || c.status === "ready" || c.status === "pending_approval") && c.issues.length) {
    notes.push({ text: `Чтобы запустить: ${c.issues.map((i) => ISSUE_LABELS[i]).join("; ")}.`, tone: "amber" });
  }
  if (c.status === "running") {
    notes.push({
      text: c.in_window_now
        ? `Сейчас окно отправки. Сегодня отправлено ${c.sent_today} из ${c.daily_limit}.`
        : `Вне окна отправки: следующее окно — ${formatDateTime(c.next_window_at)}.`,
      tone: "violet",
    });
  }
  if (c.status === "ready" || c.status === "pending_approval") {
    notes.push({
      text: "Пока кампания не запущена, подтверждённые офферы никуда не уходят. После запуска подтверждённое отправляется само — в окно и с лимитами кампании.",
      tone: "zinc",
    });
  }
  if (!notes.length) return null;
  return (
    <div className="space-y-1">
      {notes.map((n) => (
        <p
          key={n.text}
          className={cn(
            "text-sm",
            n.tone === "amber" && "text-amber-700 dark:text-amber-400",
            n.tone === "violet" && "text-violet-700 dark:text-violet-400",
            n.tone === "zinc" && "text-zinc-500",
          )}
        >
          {n.text}
        </p>
      ))}
    </div>
  );
}

function Settings({ campaign: c }: { campaign: Campaign }) {
  const [editing, setEditing] = useState(false);
  const rows: [string, string][] = [
    ["Проект", c.project_name ?? "—"],
    ["Шаблон оффера", c.template_name ?? "без шаблона"],
    ["Язык", c.language ?? "как у канала"],
    ["Варианты (A/B)", c.styles.map((s) => STYLE_LABELS[s]).join(" / ")],
    ["Каналы", c.channels.map((ch) => CHANNEL_LABELS[ch]).join(", ")],
    [
      "Почтовые аккаунты",
      c.channels.includes("email") ? (c.email_accounts.length ? c.email_accounts.map((a) => a.name).join(", ") : "любой активный") : "—",
    ],
    ["Окно отправки", formatWindow(c)],
    ["Не раньше", c.starts_at ? formatDateTime(c.starts_at) : "сразу"],
    ["Писем в день", `${c.daily_limit} (сегодня ${c.sent_today})`],
    ["Автопауза", `после ${c.max_consecutive_failures} ошибок подряд (сейчас ${c.consecutive_failures})`],
    ["Запущена", formatDateTime(c.started_at)],
    ["Завершена", formatDateTime(c.completed_at)],
  ];
  return (
    <Card>
      <div className="mb-2 flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">Настройки</h2>
        {canEditCampaign(c.status) && !editing ? (
          <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
            <Pencil className="size-3.5" aria-hidden /> Изменить
          </Button>
        ) : null}
      </div>
      {editing ? (
        <CampaignFormView campaign={c} onDone={() => setEditing(false)} />
      ) : (
        <>
          <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-3">
            {rows.map(([k, v]) => (
              <div key={k}>
                <dt className="text-xs text-zinc-500">{k}</dt>
                <dd>{v}</dd>
              </div>
            ))}
          </dl>
          {c.instructions ? <p className="mt-2 whitespace-pre-line text-sm text-zinc-600 dark:text-zinc-400">Инструкции: {c.instructions}</p> : null}
        </>
      )}
    </Card>
  );
}

function Progress({ campaign: c }: { campaign: Campaign }) {
  const n = c.counts;
  const outcomes: [string, number][] = [
    ["в работе", n.active],
    ["отправлено", n.sent],
    ["ответили", n.replied],
    ["отказались", n.opted_out],
    ["не отправлено", n.failed],
    ["оффер отклонён", n.rejected],
    ["пропущено", n.skipped],
    ["убрано", n.removed],
  ];
  return (
    <Card>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">
          Лиды кампании <span className="font-normal text-zinc-500">({n.leads})</span>
        </h2>
        <div className="flex flex-wrap gap-3 text-sm">
          <Link href={`/approvals?campaign_id=${c.id}`} className="text-violet-700 hover:underline dark:text-violet-400">
            офферы на подтверждении{n.stages.review ? ` (${n.stages.review})` : ""}
          </Link>
          <Link href={`/outreach?campaign_id=${c.id}`} className="text-violet-700 hover:underline dark:text-violet-400">
            сообщения кампании
          </Link>
          <Link href="/leads" className="text-zinc-500 hover:underline">
            добавить лиды
          </Link>
        </div>
      </div>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm tabular-nums sm:grid-cols-4">
        {outcomes.map(([k, v]) => (
          <div key={k}>
            <dt className="text-xs text-zinc-500">{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
      {n.active ? (
        <div className="mt-3 border-t border-zinc-100 pt-3 dark:border-zinc-800">
          <p className="mb-1 text-xs font-medium text-zinc-500">Где сейчас лиды в работе</p>
          <div className="flex flex-wrap gap-1.5">
            {FUNNEL.filter((s) => n.stages[s]).map((s) => (
              <Badge key={s} tone={STAGE_TONES[s]}>
                {STAGE_LABELS[s]}: {n.stages[s]}
              </Badge>
            ))}
          </div>
        </div>
      ) : null}
    </Card>
  );
}

const isConfirmationError = (e: unknown) => e instanceof ApiError && e.code === "confirmation_required";

/** §76: estimate → confirm → start. One draft per lead in its A/B style; all of them wait in the Approval Queue. */
function Generation({ campaign: c }: { campaign: Campaign }) {
  const estimate = useCampaignEstimate(c.id);
  const start = useStartCampaignOffers(c.id);
  const [notice, setNotice] = useState<string | null>(null);
  const e = estimate.data;

  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">Офферы</h2>
        <Button
          size="sm"
          variant="secondary"
          disabled={estimate.isPending}
          onClick={() => {
            setNotice(null);
            start.reset();
            estimate.mutate();
          }}
        >
          <Calculator className="size-3.5" aria-hidden /> Оценить написание офферов
        </Button>
      </div>
      <p className="mt-1 text-xs text-zinc-500">
        Для лидов без оффера пишется один черновик в его варианте A/B. Каждый черновик ждёт вашего решения в очереди
        подтверждения — кампания ничего не подтверждает сама.
      </p>
      {notice ? <p className="mt-2 text-sm text-amber-700 dark:text-amber-400">{notice}</p> : null}
      {start.data ? (
        <p role="status" className="mt-2 text-sm text-emerald-700 dark:text-emerald-400">
          Поставлено в очередь: {start.data.started}.{" "}
          <Link href="/jobs" className="underline">
            Задачи
          </Link>
        </p>
      ) : null}
      {e ? (
        <EstimateView
          estimate={e}
          pending={start.isPending}
          onConfirm={() =>
            start.mutate(e.confirm_token, {
              onSuccess: () => estimate.reset(),
              onError: (err) => {
                if (!isConfirmationError(err)) return;
                setNotice("Состав лидов, модель или цена изменились после оценки. Проверьте новую оценку и подтвердите снова.");
                estimate.mutate();
              },
            })
          }
        />
      ) : null}
      <ActionError error={estimate.error ?? (isConfirmationError(start.error) ? null : start.error)} />
    </Card>
  );
}

function EstimateView({ estimate: e, pending, onConfirm }: { estimate: CampaignEstimate; pending: boolean; onConfirm: () => void }) {
  const skipped = Object.entries(e.skipped).filter(([, v]) => v > 0);
  const count = e.lead_ids.length;
  return (
    <div className="mt-3 space-y-2 rounded-md border border-zinc-200 p-3 text-sm dark:border-zinc-700">
      {count ? (
        <p>
          Офферов: <b>{count}</b> (
          {Object.entries(e.styles)
            .filter(([, v]) => v > 0)
            .map(([s, v]) => `${STYLE_LABELS[s as keyof typeof STYLE_LABELS] ?? s}: ${v}`)
            .join(", ")}
          ) · модель {e.model_key ?? "не настроена"} {e.provider === "mock" ? <Badge tone="amber">mock</Badge> : null} · до{" "}
          {formatUsd(e.estimated_cost_usd)}
          {e.pricing_verified ? "" : " (цена не проверена)"}
        </p>
      ) : (
        <p>Сейчас никому из лидов кампании не нужен оффер.</p>
      )}
      {e.remaining ? <p className="text-zinc-500">Ещё {e.remaining} — следующим запуском (ограничение пакета).</p> : null}
      {skipped.length ? (
        <p className="text-zinc-500">
          Пропущены: {skipped.map(([k, v]) => `${GENERATION_SKIP_LABELS[k] ?? k} — ${v}`).join("; ")}
        </p>
      ) : null}
      {!e.within_budgets ? <p className="text-amber-700 dark:text-amber-400">Запуск превысит бюджет AI.</p> : null}
      {count ? (
        <Button disabled={pending || !e.model_key} onClick={onConfirm}>
          <Sparkles className="size-4" aria-hidden /> Подтвердить и написать
        </Button>
      ) : null}
    </div>
  );
}

/** ТЗ §28: counts per variant, never a verdict. */
function Stats({ id, live }: { id: number; live: boolean }) {
  const stats = useCampaignStats(id, live);
  return (
    <Card>
      <h2 className="mb-2 text-sm font-semibold">A/B: результаты по вариантам</h2>
      {stats.isPending ? (
        <LoadingState />
      ) : stats.isError ? (
        <ErrorState error={stats.error} onRetry={() => void stats.refetch()} />
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-sm tabular-nums">
              <thead className="text-left text-xs uppercase tracking-wide text-zinc-500">
                <tr>
                  {["Вариант", "Лидов", "Подтв.", "Отправл.", "Доставл.", "Ответ", "+ / −", "Без ответа", "Недост.", "Отказ", "Доля ответов"].map((h) => (
                    <th key={h} scope="col" className="py-1 pr-3 font-medium">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
                {stats.data.variants.map((v) => (
                  <tr key={v.style}>
                    <td className="py-1 pr-3">
                      <span className="font-medium">{v.label}</span> <span className="text-zinc-500">{STYLE_LABELS[v.style]}</span>
                    </td>
                    <td className="py-1 pr-3">{v.leads}</td>
                    <td className="py-1 pr-3">{v.approved}</td>
                    <td className="py-1 pr-3">{v.sent}</td>
                    <td className="py-1 pr-3">{v.delivered}</td>
                    <td className="py-1 pr-3">{v.replied}</td>
                    <td className="py-1 pr-3">
                      {v.positive_reply} / {v.negative_reply}
                    </td>
                    <td className="py-1 pr-3">{v.no_reply}</td>
                    <td className="py-1 pr-3">{v.bounced}</td>
                    <td className="py-1 pr-3">{v.opt_out}</td>
                    <td className="py-1 pr-3">
                      {formatRate(v.reply_rate)}
                      {v.small_sample ? (
                        <Badge tone="amber" title={`Меньше ${stats.data.min_sample} отправленных — сравнивать рано`}>
                          мало данных
                        </Badge>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-xs text-zinc-500">{stats.data.note}</p>
        </>
      )}
    </Card>
  );
}

function Leads({ campaign: c, live }: { campaign: Campaign; live: boolean }) {
  const [status, setStatus] = useState<CampaignLeadStatus | undefined>(undefined);
  const [offset, setOffset] = useState(0);
  const leads = useCampaignLeads(c.id, { status, offset }, live);
  const remove = useRemoveCampaignLead(c.id);
  const editable = canEditCampaign(c.status);

  return (
    <Card className="p-0">
      <div className="flex flex-wrap items-center gap-1 border-b border-zinc-200 px-4 py-2 dark:border-zinc-800" role="tablist" aria-label="Лиды кампании">
        {LEAD_TABS.map((s) => (
          <button
            key={s ?? "all"}
            type="button"
            role="tab"
            aria-selected={status === s}
            onClick={() => {
              setStatus(s);
              setOffset(0);
            }}
            className={cn(
              "rounded-md px-2.5 py-1 text-sm",
              status === s ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900" : "text-zinc-600 hover:bg-zinc-100 dark:hover:bg-zinc-800",
            )}
          >
            {s ? CAMPAIGN_LEAD_STATUS_LABELS[s] : "все"}
          </button>
        ))}
      </div>
      <div className="p-4">
        <ActionError error={remove.error} />
        {leads.isPending ? (
          <LoadingState />
        ) : leads.isError ? (
          <ErrorState error={leads.error} onRetry={() => void leads.refetch()} />
        ) : leads.data.items.length === 0 ? (
          <EmptyState
            title="Лидов нет"
            hint={status ? undefined : "Отметьте лиды на странице «Лиды» и нажмите «В кампанию»."}
          />
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className={cn("w-full text-sm", leads.isPlaceholderData && "opacity-60")}>
                <thead className="text-left text-xs uppercase tracking-wide text-zinc-500">
                  <tr>
                    <th scope="col" className="py-1 pr-3 font-medium">Канал</th>
                    <th scope="col" className="py-1 pr-3 font-medium">Вариант</th>
                    <th scope="col" className="py-1 pr-3 font-medium">Этап</th>
                    <th scope="col" className="py-1 pr-3 font-medium">Лид</th>
                    <th scope="col" className="py-1 pr-3 font-medium">Отправлено</th>
                    <th scope="col" className="py-1 font-medium">
                      <span className="sr-only">Действия</span>
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
                  {leads.data.items.map((l) => (
                    <tr key={l.lead_id}>
                      <td className="max-w-72 py-1.5 pr-3">
                        <Link href={`/channels/${l.channel_id}`} className="block truncate font-medium hover:underline" title={l.channel_title}>
                          {l.channel_title}
                        </Link>
                      </td>
                      <td className="py-1.5 pr-3 whitespace-nowrap" title={STYLE_LABELS[l.style]}>
                        {l.label} <span className="text-zinc-500">{STYLE_LABELS[l.style]}</span>
                      </td>
                      <td className="py-1.5 pr-3">
                        <Badge tone={STAGE_TONES[l.stage]} title={l.reason ?? undefined}>
                          {STAGE_LABELS[l.stage]}
                        </Badge>
                        {l.reason ? <p className="mt-0.5 text-xs text-zinc-500">{l.reason}</p> : null}
                      </td>
                      <td className="py-1.5 pr-3 whitespace-nowrap">{STATUS_LABELS[l.lead_status]}</td>
                      <td className="py-1.5 pr-3 whitespace-nowrap tabular-nums text-zinc-500">{formatDateTime(l.sent_at)}</td>
                      <td className="py-1.5 text-right whitespace-nowrap">
                        {l.offer_id && l.stage === "review" ? (
                          <Link href={`/approvals?campaign_id=${c.id}`} className="mr-2 text-xs text-violet-700 hover:underline dark:text-violet-400">
                            к подтверждению
                          </Link>
                        ) : null}
                        {editable && l.status === "active" ? (
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={remove.isPending}
                            aria-label={`Убрать «${l.channel_title}» из кампании`}
                            onClick={() => {
                              if (window.confirm(`Убрать «${l.channel_title}» из кампании? Письмо в очереди (если есть) отменится, оффер останется.`)) {
                                remove.mutate(l.lead_id);
                              }
                            }}
                          >
                            <X className="size-3.5" aria-hidden /> Убрать
                          </Button>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pager offset={offset} limit={CAMPAIGN_LEADS_PAGE} total={leads.data.total} onChange={setOffset} />
          </>
        )}
      </div>
    </Card>
  );
}
