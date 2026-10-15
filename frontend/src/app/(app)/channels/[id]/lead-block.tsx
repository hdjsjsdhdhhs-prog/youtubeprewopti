"use client";

import Link from "next/link";

import { LeadScoreBreakdown } from "@/components/lead-score";
import { FavoriteStar, HumanScore, LeadStatusActions, LeadTagsEditor } from "@/components/lead-workflow";
import { LeadOffer } from "@/components/lead-offer";
import { InlineError } from "@/components/states";
import { LeadGeneration } from "@/components/thumbnail-generation";
import { Badge, Card } from "@/components/ui";
import { useLeads } from "@/lib/api/hooks";
import { ACTIVITY_LABELS, STATUS_LABELS, formatLeadScore, type LeadQuery } from "@/lib/leads";

const leadQuery = (channelId: number): LeadQuery => ({ channel_id: channelId, sort: "lead_score", order: "desc", offset: 0 });

/** Thumbnail generation of the channel's lead (Phase 5); the lead comes from the same cached query as the card. */
export function ChannelGeneration({ channelId }: { channelId: number }) {
  const lead = useLeads(leadQuery(channelId)).data?.items[0];
  return lead ? <LeadGeneration lead={lead} /> : null;
}

/** The personalized offer of the channel's lead (Phase 7). */
export function ChannelOffer({ channelId }: { channelId: number }) {
  const lead = useLeads(leadQuery(channelId)).data?.items[0];
  return lead ? <LeadOffer lead={lead} /> : null;
}

/** The channel as a lead of this workspace: status, decisions, tags, favorite, human scores (Phase 4) and the
 * explainable Lead Score (Phase 3.7). */
export function ChannelLead({ channelId }: { channelId: number }) {
  const leads = useLeads(leadQuery(channelId));
  const lead = leads.data?.items[0];

  return (
    <Card className="mt-4">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold">Лид</h2>
        {lead ? (
          <>
            <FavoriteStar type="channel" id={channelId} on={lead.is_favorite} label={lead.channel.title} />
            <Badge tone="blue">{STATUS_LABELS[lead.status]}</Badge>
            {lead.activity_level ? <Badge>активность: {ACTIVITY_LABELS[lead.activity_level]}</Badge> : null}
            <Link href="/leads" className="text-xs text-zinc-500 hover:underline">
              все лиды
            </Link>
          </>
        ) : null}
      </div>
      {lead ? (
        <div className="mb-3 space-y-2">
          <LeadStatusActions lead={lead} />
          {lead.rejected_reason ? (
            <p className="text-xs text-zinc-500">Причина отклонения: {lead.rejected_reason}</p>
          ) : null}
          <LeadTagsEditor lead={lead} />
          {lead.score ? (
            <div className="flex flex-wrap gap-x-6">
              <HumanScore
                target="lead"
                targetId={lead.id}
                field="total"
                aiValue={lead.score.total}
                overrides={lead.overrides}
                min={0}
                max={100}
                label="Lead Score"
                format={formatLeadScore}
              />
              <HumanScore
                target="lead"
                targetId={lead.id}
                field="priority_score"
                aiValue={lead.score.priority_score}
                overrides={lead.overrides}
                min={0}
                max={100}
                label="Приоритет"
                format={formatLeadScore}
              />
            </div>
          ) : null}
        </div>
      ) : null}
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
