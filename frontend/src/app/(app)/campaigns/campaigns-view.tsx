"use client";

import { Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Badge, Button, Card, PageHeader } from "@/components/ui";
import { useCampaigns, type Campaign } from "@/lib/api/campaigns-hooks";
import { CAMPAIGN_STATUS_LABELS, CAMPAIGN_STATUS_TONES, ISSUE_LABELS, formatWindow } from "@/lib/campaigns";
import { CHANNEL_LABELS, STYLE_LABELS } from "@/lib/offers";

import { CampaignFormView } from "./campaign-form";

/** ТЗ §29: named outreach runs over chosen leads with an A/B of offer styles, a send window and limits. */
export function CampaignsView() {
  const router = useRouter();
  const [archived, setArchived] = useState(false);
  const [adding, setAdding] = useState(false);
  const list = useCampaigns(archived);

  return (
    <div className="max-w-5xl">
      <PageHeader
        title="Кампании"
        subtitle="Рассылка по выбранным лидам: A/B вариантов оффера, окно отправки, лимиты и автопауза. Каждый оффер подтверждается вручную"
        actions={
          !adding ? (
            <Button onClick={() => setAdding(true)}>
              <Plus className="size-4" aria-hidden /> Новая кампания
            </Button>
          ) : null
        }
      />
      {adding ? (
        <Card className="mb-4">
          <h2 className="mb-2 text-sm font-semibold">Новая кампания</h2>
          <CampaignFormView
            onDone={(c) => {
              setAdding(false);
              if (c) router.push(`/campaigns/${c.id}`);
            }}
          />
        </Card>
      ) : null}
      <label className="mb-3 inline-flex items-center gap-1.5 text-sm text-zinc-600 dark:text-zinc-400">
        <input type="checkbox" className="size-4" checked={archived} onChange={(e) => setArchived(e.currentTarget.checked)} />
        показывать архив
      </label>
      {list.isPending ? (
        <LoadingState />
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => void list.refetch()} />
      ) : list.data.items.length === 0 ? (
        <EmptyState
          title="Кампаний пока нет"
          hint="Создайте кампанию, затем добавьте в неё лиды на странице «Лиды» (кнопка «В кампанию») и напишите офферы."
        />
      ) : (
        <ul className="space-y-3">
          {list.data.items.map((c) => (
            <li key={c.id}>
              <CampaignRow campaign={c} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function CampaignRow({ campaign: c }: { campaign: Campaign }) {
  const n = c.counts;
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <Link href={`/campaigns/${c.id}`} className="font-semibold hover:underline">
          {c.name}
        </Link>
        <Badge tone={CAMPAIGN_STATUS_TONES[c.status]}>{CAMPAIGN_STATUS_LABELS[c.status]}</Badge>
        {c.project_name ? <span className="text-xs text-zinc-500">проект «{c.project_name}»</span> : null}
      </div>
      {c.paused_reason && c.status === "paused" ? (
        <p className="mt-1 text-sm text-amber-700 dark:text-amber-400">{c.paused_reason}</p>
      ) : null}
      <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
        {c.styles.map((s) => STYLE_LABELS[s]).join(" / ")} · {c.channels.map((ch) => CHANNEL_LABELS[ch]).join(", ")} ·{" "}
        {formatWindow(c)}
      </p>
      <p className="mt-1 text-sm tabular-nums">
        Лидов: {n.leads} · в работе: {n.active} · отправлено: {n.sent} · ответили: {n.replied}
        {n.failed ? ` · не отправлено: ${n.failed}` : ""} · сегодня {c.sent_today} из {c.daily_limit}
      </p>
      {c.issues.length && (c.status === "draft" || c.status === "ready") ? (
        <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">{c.issues.map((i) => ISSUE_LABELS[i]).join("; ")}</p>
      ) : null}
    </Card>
  );
}
