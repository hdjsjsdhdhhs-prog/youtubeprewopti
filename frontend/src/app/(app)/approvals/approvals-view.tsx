"use client";

import { ImageOff } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { OfferEditor } from "@/components/offer-editor";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Badge, Card, PageHeader, Pager } from "@/components/ui";
import { QUEUE_PAGE, useApprovalQueue, type QueueCard } from "@/lib/api/offers-hooks";
import { problemLabel } from "@/lib/analysis";
import { CONTACT_TYPE_LABELS } from "@/lib/contacts";
import { formatCount } from "@/lib/format";
import { STATUS_LABELS, formatLeadScore, leadScoreTone } from "@/lib/leads";
import { OFFER_STATUS_LABELS, QUEUE_TABS, type OfferStatus } from "@/lib/offers";
import { cn } from "@/lib/utils";

/** ТЗ §22: every offer waits here for a human decision. Nothing is sent before «Подтвердить». */
export function ApprovalsView() {
  const [status, setStatus] = useState<OfferStatus>("pending_approval");
  const [offset, setOffset] = useState(0);
  const queue = useApprovalQueue(status, offset);
  const counts = queue.data?.counts;

  return (
    <div className="max-w-6xl">
      <PageHeader
        title="Очередь подтверждения"
        subtitle="Офферы ждут вашего решения: подтвердить, поправить, перегенерировать, отклонить, пропустить или запланировать"
      />
      <div role="tablist" aria-label="Статус офферов" className="mb-4 flex flex-wrap gap-1">
        {QUEUE_TABS.map((s) => (
          <button
            key={s}
            type="button"
            role="tab"
            aria-selected={status === s}
            onClick={() => {
              setStatus(s);
              setOffset(0);
            }}
            className={cn(
              "rounded-md px-3 py-1.5 text-sm",
              status === s ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900" : "text-zinc-600 hover:bg-zinc-100 dark:hover:bg-zinc-800",
            )}
          >
            {OFFER_STATUS_LABELS[s]}
            {counts ? <span className="ml-1 opacity-70">{counts[s] ?? 0}</span> : null}
          </button>
        ))}
      </div>
      {queue.isPending ? (
        <LoadingState />
      ) : queue.isError ? (
        <ErrorState error={queue.error} onRetry={() => void queue.refetch()} />
      ) : queue.data.items.length === 0 ? (
        <EmptyState
          title={status === "pending_approval" ? "Очередь пуста" : "Здесь пока ничего нет"}
          hint="Оффер появляется в очереди, когда вы запускаете «Оффер» на карточке канала и черновики готовы."
        />
      ) : (
        <>
          <div className="space-y-4">
            {queue.data.items.map((card) => (
              <QueueCardView key={card.offer.id} card={card} />
            ))}
          </div>
          <Pager offset={offset} limit={QUEUE_PAGE} total={queue.data.total} onChange={setOffset} />
        </>
      )}
    </div>
  );
}

function QueueCardView({ card }: { card: QueueCard }) {
  const { offer, channel, lead, thumbnail, analysis } = card;
  const usable = card.contacts.filter((c) => c.usable);
  return (
    <Card>
      <div className="grid gap-4 lg:grid-cols-[18rem_1fr]">
        <aside className="space-y-3 text-sm" aria-label={`Канал ${channel.title}`}>
          <div className="flex items-center gap-2">
            {channel.avatar_url ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={channel.avatar_url} alt="" referrerPolicy="no-referrer" className="size-8 rounded-full bg-zinc-200" />
            ) : null}
            <div className="min-w-0">
              <Link href={`/channels/${channel.id}`} className="block truncate font-semibold hover:underline">
                {channel.title}
              </Link>
              <p className="truncate text-xs text-zinc-500">
                {channel.handle ? `@${channel.handle.replace(/^@/, "")} · ` : ""}
                {channel.subscriber_count !== null ? `${formatCount(channel.subscriber_count)} подписчиков` : "подписчики скрыты"}
              </p>
            </div>
          </div>
          <div className="flex flex-wrap gap-1.5">
            <Badge tone={leadScoreTone(lead.lead_score)}>Lead {formatLeadScore(lead.lead_score)}</Badge>
            <Badge>приоритет {formatLeadScore(lead.priority_score)}</Badge>
            <Badge>{STATUS_LABELS[lead.status]}</Badge>
          </div>
          <figure>
            <figcaption className="mb-1 text-xs text-zinc-500">Текущее превью{thumbnail ? `: ${thumbnail.title}` : ""}</figcaption>
            {thumbnail?.image_url ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={thumbnail.image_url} alt={`Текущее превью: ${thumbnail.title}`} className="aspect-video w-full rounded object-cover" />
            ) : (
              <div className="flex aspect-video w-full items-center justify-center rounded bg-zinc-100 text-zinc-400 dark:bg-zinc-800">
                <ImageOff className="size-5" aria-hidden />
              </div>
            )}
          </figure>
          {offer.attachment?.image_url ? (
            <figure>
              <figcaption className="mb-1 text-xs text-zinc-500">Бесплатное превью (вариант {offer.attachment.label})</figcaption>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={offer.attachment.image_url} alt={`Сгенерированное превью, вариант ${offer.attachment.label}`} className="aspect-video w-full rounded object-cover" />
            </figure>
          ) : (
            <p className="text-xs text-zinc-500">Бесплатное превью не приложено.</p>
          )}
          {analysis ? (
            <div>
              <p className="text-xs font-medium text-zinc-500">AI-анализ</p>
              {analysis.summary ? <p className="mt-0.5">{analysis.summary}</p> : null}
              <div className="mt-1 flex flex-wrap gap-1">
                {analysis.problem_codes.map((c) => (
                  <Badge key={c} tone="amber">
                    {problemLabel(c)}
                  </Badge>
                ))}
              </div>
            </div>
          ) : null}
          <div>
            <p className="text-xs font-medium text-zinc-500">Контакты</p>
            {usable.length ? (
              <p className="mt-0.5">{usable.map((c) => `${CONTACT_TYPE_LABELS[c.type]}: ${c.value}`).join(" · ")}</p>
            ) : (
              <p className="mt-0.5 text-zinc-500">нет пригодных контактов</p>
            )}
          </div>
        </aside>
        <OfferEditor offer={offer} leadId={lead.id} contacts={card.contacts} samples={card.samples} />
      </div>
    </Card>
  );
}
