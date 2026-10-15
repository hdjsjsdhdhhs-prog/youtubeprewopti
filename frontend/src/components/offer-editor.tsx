"use client";

import { AlertTriangle, CalendarClock, Check, Loader2, RefreshCw, SkipForward, X } from "lucide-react";
import { useState } from "react";

import { errorMessage } from "@/components/states";
import { JobStatusBadge } from "@/components/status-badges";
import { Badge, Button, Input, Label, Select, Textarea } from "@/components/ui";
import { ApiError } from "@/lib/api/client";
import type { ContactItem } from "@/lib/api/contacts-hooks";
import {
  useDecideOffer,
  useEditOffer,
  useOfferEstimate,
  useScheduleOffer,
  useStartOffer,
  useUnscheduleOffer,
  type Offer,
  type OfferEdit,
  type OfferIn,
  type OfferVariant,
  type Sample,
} from "@/lib/api/offers-hooks";
import { CONTACT_TYPE_LABELS } from "@/lib/contacts";
import { formatDateTime } from "@/lib/format";
import {
  BLOCKER_LABELS,
  CHANNEL_LABELS,
  FACT_KIND_LABELS,
  ISSUE_LABELS,
  MAX_BODY,
  MAX_CTA,
  MAX_SUBJECT,
  OFFER_STATUS_LABELS,
  OFFER_STATUS_TONES,
  STYLE_LABELS,
  VARIANT_STATUS_LABELS,
  approveBlockers,
  canEditOffer,
  channelsFor,
  defaultScheduleInput,
  formatPersonalization,
  isOfferActive,
  localInputToIso,
  offerErrorMessage,
  personalizationTone,
  regenerateRequest,
  textChanges,
  textDraftErrors,
  type OutreachChannel,
  type TextDraft,
} from "@/lib/offers";
import { formatUsd } from "@/lib/prefilter";
import { cn } from "@/lib/utils";

export function OfferError({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <p role="alert" className="text-sm text-red-600">
      {offerErrorMessage(error) ?? errorMessage(error)}
    </p>
  );
}

const draftOf = (v: OfferVariant | undefined): TextDraft => ({ subject: v?.subject ?? "", body: v?.body ?? "", cta: v?.cta ?? "" });

/** One offer: drafts A / B / C, the editable text (ТЗ §23), guard issues, personalization and the data it used,
 * recipient / channel / attachment and the buttons of ТЗ §22. Approve confirms exactly the content on screen. */
export function OfferEditor({
  offer,
  leadId,
  contacts,
  samples,
}: {
  offer: Offer;
  leadId: number;
  contacts: ContactItem[];
  samples: Sample[];
}) {
  const edit = useEditOffer();
  const decide = useDecideOffer();
  const selected = offer.variants.find((v) => v.is_selected);
  const [draft, setDraft] = useState<TextDraft>(draftOf(selected));
  const [draftErrors, setDraftErrors] = useState<string[]>([]);
  const [comment, setComment] = useState("");
  // a new selection / saved text from the server replaces the local draft (state adjusted during render)
  const savedKey = `${selected?.id}:${selected?.subject}:${selected?.body}:${selected?.cta}`;
  const [shownKey, setShownKey] = useState(savedKey);
  if (shownKey !== savedKey) {
    setShownKey(savedKey);
    setDraft(draftOf(selected));
  }

  const editable = canEditOffer(offer.status);
  const changes = selected ? textChanges(draftOf(selected), draft) : {};
  const dirty = Object.keys(changes).length > 0;
  const blockers = approveBlockers(offer);
  const roundVariants = offer.variants.filter((v) => v.round === offer.round);
  const patch = (body: OfferEdit) => edit.mutate({ id: offer.id, body });

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={OFFER_STATUS_TONES[offer.status]}>{OFFER_STATUS_LABELS[offer.status]}</Badge>
        {offer.round > 1 ? <Badge>раунд {offer.round}</Badge> : null}
        {offer.manually_edited ? <Badge tone="violet">изменён вручную</Badge> : null}
        {offer.provider_is_mock ? (
          <Badge tone="amber" title="Черновики написаны mock-провайдером: это не настоящий текст модели">
            mock
          </Badge>
        ) : null}
        <Badge tone={personalizationTone(offer.personalization_score)} title="Персонализация: какие конкретные данные канала использованы и насколько текст уникален">
          персонализация {formatPersonalization(offer.personalization_score)}
        </Badge>
        {offer.scheduled_at ? <Badge tone="violet">отправка: {formatDateTime(offer.scheduled_at)}</Badge> : null}
        {offer.approval ? (
          <span className="text-xs text-zinc-500">
            последнее решение: {offer.approval.decision === "approved" ? "подтверждён" : offer.approval.decision === "rejected" ? "отклонён" : "отложен"}{" "}
            {formatDateTime(offer.approval.created_at)}
            {offer.approval.decision === "approved" && !offer.approval.is_current ? " · контент изменён после подтверждения" : ""}
          </span>
        ) : null}
      </div>

      {offer.status === "draft" ? (
        <p className="flex items-center gap-2 text-sm text-zinc-500">
          {isOfferActive(offer) ? <Loader2 className="size-4 animate-spin" aria-hidden /> : null}
          Черновики пишутся…
          {offer.job ? <JobStatusBadge status={offer.job.status} /> : null}
        </p>
      ) : null}
      {offer.error ? <p className="text-sm text-amber-700 dark:text-amber-400">{offer.error}</p> : null}

      {roundVariants.length ? (
        <div role="tablist" aria-label="Черновики" className="flex flex-wrap gap-1.5">
          {roundVariants.map((v) => (
            <button
              key={v.id}
              type="button"
              role="tab"
              aria-selected={v.is_selected}
              disabled={!editable || v.status !== "ready" || v.is_selected || edit.isPending}
              onClick={() => patch({ variant_id: v.id })}
              className={cn(
                "rounded-md border px-2.5 py-1 text-left text-xs",
                v.is_selected
                  ? "border-blue-500 bg-blue-50 dark:bg-blue-950/40"
                  : "border-zinc-200 hover:bg-zinc-50 disabled:opacity-60 dark:border-zinc-700 dark:hover:bg-zinc-800",
              )}
            >
              <b>{v.label}</b> · {STYLE_LABELS[v.style]}
              <span className="ml-1 text-zinc-500">
                {v.status === "ready" ? formatPersonalization(v.personalization_score) : VARIANT_STATUS_LABELS[v.status]}
              </span>
              {v.guard_issues.length ? <AlertTriangle className="ml-1 inline size-3 text-red-500" aria-label="есть проблемы в тексте" /> : null}
            </button>
          ))}
        </div>
      ) : null}

      {selected ? (
        <form
          className="space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            const errors = textDraftErrors(draft);
            setDraftErrors(errors);
            if (errors.length || !dirty) return;
            patch(changes);
          }}
        >
          <div>
            <Label htmlFor={`subject-${offer.id}`}>Тема</Label>
            <Input
              id={`subject-${offer.id}`}
              value={draft.subject}
              maxLength={MAX_SUBJECT}
              readOnly={!editable}
              onChange={(e) => setDraft({ ...draft, subject: e.currentTarget.value })}
            />
          </div>
          <div>
            <Label htmlFor={`body-${offer.id}`}>Текст сообщения</Label>
            <Textarea
              id={`body-${offer.id}`}
              value={draft.body}
              rows={8}
              maxLength={MAX_BODY}
              readOnly={!editable}
              className="font-sans"
              onChange={(e) => setDraft({ ...draft, body: e.currentTarget.value })}
            />
          </div>
          <div>
            <Label htmlFor={`cta-${offer.id}`}>CTA</Label>
            <Input
              id={`cta-${offer.id}`}
              value={draft.cta}
              maxLength={MAX_CTA}
              readOnly={!editable}
              onChange={(e) => setDraft({ ...draft, cta: e.currentTarget.value })}
            />
          </div>
          {draftErrors.map((m) => (
            <p key={m} className="text-sm text-red-600">
              {m}
            </p>
          ))}
          {editable ? (
            <div className="flex items-center gap-2">
              <Button type="submit" size="sm" variant="secondary" disabled={!dirty || edit.isPending}>
                Сохранить текст
              </Button>
              {dirty ? (
                <Button size="sm" variant="ghost" onClick={() => setDraft(draftOf(selected))}>
                  Отменить правки
                </Button>
              ) : null}
              {dirty ? <span className="text-xs text-zinc-500">несохранённые правки — оффер будет помечен «изменён вручную»</span> : null}
            </div>
          ) : null}
          <GuardIssues variant={selected} />
          <Sources variant={selected} />
        </form>
      ) : null}

      {editable ? (
        <Recipient offer={offer} contacts={contacts} samples={samples} onPatch={patch} pending={edit.isPending} />
      ) : null}
      <OfferError error={edit.error} />

      {offer.status === "pending_approval" ? (
        <div className="space-y-2 border-t border-zinc-100 pt-3 dark:border-zinc-800">
          {blockers.length ? (
            <ul className="space-y-0.5 text-sm text-amber-700 dark:text-amber-400" aria-label="Почему нельзя подтвердить">
              {blockers.map((b) => (
                <li key={b}>• {BLOCKER_LABELS[b]}</li>
              ))}
            </ul>
          ) : null}
          <div className="flex flex-wrap items-center gap-2">
            <Input
              aria-label="Комментарий к решению"
              placeholder="комментарий (необязательно)"
              value={comment}
              maxLength={500}
              onChange={(e) => setComment(e.currentTarget.value)}
              className="w-64"
            />
            <Button
              disabled={!offer.can_approve || dirty || decide.isPending}
              title={dirty ? "Сначала сохраните или отмените правки" : undefined}
              onClick={() => decide.mutate({ id: offer.id, decision: "approve", contentHash: offer.content_hash, comment })}
            >
              <Check className="size-4" aria-hidden /> Подтвердить
            </Button>
            <Button variant="secondary" disabled={decide.isPending} onClick={() => decide.mutate({ id: offer.id, decision: "skip", comment })}>
              <SkipForward className="size-4" aria-hidden /> Пропустить
            </Button>
            <Button variant="ghost" disabled={decide.isPending} onClick={() => decide.mutate({ id: offer.id, decision: "reject", comment })}>
              <X className="size-4" aria-hidden /> Отклонить
            </Button>
          </div>
          <p className="text-xs text-zinc-500">Подтверждение ничего не отправляет: отправка появится в следующей фазе и будет возможна только для подтверждённого текста.</p>
        </div>
      ) : null}
      {offer.status === "approved" || offer.status === "scheduled" ? (
        <ScheduleControls offer={offer} />
      ) : null}
      {offer.status === "approved" || offer.status === "scheduled" ? (
        <Button size="sm" variant="ghost" disabled={decide.isPending} onClick={() => decide.mutate({ id: offer.id, decision: "reject", comment })}>
          <X className="size-3.5" aria-hidden /> Отклонить
        </Button>
      ) : null}
      <OfferError error={decide.error} />

      {offer.status !== "draft" ? <Regenerate offer={offer} leadId={leadId} /> : null}
    </div>
  );
}

function GuardIssues({ variant }: { variant: OfferVariant }) {
  if (!variant.guard_issues.length) return null;
  return (
    <div role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm dark:bg-red-950/40">
      <p className="font-medium text-red-800 dark:text-red-300">Проверка текста: исправьте перед подтверждением</p>
      <ul className="mt-1 space-y-0.5 text-red-700 dark:text-red-300">
        {variant.guard_issues.map((i, n) => (
          <li key={`${i.code}-${n}`}>
            <Badge tone="red">{ISSUE_LABELS[i.code]}</Badge> {i.message}
          </li>
        ))}
      </ul>
    </div>
  );
}

function Sources({ variant }: { variant: OfferVariant }) {
  const counted = variant.personalization_sources.filter((s) => s.counted);
  const listed = variant.personalization_sources.filter((s) => !s.counted);
  return (
    <details className="text-sm">
      <summary className="cursor-pointer text-zinc-600 dark:text-zinc-400">
        Использованные данные канала: {counted.length}
        {variant.edited ? " · текст изменён вручную" : ""}
      </summary>
      {counted.length ? (
        <ul className="mt-1 space-y-0.5">
          {counted.map((s) => (
            <li key={s.fact_id}>
              <Badge>{FACT_KIND_LABELS[s.kind as keyof typeof FACT_KIND_LABELS] ?? s.kind}</Badge> {s.value}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-1 text-zinc-500">Конкретных данных канала в тексте не найдено.</p>
      )}
      {listed.length ? (
        <p className="mt-1 text-xs text-zinc-500">
          Модель указала, но в тексте их нет: {listed.map((s) => s.value).join("; ")}
        </p>
      ) : null}
    </details>
  );
}

function Recipient({
  offer,
  contacts,
  samples,
  onPatch,
  pending,
}: {
  offer: Offer;
  contacts: ContactItem[];
  samples: Sample[];
  onPatch: (body: OfferEdit) => void;
  pending: boolean;
}) {
  const shown = contacts.filter((c) => c.present || c.id === offer.contact?.id);
  const channels = channelsFor(offer.contact?.type);
  return (
    <div className="grid gap-2 sm:grid-cols-3">
      <div>
        <Label htmlFor={`contact-${offer.id}`}>Контакт</Label>
        <Select
          id={`contact-${offer.id}`}
          value={offer.contact?.id ?? ""}
          disabled={pending}
          onChange={(e) => onPatch({ contact_id: e.currentTarget.value ? Number(e.currentTarget.value) : null })}
        >
          <option value="">— не выбран —</option>
          {shown.map((c) => (
            <option key={c.id} value={c.id}>
              {CONTACT_TYPE_LABELS[c.type]}: {c.value}
              {c.usable ? "" : " (нельзя использовать)"}
            </option>
          ))}
        </Select>
        {!shown.length ? <p className="mt-1 text-xs text-zinc-500">У канала нет контактов — соберите или добавьте их в карточке «Контакты».</p> : null}
      </div>
      <div>
        <Label htmlFor={`channel-${offer.id}`}>Канал отправки</Label>
        <Select
          id={`channel-${offer.id}`}
          value={offer.channel ?? ""}
          disabled={pending || !offer.contact}
          onChange={(e) => onPatch({ channel: (e.currentTarget.value || null) as OutreachChannel | null })}
        >
          {!offer.channel ? <option value="">— не выбран —</option> : null}
          {offer.channel && !channels.includes(offer.channel) ? (
            <option value={offer.channel}>{CHANNEL_LABELS[offer.channel]} (не подходит)</option>
          ) : null}
          {channels.map((ch) => (
            <option key={ch} value={ch}>
              {CHANNEL_LABELS[ch]}
            </option>
          ))}
        </Select>
      </div>
      <div>
        <Label htmlFor={`attachment-${offer.id}`}>Бесплатное превью</Label>
        <Select
          id={`attachment-${offer.id}`}
          value={offer.attachment?.variant_id ?? ""}
          disabled={pending}
          onChange={(e) => onPatch({ attachment_variant_id: e.currentTarget.value ? Number(e.currentTarget.value) : null })}
        >
          <option value="">без вложения</option>
          {offer.attachment && !samples.some((s) => s.variant_id === offer.attachment?.variant_id) ? (
            <option value={offer.attachment.variant_id}>вариант {offer.attachment.label}</option>
          ) : null}
          {samples.map((s) => (
            <option key={s.variant_id} value={s.variant_id}>
              вариант {s.label}
              {s.status === "final" ? " (финальный)" : ""}
              {s.video_title ? ` — ${s.video_title}` : ""}
            </option>
          ))}
        </Select>
        {offer.attachment?.image_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={offer.attachment.image_url} alt={`Вложение: вариант ${offer.attachment.label}`} className="mt-1 aspect-video w-40 rounded object-cover" />
        ) : null}
      </div>
    </div>
  );
}

function ScheduleControls({ offer }: { offer: Offer }) {
  const schedule = useScheduleOffer();
  const unschedule = useUnscheduleOffer();
  const [when, setWhen] = useState(defaultScheduleInput);
  const iso = localInputToIso(when);
  return (
    <div className="space-y-1 border-t border-zinc-100 pt-3 dark:border-zinc-800">
      <div className="flex flex-wrap items-end gap-2">
        <div>
          <Label htmlFor={`when-${offer.id}`}>Когда отправить</Label>
          <Input id={`when-${offer.id}`} type="datetime-local" value={when} onChange={(e) => setWhen(e.currentTarget.value)} className="w-56" />
        </div>
        <Button
          variant="secondary"
          disabled={!iso || schedule.isPending}
          onClick={() => iso && schedule.mutate({ id: offer.id, scheduledAt: iso })}
        >
          <CalendarClock className="size-4" aria-hidden /> {offer.status === "scheduled" ? "Перенести" : "Запланировать"}
        </Button>
        {offer.status === "scheduled" ? (
          <Button variant="ghost" disabled={unschedule.isPending} onClick={() => unschedule.mutate(offer.id)}>
            Отменить план
          </Button>
        ) : null}
      </div>
      <p className="text-xs text-zinc-500">Сейчас время только сохраняется: отправка писем и сообщений — следующая фаза.</p>
      <OfferError error={schedule.error ?? unschedule.error} />
    </div>
  );
}

const isConfirmationError = (e: unknown) => e instanceof ApiError && e.code === "confirmation_required";

/** «Regenerate»: the same settings as the current round, estimate first, then the confirmed start. */
function Regenerate({ offer, leadId }: { offer: Offer; leadId: number }) {
  const estimate = useOfferEstimate(leadId);
  const start = useStartOffer(leadId);
  const [request, setRequest] = useState<OfferIn | null>(null);
  const run = (req: OfferIn) => {
    setRequest(req);
    estimate.mutate(req);
  };
  return (
    <div className="border-t border-zinc-100 pt-3 dark:border-zinc-800">
      {!estimate.data ? (
        <Button size="sm" variant="ghost" disabled={estimate.isPending} onClick={() => run(regenerateRequest(offer))}>
          <RefreshCw className="size-3.5" aria-hidden /> Перегенерировать
        </Button>
      ) : (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span>
            Новый раунд {estimate.data.round}: {estimate.data.variant_count} черн. · {formatUsd(estimate.data.estimated_cost_usd)}
            {estimate.data.within_budgets ? "" : " · превысит бюджет"}
          </span>
          <Button
            size="sm"
            disabled={start.isPending || !request}
            onClick={() =>
              request &&
              start.mutate(
                { ...request, confirm_token: estimate.data!.confirm_token },
                {
                  onSuccess: () => estimate.reset(),
                  onError: (err) => {
                    if (isConfirmationError(err)) run(request);
                  },
                },
              )
            }
          >
            Подтвердить и запустить
          </Button>
          <Button size="sm" variant="ghost" onClick={() => estimate.reset()}>
            Отмена
          </Button>
        </div>
      )}
      <OfferError error={estimate.error ?? start.error} />
    </div>
  );
}
