"use client";

import { ChannelAuditDetails, ChannelAuditHeadline, ComparedThumbnails } from "@/components/channel-audit";
import { ErrorState, LoadingState } from "@/components/states";
import { Card } from "@/components/ui";
import { useChannelVisualAudit } from "@/lib/api/hooks";

/** Phase 3.6: the channel's current comparative thumbnail audit (ТЗ §17). */
export function ChannelVisualAudit({ channelId }: { channelId: number }) {
  const report = useChannelVisualAudit(channelId);
  if (report.isPending) {
    return (
      <Card className="mt-4">
        <LoadingState />
      </Card>
    );
  }
  if (report.isError) {
    return (
      <Card className="mt-4">
        <ErrorState error={report.error} onRetry={() => void report.refetch()} />
      </Card>
    );
  }
  const { analysis, aggregates, videos, scoring } = report.data;
  if (!analysis || !aggregates) {
    return (
      <Card className="mt-4">
        <h2 className="text-sm font-semibold">Визуальный аудит канала</h2>
        <p className="mt-1 text-xs text-zinc-500">
          Ещё не проводился. Запускается на странице проекта (блок «Аудит каналов») после AI-аудита минимум трёх превью
          канала.
        </p>
      </Card>
    );
  }
  return (
    <Card className="mt-4 space-y-4">
      <div>
        <h2 className="text-sm font-semibold">
          Визуальный аудит канала{" "}
          <span className="font-normal text-zinc-500">
            · веса профиля версии {scoring.version}
            {scoring.is_default ? " (по умолчанию)" : ""}
          </span>
        </h2>
        <div className="mt-2">
          <ChannelAuditHeadline analysis={analysis} aggregates={aggregates} />
        </div>
      </div>
      <ComparedThumbnails videos={videos} analysis={analysis} />
      <ChannelAuditDetails analysis={analysis} videos={videos} />
    </Card>
  );
}
