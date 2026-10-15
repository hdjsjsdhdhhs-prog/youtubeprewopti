"use client";

import { Ban, Loader2, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useState } from "react";

import { InlineError } from "@/components/states";
import { Badge, Button, Card, Input, Select } from "@/components/ui";
import {
  useAddContact,
  useChannelContacts,
  useDeleteContact,
  useScanChannelContacts,
  useSetContactStatus,
  type ContactItem,
} from "@/lib/api/contacts-hooks";
import {
  CONTACT_STATUS_LABELS,
  CONTACT_TYPES,
  CONTACT_TYPE_LABELS,
  SCAN_STATUS_LABELS,
  SETTABLE_STATUSES,
  SOURCE_LABELS,
  contactHref,
  contactabilityTone,
  contactsSummary,
  formatContactScore,
  type ContactType,
} from "@/lib/contacts";
import { formatDateTime } from "@/lib/format";

function statusTone(c: ContactItem): "green" | "red" | "amber" | "neutral" {
  if (!c.usable) return "red";
  return c.status === "verified" ? "green" : c.status === "unverified" ? "amber" : "neutral";
}

/** Contacts of a channel (ТЗ §18–19): found in its own texts (public) and added by hand; the workspace's
 * verdicts; the Contactability Score. Only public data is collected, nothing is fetched from the network. */
export function ChannelContactsCard({ channelId }: { channelId: number }) {
  const contacts = useChannelContacts(channelId);
  const scan = useScanChannelContacts(channelId);
  const data = contacts.data;

  return (
    <Card className="mt-3">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold">Контакты</h2>
        {data ? (
          <>
            <Badge tone={data.status === "found" ? "green" : "neutral"}>{SCAN_STATUS_LABELS[data.status]}</Badge>
            <Badge tone={contactabilityTone(data.contactability_score)} title="Contactability Score (0–100)">
              Контактность: {formatContactScore(data.contactability_score)}
            </Badge>
          </>
        ) : null}
        <div className="ml-auto flex items-center gap-2">
          {data?.scan ? (
            <span className="text-xs text-zinc-500" title={`Просмотрено текстов: ${data.scan.texts_scanned}`}>
              Собрано: {formatDateTime(data.scan.scanned_at)}
            </span>
          ) : null}
          <Button
            size="sm"
            variant="secondary"
            disabled={scan.isPending}
            title="Описание канала и описания последних сохранённых видео; бесплатно, без запросов в сеть"
            onClick={() => scan.mutate()}
          >
            {scan.isPending ? (
              <Loader2 className="size-3.5 animate-spin" aria-hidden />
            ) : (
              <RefreshCw className="size-3.5" aria-hidden />
            )}
            {data?.scan ? "Собрать заново" : "Собрать контакты"}
          </Button>
        </div>
      </div>
      <InlineError error={scan.error} />

      {contacts.isPending ? (
        <p className="mt-2 text-sm text-zinc-500">Загрузка…</p>
      ) : contacts.isError ? (
        <InlineError error={contacts.error} />
      ) : (
        <>
          <p className="mt-1 text-xs text-zinc-500">{contactsSummary(contacts.data)}</p>
          {contacts.data.items.length > 0 ? (
            <ul className="mt-2 divide-y divide-zinc-100 dark:divide-zinc-800">
              {contacts.data.items.map((c) => (
                <ContactRow key={c.id} channelId={channelId} contact={c} />
              ))}
            </ul>
          ) : contacts.data.status === "not_found" ? (
            <p className="mt-2 text-sm text-zinc-500">
              В описаниях канала и видео нет публичных контактов. Если нашли контакт сами — добавьте его ниже.
            </p>
          ) : null}
          <AddContact channelId={channelId} />
        </>
      )}
    </Card>
  );
}

function ContactRow({ channelId, contact: c }: { channelId: number; contact: ContactItem }) {
  const setStatus = useSetContactStatus(channelId);
  const remove = useDeleteContact(channelId);
  const href = contactHref(c.type, c.value_normalized, c.value);
  const busy = setStatus.isPending || remove.isPending;
  const optedOut = c.status === "opted_out" || c.suppressed;

  return (
    <li className="py-2">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Badge>{CONTACT_TYPE_LABELS[c.type]}</Badge>
        {href ? (
          <a
            href={href}
            target="_blank"
            rel="noopener noreferrer nofollow"
            className={`break-all font-medium hover:underline ${c.usable ? "" : "text-zinc-400 line-through"}`}
          >
            {c.value}
          </a>
        ) : (
          <span className={`break-all font-medium ${c.usable ? "" : "text-zinc-400 line-through"}`}>{c.value}</span>
        )}
        <Badge tone={statusTone(c)}>{c.suppressed ? "в списке подавления" : CONTACT_STATUS_LABELS[c.status]}</Badge>
        {!c.present ? <Badge tone="amber" title="Контакта больше нет в текстах канала">пропал из текстов</Badge> : null}
        {c.is_manual ? <Badge tone="blue">вручную</Badge> : null}
      </div>
      <p className="mt-0.5 text-xs text-zinc-500">
        {SOURCE_LABELS[c.source]}
        {c.source !== "manual" ? ` · уверенность ${Math.round(Number(c.confidence) * 100)}%` : null}
        {c.seen_in_videos > 0 ? ` · в ${c.seen_in_videos} видео` : null}
        {c.note ? ` · ${c.note}` : null}
      </p>
      <div className="mt-1 flex flex-wrap items-center gap-1.5">
        {optedOut ? (
          <span className="text-xs text-zinc-500">
            Отказался от общения: снять отметку может администратор в разделе «Не писать».
          </span>
        ) : (
          <>
            {SETTABLE_STATUSES.filter((s) => s !== c.status).map((s) => (
              <Button
                key={s}
                size="sm"
                variant="ghost"
                disabled={busy}
                onClick={() => setStatus.mutate({ id: c.id, body: { status: s, note: "" } })}
              >
                {s === "verified" ? "Подтвердить" : s === "invalid" ? "Не работает" : "Сбросить проверку"}
              </Button>
            ))}
            <Button
              size="sm"
              variant="ghost"
              disabled={busy}
              title="Больше не писать по этому контакту: попадает в список подавления и необратимо для канала"
              onClick={() => setStatus.mutate({ id: c.id, body: { status: "opted_out", note: "" } })}
            >
              <Ban className="size-3.5" aria-hidden /> Отказался
            </Button>
            {c.is_manual ? (
              <Button size="sm" variant="ghost" disabled={busy} aria-label="Удалить контакт" onClick={() => remove.mutate(c.id)}>
                <Trash2 className="size-3.5" aria-hidden />
              </Button>
            ) : null}
          </>
        )}
      </div>
      <InlineError error={setStatus.error ?? remove.error} />
    </li>
  );
}

function AddContact({ channelId }: { channelId: number }) {
  const add = useAddContact(channelId);
  const [type, setType] = useState<ContactType>("telegram");
  const [value, setValue] = useState("");
  return (
    <form
      className="mt-3 flex flex-wrap items-center gap-2 border-t border-zinc-100 pt-3 dark:border-zinc-800"
      onSubmit={(e) => {
        e.preventDefault();
        if (!value.trim()) return;
        add.mutate({ type, value: value.trim(), status: "unverified", note: "" }, { onSuccess: () => setValue("") });
      }}
    >
      <Select
        aria-label="Тип контакта"
        value={type}
        onChange={(e) => setType(e.currentTarget.value as ContactType)}
        className="h-8 w-40"
      >
        {CONTACT_TYPES.map((t) => (
          <option key={t} value={t}>
            {CONTACT_TYPE_LABELS[t]}
          </option>
        ))}
      </Select>
      <Input
        aria-label="Значение контакта"
        placeholder="@username, почта, ссылка"
        value={value}
        maxLength={320}
        onChange={(e) => setValue(e.currentTarget.value)}
        className="h-8 w-72"
      />
      <Button size="sm" type="submit" variant="secondary" disabled={add.isPending || !value.trim()}>
        <Plus className="size-3.5" aria-hidden /> Добавить контакт
      </Button>
      <InlineError error={add.error} />
    </form>
  );
}
