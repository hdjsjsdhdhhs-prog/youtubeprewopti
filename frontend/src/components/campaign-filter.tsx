"use client";

import { X } from "lucide-react";
import Link from "next/link";

import { useCampaign } from "@/lib/api/campaigns-hooks";

/** «Only campaign X» above a list filtered by `?campaign_id=` (Approval Queue, outreach messages). */
export function CampaignFilterChip({ campaignId, onClear }: { campaignId: number; onClear: () => void }) {
  const campaign = useCampaign(campaignId);
  const name = campaign.data?.name ?? (campaign.isError ? `#${campaignId}` : "…");
  return (
    <div className="mb-3 inline-flex items-center gap-2 rounded-md border border-violet-200 bg-violet-50 px-2.5 py-1 text-sm dark:border-violet-900 dark:bg-violet-950/40">
      <span>
        Только кампания{" "}
        <Link href={`/campaigns/${campaignId}`} className="font-medium hover:underline">
          «{name}»
        </Link>
      </span>
      <button
        type="button"
        aria-label="Показать все, не только эту кампанию"
        onClick={onClear}
        className="rounded p-0.5 text-zinc-500 hover:bg-violet-100 hover:text-zinc-900 dark:hover:bg-violet-900 dark:hover:text-zinc-100"
      >
        <X className="size-3.5" aria-hidden />
      </button>
    </div>
  );
}
