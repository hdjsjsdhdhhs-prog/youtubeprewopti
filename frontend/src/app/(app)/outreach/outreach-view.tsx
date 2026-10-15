"use client";

import Link from "next/link";
import { useState } from "react";

import { MessagePanel } from "@/components/outreach-message";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Card, PageHeader, Pager } from "@/components/ui";
import { MESSAGES_PAGE, useMessages } from "@/lib/api/outreach-hooks";
import { MESSAGE_STATUS_LABELS, MESSAGE_TABS, type MessageStatus } from "@/lib/outreach";
import { cn } from "@/lib/utils";

const EMPTY_HINT: Partial<Record<MessageStatus, string>> = {
  queued: "Письмо попадает сюда после «Отправить сейчас» или «Запланировать» у подтверждённого оффера.",
  manual: "Сообщения для Telegram, VK и ручного канала ждут здесь, пока вы не отправите их сами.",
};

/** ТЗ §24–27: every outgoing message — queued, waiting for the operator, sent (with what happened after), failed
 * or cancelled. Nothing is created here: a message comes only from an approved offer. */
export function OutreachView() {
  const [status, setStatus] = useState<MessageStatus>("queued");
  const [offset, setOffset] = useState(0);
  const list = useMessages({ status, offset });
  const counts = list.data?.counts;

  return (
    <div className="max-w-5xl">
      <PageHeader
        title="Рассылка"
        subtitle="Сообщения из подтверждённых офферов: очередь, ручная отправка, отправленные и что было после"
        actions={
          <Link href="/email-accounts" className="text-sm text-zinc-500 hover:underline">
            почтовые аккаунты
          </Link>
        }
      />
      <div role="tablist" aria-label="Статус сообщений" className="mb-4 flex flex-wrap gap-1">
        {MESSAGE_TABS.map((s) => (
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
            {MESSAGE_STATUS_LABELS[s]}
            {counts ? <span className="ml-1 opacity-70">{counts[s] ?? 0}</span> : null}
          </button>
        ))}
      </div>
      {list.isPending ? (
        <LoadingState />
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => void list.refetch()} />
      ) : list.data.items.length === 0 ? (
        <EmptyState title="Здесь пока ничего нет" hint={EMPTY_HINT[status]} />
      ) : (
        <>
          <div className="space-y-3">
            {list.data.items.map((m) => (
              <Card key={m.id}>
                <p className="mb-2 text-sm">
                  <Link href={`/channels/${m.channel_id}`} className="font-semibold hover:underline">
                    {m.channel_title || `канал ${m.channel_id}`}
                  </Link>
                  <span className="text-zinc-500"> · оффер #{m.offer_id}</span>
                </p>
                <MessagePanel message={m} />
              </Card>
            ))}
          </div>
          <Pager offset={offset} limit={MESSAGES_PAGE} total={list.data.total} onChange={setOffset} />
        </>
      )}
    </div>
  );
}
