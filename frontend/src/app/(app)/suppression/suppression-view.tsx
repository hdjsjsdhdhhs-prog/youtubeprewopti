"use client";

import { Trash2 } from "lucide-react";
import { useState } from "react";

import { EmptyState, ErrorState, InlineError, LoadingState } from "@/components/states";
import { Badge, Button, Card, Input, Label, PageHeader, Pager, Select } from "@/components/ui";
import {
  SUPPRESSION_PAGE,
  useAddSuppression,
  useRemoveSuppression,
  useSuppressionList,
} from "@/lib/api/contacts-hooks";
import { useMe } from "@/lib/api/hooks";
import {
  CONTACT_TYPES,
  CONTACT_TYPE_LABELS,
  SUPPRESSION_REASONS,
  SUPPRESSION_REASON_LABELS,
  type ContactType,
} from "@/lib/contacts";
import { formatDateTime } from "@/lib/format";

/** ТЗ §18–19: values that must never be contacted (opt-out, bounce, complaint, manual). Contacts with such a
 * value are not usable for Contactability, and from the outreach phase on nothing is sent to them. */
export function SuppressionView() {
  const me = useMe();
  const isAdmin = me.data ? ["owner", "admin"].includes(me.data.role) : false;
  const [offset, setOffset] = useState(0);
  const list = useSuppressionList(offset);
  const add = useAddSuppression();
  const remove = useRemoveSuppression();
  const [type, setType] = useState<ContactType>("email");
  const [value, setValue] = useState("");
  const [reason, setReason] = useState<(typeof SUPPRESSION_REASONS)[number]>("manual");

  return (
    <div className="max-w-4xl">
      <PageHeader
        title="Не писать"
        subtitle="Контакты, по которым нельзя связываться: отказ, недоставка, жалоба или ваше решение. Действует во всём workspace"
      />
      <Card className="mb-4">
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (!value.trim()) return;
            add.mutate({ type, value: value.trim(), reason, note: "" }, { onSuccess: () => setValue("") });
          }}
        >
          <div>
            <Label htmlFor="sup-type">Тип</Label>
            <Select id="sup-type" value={type} onChange={(e) => setType(e.currentTarget.value as ContactType)} className="w-40">
              {CONTACT_TYPES.map((t) => (
                <option key={t} value={t}>
                  {CONTACT_TYPE_LABELS[t]}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label htmlFor="sup-value">Значение</Label>
            <Input
              id="sup-value"
              value={value}
              maxLength={320}
              placeholder="почта, @username или ссылка"
              onChange={(e) => setValue(e.currentTarget.value)}
              className="w-72"
            />
          </div>
          <div>
            <Label htmlFor="sup-reason">Причина</Label>
            <Select
              id="sup-reason"
              value={reason}
              onChange={(e) => setReason(e.currentTarget.value as (typeof SUPPRESSION_REASONS)[number])}
              className="w-52"
            >
              {SUPPRESSION_REASONS.map((r) => (
                <option key={r} value={r}>
                  {SUPPRESSION_REASON_LABELS[r]}
                </option>
              ))}
            </Select>
          </div>
          <Button type="submit" disabled={add.isPending || !value.trim()}>
            Добавить
          </Button>
        </form>
        <div className="mt-2">
          <InlineError error={add.error} />
        </div>
      </Card>

      {list.isPending ? (
        <LoadingState />
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => void list.refetch()} />
      ) : list.data.items.length === 0 ? (
        <EmptyState
          title="Список пуст"
          hint="Контакт попадёт сюда, когда вы отметите «Отказался» на карточке канала или добавите значение выше."
        />
      ) : (
        <>
          <div className="overflow-x-auto rounded-lg border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
            <table className="w-full text-sm">
              <thead className="bg-zinc-50 text-left text-xs uppercase tracking-wide text-zinc-500 dark:bg-zinc-900">
                <tr>
                  <th scope="col" className="px-3 py-2 font-medium">Тип</th>
                  <th scope="col" className="px-3 py-2 font-medium">Значение</th>
                  <th scope="col" className="px-3 py-2 font-medium">Причина</th>
                  <th scope="col" className="px-3 py-2 font-medium">Добавлено</th>
                  <th scope="col" className="w-12 px-3 py-2" />
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
                {list.data.items.map((e) => (
                  <tr key={e.id}>
                    <td className="px-3 py-2">
                      <Badge>{CONTACT_TYPE_LABELS[e.type]}</Badge>
                    </td>
                    <td className="px-3 py-2 font-medium break-all">{e.value_normalized}</td>
                    <td className="px-3 py-2">{SUPPRESSION_REASON_LABELS[e.reason]}</td>
                    <td className="px-3 py-2 whitespace-nowrap text-zinc-500">{formatDateTime(e.created_at)}</td>
                    <td className="px-3 py-2 text-right">
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={!isAdmin || remove.isPending}
                        title={isAdmin ? "Убрать из списка (действие администратора, попадает в аудит)" : "Убрать из списка может администратор"}
                        aria-label={`Убрать ${e.value_normalized} из списка`}
                        onClick={() => remove.mutate(e.id)}
                      >
                        <Trash2 className="size-3.5" aria-hidden />
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <InlineError error={remove.error} />
          <Pager offset={offset} limit={SUPPRESSION_PAGE} total={list.data.total} onChange={setOffset} />
        </>
      )}
    </div>
  );
}
