"use client";

import { Calculator, Play } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { OfferEditor, OfferError } from "@/components/offer-editor";
import { InlineError } from "@/components/states";
import { Badge, Button, Card, Input, Label, Select, Textarea } from "@/components/ui";
import { ApiError } from "@/lib/api/client";
import type { Lead } from "@/lib/api/hooks";
import {
  useLeadOffers,
  useOfferEstimate,
  useOfferTemplates,
  useStartOffer,
  type OfferEstimate,
  type OfferIn,
  type Sample,
} from "@/lib/api/offers-hooks";
import { STATUS_LABELS } from "@/lib/leads";
import {
  DEFAULT_OFFER_FORM,
  FACT_KIND_LABELS,
  MAX_INSTRUCTIONS,
  MAX_OFFER_VARIANTS,
  OFFER_STARTABLE,
  STYLE_LABELS,
  offerFormErrors,
  offerRequest,
  sameOfferRequest,
  type OfferForm,
} from "@/lib/offers";
import { formatUsd } from "@/lib/prefilter";

type LeadRef = Pick<Lead, "id" | "status">;

const isConfirmationError = (e: unknown) => e instanceof ApiError && e.code === "confirmation_required";
const OPEN = new Set(["draft", "pending_approval", "approved", "scheduled"]);

/** Phase 7 on the channel card: write a personalized offer (estimate → confirmed start) and work on it. */
export function LeadOffer({ lead }: { lead: LeadRef }) {
  const offers = useLeadOffers(lead.id);
  const latest = offers.data?.items[0];
  const open = latest && OPEN.has(latest.status) ? latest : undefined;
  return (
    <Card className="mt-4">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold">Оффер</h2>
        <span className="text-xs text-zinc-500">
          текст пишется только по реальным данным канала · ничего не отправляется без вашего подтверждения
        </span>
        <Link href="/approvals" className="text-xs text-zinc-500 hover:underline">
          очередь подтверждения
        </Link>
      </div>
      {offers.isPending ? (
        <p className="text-sm text-zinc-500">Загрузка…</p>
      ) : offers.isError ? (
        <InlineError error={offers.error} />
      ) : open ? (
        <OfferEditor offer={open} leadId={lead.id} contacts={offers.data.contacts} samples={offers.data.samples} />
      ) : (
        <>
          {latest ? (
            <p className="mb-2 text-sm text-zinc-500">
              Предыдущий оффер {latest.status === "rejected" ? "отклонён" : "не получился"} — можно написать заново.
            </p>
          ) : null}
          <OfferStartForm lead={lead} leadStatus={offers.data.lead_status} samples={offers.data.samples} />
        </>
      )}
    </Card>
  );
}

function OfferStartForm({ lead, leadStatus, samples }: { lead: LeadRef; leadStatus: Lead["status"]; samples: Sample[] }) {
  const templates = useOfferTemplates();
  const estimate = useOfferEstimate(lead.id);
  const start = useStartOffer(lead.id);
  const [form, setForm] = useState<OfferForm>(DEFAULT_OFFER_FORM);
  const [estimated, setEstimated] = useState<OfferIn | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const set = <K extends keyof OfferForm>(k: K, v: OfferForm[K]) => setForm((f) => ({ ...f, [k]: v }));
  const current = offerRequest(form);
  const stale = estimate.data !== undefined && !sameOfferRequest(current, estimated);
  const startable = OFFER_STARTABLE.has(leadStatus);

  const runEstimate = (req: OfferIn) => {
    setEstimated(req);
    estimate.mutate(req);
  };

  return (
    <form
      className="space-y-3"
      onSubmit={(e) => {
        e.preventDefault();
        const errs = offerFormErrors(form);
        setErrors(errs);
        if (errs.length) return;
        setNotice(null);
        start.reset();
        runEstimate(current);
      }}
    >
      {!startable ? (
        <p className="text-sm text-amber-700 dark:text-amber-400">
          В статусе «{STATUS_LABELS[leadStatus]}» оффер написать нельзя.
        </p>
      ) : null}
      <div className="grid gap-3 sm:grid-cols-4">
        <div>
          <Label htmlFor="offer-variants">Вариантов (A/B/C)</Label>
          <Select id="offer-variants" value={form.variant_count} onChange={(e) => set("variant_count", Number(e.currentTarget.value))}>
            {Array.from({ length: MAX_OFFER_VARIANTS }, (_, i) => i + 1).map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor="offer-template">Шаблон</Label>
          <Select
            id="offer-template"
            value={form.template_id ?? ""}
            onChange={(e) => set("template_id", e.currentTarget.value ? Number(e.currentTarget.value) : null)}
          >
            <option value="">без шаблона</option>
            {(templates.data ?? []).map((t) => (
              <option key={t.id} value={t.id}>
                {t.name} (v{t.version})
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor="offer-language">Язык</Label>
          <Input
            id="offer-language"
            value={form.language}
            placeholder="как у канала"
            maxLength={10}
            onChange={(e) => set("language", e.currentTarget.value.trim())}
          />
        </div>
        <div>
          <Label htmlFor="offer-attachment">Бесплатное превью</Label>
          <Select
            id="offer-attachment"
            value={String(form.attachment)}
            onChange={(e) => {
              const v = e.currentTarget.value;
              set("attachment", v === "default" || v === "none" ? v : Number(v));
            }}
          >
            <option value="default">финальный образец (если есть)</option>
            <option value="none">без вложения</option>
            {samples.map((s) => (
              <option key={s.variant_id} value={s.variant_id}>
                вариант {s.label}
                {s.status === "final" ? " (финальный)" : ""}
              </option>
            ))}
          </Select>
        </div>
      </div>
      <div>
        <Label htmlFor="offer-instructions">Ваши инструкции</Label>
        <Textarea
          id="offer-instructions"
          rows={2}
          maxLength={MAX_INSTRUCTIONS}
          value={form.instructions}
          placeholder="например: на «вы», упомянуть, что могу сделать серию"
          className="font-sans"
          onChange={(e) => set("instructions", e.currentTarget.value)}
        />
      </div>
      {errors.map((m) => (
        <p key={m} className="text-sm text-red-600">
          {m}
        </p>
      ))}
      <Button type="submit" variant="secondary" disabled={estimate.isPending || !startable}>
        <Calculator className="size-4" aria-hidden /> Оценить
      </Button>
      {notice ? <p className="text-sm text-amber-700 dark:text-amber-400">{notice}</p> : null}
      {estimate.data ? (
        <EstimateView
          estimate={estimate.data}
          stale={stale}
          pending={start.isPending}
          onConfirm={() => {
            if (!estimated || !estimate.data) return;
            start.mutate(
              { ...estimated, confirm_token: estimate.data.confirm_token },
              {
                onSuccess: () => {
                  estimate.reset();
                  setEstimated(null);
                },
                onError: (err) => {
                  if (!isConfirmationError(err)) return;
                  setNotice("Данные канала, модель или цена изменились после оценки. Проверьте новую оценку и подтвердите снова.");
                  runEstimate(estimated);
                },
              },
            );
          }}
        />
      ) : null}
      <OfferError error={estimate.error ?? start.error} />
    </form>
  );
}

function EstimateView({
  estimate: e,
  stale,
  pending,
  onConfirm,
}: {
  estimate: OfferEstimate;
  stale: boolean;
  pending: boolean;
  onConfirm: () => void;
}) {
  return (
    <div className="space-y-2 rounded-md border border-zinc-200 p-3 text-sm dark:border-zinc-700">
      <p>
        {e.round > 1 ? `Новый раунд ${e.round} оффера. ` : ""}
        Черновики: {e.styles.map((s) => STYLE_LABELS[s]).join(" · ")} · модель {e.model_key ?? "не настроена"}
        {e.provider === "mock" ? <Badge tone="amber">mock</Badge> : null} · {formatUsd(e.estimated_cost_usd)}
        {e.pricing_verified ? "" : " (цена не проверена)"}
      </p>
      {!e.within_budgets ? <p className="text-amber-700 dark:text-amber-400">Запуск превысит бюджет AI.</p> : null}
      <div>
        <p className="font-medium">Факты, на которые может опираться текст ({e.inputs.facts.length})</p>
        <ul className="mt-1 max-h-48 space-y-0.5 overflow-auto">
          {e.inputs.facts.map((f) => (
            <li key={f.id}>
              <span className="font-mono text-xs text-zinc-500">F{f.id}</span> <Badge>{FACT_KIND_LABELS[f.kind]}</Badge> {f.value}
            </li>
          ))}
        </ul>
        {e.inputs.sample ? null : <p className="mt-1 text-xs text-zinc-500">Бесплатное превью не приложено.</p>}
      </div>
      <details>
        <summary className="cursor-pointer text-zinc-600 dark:text-zinc-400">Промпт варианта A</summary>
        <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded bg-zinc-50 p-2 text-xs dark:bg-zinc-950">{e.prompt_preview}</pre>
      </details>
      {stale ? <p className="text-amber-700 dark:text-amber-400">Параметры изменились — оцените заново.</p> : null}
      <Button disabled={stale || pending || !e.model_key} onClick={onConfirm}>
        <Play className="size-4" aria-hidden /> Подтвердить и написать
      </Button>
    </div>
  );
}
