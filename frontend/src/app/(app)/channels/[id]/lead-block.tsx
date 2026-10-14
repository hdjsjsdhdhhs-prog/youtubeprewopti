"use client";

import Link from "next/link";

import { LeadScoreBreakdown } from "@/components/lead-score";
import { InlineError } from "@/components/states";
import { Badge, Card } from "@/components/ui";
import { useLeads } from "@/lib/api/hooks";
import { ACTIVITY_LABELS, STATUS_LABELS, type LeadQuery } from "@/lib/leads";

/** The channel as a lead of this workspace (Phase 3.7): status and the explainable Lead Score. */
export function ChannelLead({ channelId }: { channelId: number }) {
  const query: LeadQuery = { channel_id: channelId, sort: "lead_score", order: "desc", offset: 0 };
  const leads = useLeads(query);
  const lead = leads.data?.items[0];

  return (
    <Card className="mt-4">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold">Лид</h2>
        {lead ? (
          <>
            <Badge tone="blue">{STATUS_LABELS[lead.status]}</Badge>
            {lead.activity_level ? <Badge>активность: {ACTIVITY_LABELS[lead.activity_level]}</Badge> : null}
            <Link href="/leads" className="text-xs text-zinc-500 hover:underline">
              все лиды
            </Link>
          </>
        ) : null}
      </div>
      <InlineError error={leads.error} />
      {leads.isPending ? (
        <p className="text-sm text-zinc-500">Загрузка…</p>
      ) : !lead ? (
        <p className="text-sm text-zinc-500">
          Лида ещё нет: он создаётся после поиска и анализа (или кнопкой «Пересчитать» на странице «Лиды»).
        </p>
      ) : lead.score ? (
        <LeadScoreBreakdown score={lead.score} />
      ) : (
        <p className="text-sm text-zinc-500">Балл ещё не посчитан.</p>
      )}
    </Card>
  );
}
