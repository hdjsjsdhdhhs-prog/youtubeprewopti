"use client";

import { Ban, CheckCheck, Loader2, MessageSquarePlus } from "lucide-react";
import { useState } from "react";

import { errorMessage } from "@/components/states";
import { Badge, Button, Input, Select } from "@/components/ui";
import {
  useCancelMessage,
  useMarkMessageSent,
  useRecordMessageEvent,
  type Message,
} from "@/lib/api/outreach-hooks";
import { CONTACT_TYPE_LABELS, type ContactType } from "@/lib/contacts";
import { formatDateTime } from "@/lib/format";
import { CHANNEL_LABELS, offerErrorMessage } from "@/lib/offers";
import {
  MANUAL_EVENTS,
  MESSAGE_EVENT_LABELS,
  MESSAGE_STATUS_LABELS,
  MESSAGE_STATUS_TONES,
  SUPPRESSING_EVENTS,
  canCancelMessage,
  type ManualEventType,
} from "@/lib/outreach";

function MessageError({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <p role="alert" className="text-sm text-red-600">
      {offerErrorMessage(error) ?? errorMessage(error)}
    </p>
  );
}

const contactLabel = (type: string) => CONTACT_TYPE_LABELS[type as ContactType] ?? type;

/** One outgoing message (ТЗ §24–27): exactly what is / was sent, its timeline and the operator's actions —
 * cancel (queued / manual), «sent» for a manual channel, and what happened after the send. */
export function MessagePanel({ message: m, cancellable = true }: { message: Message; cancellable?: boolean }) {
  const cancel = useCancelMessage();
  const markSent = useMarkMessageSent();
  const record = useRecordMessageEvent();
  const [note, setNote] = useState("");
  const [event, setEvent] = useState<ManualEventType>("replied");
  const manual = m.status === "manual";
  const canCancel = cancellable && canCancelMessage(m.status);
  const error = cancel.error ?? markSent.error ?? record.error;

  return (
    <div className="space-y-2 rounded-md border border-zinc-200 p-3 text-sm dark:border-zinc-700" aria-label={`Сообщение ${m.id}`}>
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={MESSAGE_STATUS_TONES[m.status]}>{MESSAGE_STATUS_LABELS[m.status]}</Badge>
        {m.status === "sending" ? <Loader2 className="size-3.5 animate-spin text-zinc-500" aria-hidden /> : null}
        <span>
          {CHANNEL_LABELS[m.channel]} → <b className="break-all">{m.to_address}</b>
          <span className="text-zinc-500"> ({contactLabel(m.contact_type)})</span>
        </span>
        {m.email_account_name ? <span className="text-xs text-zinc-500">с аккаунта «{m.email_account_name}»</span> : null}
      </div>
      <p className="text-xs text-zinc-500">
        {m.sent_at
          ? `отправлено ${formatDateTime(m.sent_at)}`
          : m.status === "queued"
            ? `отправка не раньше ${formatDateTime(m.next_attempt_at ?? m.scheduled_at)} · с учётом паузы и дневного лимита аккаунта`
            : manual
              ? "отправьте этот текст сами и отметьте «Отправлено»"
              : `создано ${formatDateTime(m.created_at)}`}
        {m.attempts > 1 ? ` · попыток: ${m.attempts}` : ""}
      </p>
      {m.error_message ? (
        <p className="text-sm text-red-700 dark:text-red-400">
          {m.error_message}
          {m.error_code ? <span className="ml-1 font-mono text-xs opacity-70">{m.error_code}</span> : null}
        </p>
      ) : null}

      <details open={manual}>
        <summary className="cursor-pointer text-zinc-600 dark:text-zinc-400">Текст сообщения (ровно то, что уходит)</summary>
        {m.subject ? (
          <p className="mt-1">
            <span className="text-zinc-500">Тема:</span> {m.subject}
          </p>
        ) : null}
        <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded bg-zinc-50 p-2 font-sans text-sm dark:bg-zinc-950">{m.body}</pre>
        {m.attachment_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={m.attachment_url} alt="Вложение: бесплатное превью" className="mt-1 aspect-video w-40 rounded object-cover" />
        ) : null}
      </details>

      {m.events.length ? (
        <ol className="space-y-0.5 text-xs text-zinc-600 dark:text-zinc-400" aria-label="История сообщения">
          {m.events.map((e) => (
            <li key={e.id}>
              {formatDateTime(e.occurred_at)} · <b>{MESSAGE_EVENT_LABELS[e.type]}</b>
              {e.note ? ` — ${e.note}` : ""}
            </li>
          ))}
        </ol>
      ) : null}

      {canCancel || manual || m.status === "sent" ? (
        <div className="flex flex-wrap items-center gap-2 border-t border-zinc-100 pt-2 dark:border-zinc-800">
          <Input
            aria-label="Заметка"
            placeholder="заметка (необязательно)"
            value={note}
            maxLength={1000}
            onChange={(e) => setNote(e.currentTarget.value)}
            className="h-7 w-56 text-xs"
          />
          {manual ? (
            <Button size="sm" disabled={markSent.isPending} onClick={() => markSent.mutate({ id: m.id, note }, { onSuccess: () => setNote("") })}>
              <CheckCheck className="size-3.5" aria-hidden /> Отправлено
            </Button>
          ) : null}
          {m.status === "sent" ? (
            <>
              <Select
                aria-label="Что произошло"
                value={event}
                onChange={(e) => setEvent(e.currentTarget.value as ManualEventType)}
                className="h-7 w-52 text-xs"
              >
                {MANUAL_EVENTS.map((t) => (
                  <option key={t} value={t}>
                    {MESSAGE_EVENT_LABELS[t]}
                  </option>
                ))}
              </Select>
              <Button
                size="sm"
                variant="secondary"
                disabled={record.isPending}
                onClick={() => record.mutate({ id: m.id, type: event, note }, { onSuccess: () => setNote("") })}
              >
                <MessageSquarePlus className="size-3.5" aria-hidden /> Записать
              </Button>
              {SUPPRESSING_EVENTS.has(event) ? (
                <span className="text-xs text-amber-700 dark:text-amber-400">адрес попадёт в список «Не писать»</span>
              ) : null}
            </>
          ) : null}
          {canCancel ? (
            <Button size="sm" variant="ghost" disabled={cancel.isPending} onClick={() => cancel.mutate(m.id)}>
              <Ban className="size-3.5" aria-hidden /> Отменить отправку
            </Button>
          ) : null}
        </div>
      ) : null}
      <MessageError error={error} />
    </div>
  );
}
