"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Calculator, Check, Copy, ImageOff, Loader2, Play, Sparkles, Star, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { InlineError, errorMessage } from "@/components/states";
import { JobStatusBadge } from "@/components/status-badges";
import { Badge, Button, Card, Label, Select, Textarea } from "@/components/ui";
import { ApiError } from "@/lib/api/client";
import {
  invalidateAfterGeneration,
  useDecideVariant,
  useGenerationEstimate,
  useLeadGenerations,
  useReferences,
  useStartGeneration,
  type ComparisonOriginal,
  type Generation,
  type GenerationEstimate,
  type GenerationIn,
  type GenerationStartResult,
  type GenerationVariant,
  type LeadGenerations,
} from "@/lib/api/generation-hooks";
import type { Lead } from "@/lib/api/hooks";
import { formatScore, problemLabel, scoreTone } from "@/lib/analysis";
import { formatDateTime } from "@/lib/format";
import {
  DECISION_LABELS,
  DEFAULT_GENERATION_FORM,
  GENERATION_STARTABLE,
  GENERATION_STATUS_LABELS,
  MAX_INSTRUCTIONS,
  MAX_REFERENCES,
  MAX_VARIANTS,
  QUALITY_LABELS,
  SIZE_LABELS,
  VARIANT_STATUS_LABELS,
  VARIANT_STATUS_TONES,
  canMakeMoreLike,
  generationErrorMessage,
  generationFormErrors,
  generationRequest,
  isGenerationActive,
  originalProblems,
  sameRequest,
  strategyLabel,
  variantDecisions,
  type GenerationForm,
} from "@/lib/generation";
import { STATUS_LABELS } from "@/lib/leads";
import { formatUsd } from "@/lib/prefilter";
import { cn } from "@/lib/utils";

type LeadRef = Pick<Lead, "id" | "status">;

const isConfirmationError = (e: unknown) => e instanceof ApiError && e.code === "confirmation_required";

function GenerationError({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <p role="alert" className="text-sm text-red-600">
      {generationErrorMessage(error) ?? errorMessage(error)}
    </p>
  );
}

/** Phase 5 on the lead card: the generation form (estimate → confirmed start) and «Original vs Generated». */
export function LeadGeneration({ lead }: { lead: LeadRef }) {
  const qc = useQueryClient();
  const [form, setForm] = useState<GenerationForm>(DEFAULT_GENERATION_FORM);
  const formRef = useRef<HTMLDivElement>(null);
  const generations = useLeadGenerations(lead.id);

  // A finished run moves the lead to generated / back to selected: refresh lead lists, cards and jobs.
  const activeKey = (generations.data?.items ?? [])
    .filter(isGenerationActive)
    .map((g) => g.id)
    .join(",");
  const previous = useRef(activeKey);
  useEffect(() => {
    if (previous.current && previous.current !== activeKey) void invalidateAfterGeneration(qc, lead.id);
    previous.current = activeKey;
  }, [activeKey, lead.id, qc]);

  const makeMoreLike = (v: GenerationVariant) => {
    setForm((f) => ({ ...f, parent_variant_id: v.id }));
    formRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
  };

  return (
    <Card className="mt-4">
      <div ref={formRef} className="mb-2 flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold">Генерация превью</h2>
        <span className="text-xs text-zinc-500">
          варианты исправляют проблемы из аудита превью и канала · оценка бесплатна, запуск — только после подтверждения
        </span>
      </div>
      <GenerationFormView lead={lead} form={form} setForm={setForm} data={generations.data} />
      <div className="mt-4 border-t border-zinc-100 pt-3 dark:border-zinc-800">
        {generations.isPending ? (
          <p className="text-sm text-zinc-500">Загрузка…</p>
        ) : generations.isError ? (
          <InlineError error={generations.error} />
        ) : (
          <GenerationResults leadId={lead.id} data={generations.data} onMakeMoreLike={makeMoreLike} />
        )}
      </div>
    </Card>
  );
}

// --- the form -----------------------------------------------------------------------------------

function findVariant(data: LeadGenerations | undefined, id: number | null): GenerationVariant | undefined {
  if (id === null || !data) return undefined;
  for (const g of data.items) {
    const v = g.variants.find((x) => x.id === id);
    if (v) return v;
  }
  return undefined;
}

function GenerationFormView({
  lead,
  form,
  setForm,
  data,
}: {
  lead: LeadRef;
  form: GenerationForm;
  setForm: React.Dispatch<React.SetStateAction<GenerationForm>>;
  data: LeadGenerations | undefined;
}) {
  const estimate = useGenerationEstimate(lead.id);
  const start = useStartGeneration(lead.id);
  const references = useReferences();
  // The request of the estimate on screen: the start confirms exactly that one, not later form edits.
  const [estimated, setEstimated] = useState<GenerationIn | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [formErrors, setFormErrors] = useState<string[]>([]);
  const set = <K extends keyof GenerationForm>(key: K, value: GenerationForm[K]) => setForm((f) => ({ ...f, [key]: value }));

  const current = generationRequest(form);
  const stale = estimate.data !== undefined && estimated !== null && !sameRequest(current, estimated);
  const parent = findVariant(data, form.parent_variant_id);
  const running = (data?.items ?? []).some(isGenerationActive);
  const startable = GENERATION_STARTABLE.has(data?.lead_status ?? lead.status);

  const runEstimate = (req: GenerationIn) => {
    setEstimated(req);
    estimate.mutate(req);
  };
  const confirmAndStart = () => {
    if (!estimate.data || !estimated) return;
    setNotice(null);
    start.mutate(
      { ...estimated, confirm_token: estimate.data.confirm_token },
      {
        onSuccess: () => {
          estimate.reset();
          setEstimated(null);
        },
        onError: (err) => {
          if (!isConfirmationError(err)) return;
          setNotice("Данные, модель, цена или бюджет изменились после оценки. Проверьте новую оценку и подтвердите снова.");
          runEstimate(estimated);
        },
      },
    );
  };

  const toggleReference = (id: number, on: boolean) =>
    set("reference_ids", on ? [...form.reference_ids, id] : form.reference_ids.filter((r) => r !== id));
  const candidates = estimate.data?.source_candidates ?? [];
  const models = estimate.data?.models ?? [];
  const refs = references.data ?? [];

  return (
    <form
      className="space-y-3"
      onSubmit={(e) => {
        e.preventDefault();
        const errors = generationFormErrors(form);
        setFormErrors(errors);
        if (errors.length) return;
        setNotice(null);
        start.reset();
        runEstimate(current);
      }}
    >
      {parent ? (
        <p className="flex flex-wrap items-center gap-2 rounded-md bg-violet-50 px-3 py-2 text-sm dark:bg-violet-950/40">
          <Copy className="size-4 text-violet-600" aria-hidden />
          «Ещё такие же» на основе варианта <b>{parent.label}</b> ({strategyLabel(parent.strategy)}): он заменит текущее
          превью как основа.
          <Button size="sm" variant="ghost" onClick={() => set("parent_variant_id", null)}>
            <X className="size-3.5" aria-hidden /> Убрать
          </Button>
        </p>
      ) : null}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div>
          <Label htmlFor="gen-count">Вариантов</Label>
          <Select id="gen-count" value={form.variant_count} onChange={(e) => set("variant_count", Number(e.target.value))}>
            {Array.from({ length: MAX_VARIANTS }, (_, i) => i + 1).map((n) => (
              <option key={n} value={n}>
                {n} ({"ABCDEF".slice(0, n).split("").join(", ")})
              </option>
            ))}
          </Select>
        </div>
        <div className="col-span-2">
          <Label htmlFor="gen-source">Какое превью переделываем</Label>
          <Select
            id="gen-source"
            value={form.source_video_id ?? ""}
            onChange={(e) => set("source_video_id", e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">Авто — самое слабое по аудиту</option>
            {candidates.map((c) => (
              <option key={c.video_id} value={c.video_id}>
                {c.title.slice(0, 70)}
                {c.overall_score !== null ? ` · балл ${formatScore(c.overall_score)}` : " · без аудита"}
              </option>
            ))}
          </Select>
          {candidates.length === 0 ? (
            <p className="mt-1 text-xs text-zinc-500">Список превью канала появится после первой оценки.</p>
          ) : null}
        </div>
        <div>
          <Label htmlFor="gen-model">Модель</Label>
          <Select id="gen-model" value={form.model_key} onChange={(e) => set("model_key", e.target.value)}>
            <option value="">По умолчанию</option>
            {models.map((m) => (
              <option key={m.key} value={m.key}>
                {m.key} · {formatUsd(m.price_per_image_usd)}
                {m.pricing_verified ? "" : " (цена не проверена)"}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor="gen-size">Размер</Label>
          <Select id="gen-size" value={form.size} onChange={(e) => set("size", e.target.value as GenerationForm["size"])}>
            {Object.entries(SIZE_LABELS).map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor="gen-quality">Качество</Label>
          <Select
            id="gen-quality"
            value={form.quality}
            onChange={(e) => set("quality", e.target.value as GenerationForm["quality"])}
          >
            <option value="">По умолчанию модели</option>
            {Object.entries(QUALITY_LABELS).map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </Select>
        </div>
        <label className="col-span-2 flex items-center gap-1.5 self-end pb-2 text-sm">
          <input
            type="checkbox"
            checked={form.use_source_image}
            onChange={(e) => set("use_source_image", e.target.checked)}
          />
          отправить модели текущее превью (иначе — только описание)
        </label>
      </div>
      <div>
        <Label htmlFor="gen-instructions">Инструкции (необязательно)</Label>
        <Textarea
          id="gen-instructions"
          rows={2}
          maxLength={MAX_INSTRUCTIONS}
          className="font-sans"
          placeholder="например: без текста, лицо ведущего справа, фирменный жёлтый цвет"
          value={form.instructions}
          onChange={(e) => set("instructions", e.target.value)}
        />
      </div>
      <fieldset>
        <legend className="mb-1 text-xs font-medium text-zinc-600 dark:text-zinc-400">
          Референсы стиля (до {MAX_REFERENCES}) ·{" "}
          <Link href="/references" className="underline">
            управлять
          </Link>
        </legend>
        {references.isPending ? (
          <p className="text-xs text-zinc-500">Загрузка…</p>
        ) : refs.length === 0 ? (
          <p className="text-xs text-zinc-500">Референсов нет — загрузите их на странице «Референсы».</p>
        ) : (
          <ul className="flex flex-wrap gap-2">
            {refs.map((r) => {
              const on = form.reference_ids.includes(r.id);
              const full = !on && form.reference_ids.length >= MAX_REFERENCES;
              return (
                <li key={r.id}>
                  <label
                    className={cn(
                      "flex w-36 cursor-pointer flex-col gap-1 rounded-md border p-1 text-xs",
                      on ? "border-blue-500 ring-2 ring-blue-500/30" : "border-zinc-200 dark:border-zinc-700",
                      full && "cursor-not-allowed opacity-50",
                    )}
                    title={r.description || r.title}
                  >
                    {/* Served by our API with the session cookie. */}
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={r.image_url} alt="" loading="lazy" className="aspect-video w-full rounded object-cover" />
                    <span className="flex items-center gap-1">
                      <input
                        type="checkbox"
                        checked={on}
                        disabled={full}
                        onChange={(e) => toggleReference(r.id, e.target.checked)}
                        aria-label={`Референс «${r.title}»`}
                      />
                      <span className="truncate">{r.title}</span>
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
        )}
        <InlineError error={references.error} />
      </fieldset>
      {formErrors.length > 0 ? (
        <ul role="alert" className="list-inside list-disc text-xs text-red-600">
          {formErrors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" disabled={estimate.isPending}>
          <Calculator className="size-4" aria-hidden /> {estimate.isPending ? "Считаем…" : "Оценить задание и стоимость"}
        </Button>
        {!startable ? (
          <span className="text-xs text-amber-700 dark:text-amber-400">
            В статусе «{STATUS_LABELS[data?.lead_status ?? lead.status]}» генерация недоступна.
          </span>
        ) : running ? (
          <span className="text-xs text-zinc-500">Генерация для этого лида уже идёт — новая станет доступна после неё.</span>
        ) : null}
      </div>
      <GenerationError error={estimate.error} />
      {start.data ? <StartedNote started={start.data} /> : null}
      {estimate.data ? (
        <EstimateView
          e={estimate.data}
          stale={stale}
          canStart={startable && !running}
          starting={start.isPending || estimate.isPending}
          notice={notice}
          startError={isConfirmationError(start.error) ? null : start.error}
          onStart={confirmAndStart}
        />
      ) : null}
    </form>
  );
}

function StartedNote({ started }: { started: GenerationStartResult }) {
  return (
    <p role="status" className="text-xs text-zinc-600 dark:text-zinc-300">
      Генерация поставлена: {started.generation.variant_count} вариантов, потолок{" "}
      {formatUsd(started.generation.estimated_cost_usd)}. Лид — «{STATUS_LABELS[started.lead_status]}». Результаты
      появятся ниже ·{" "}
      <Link href="/jobs" className="underline">
        Задачи
      </Link>
    </p>
  );
}

const BASE_IMAGE_TEXT = {
  current_thumbnail: "Модель получит текущее превью и переделает его.",
  parent_variant: "Модель получит выбранный вариант как основу («ещё такие же»).",
} as const;

function EstimateView({
  e,
  stale,
  canStart,
  starting,
  notice,
  startError,
  onStart,
}: {
  e: GenerationEstimate;
  stale: boolean;
  canStart: boolean;
  starting: boolean;
  notice: string | null;
  startError: unknown;
  onStart: () => void;
}) {
  const b = e.brief;
  const approved = b.history.approved ?? [];
  const rejected = b.history.rejected ?? [];
  const enabled = canStart && !stale && !starting && e.model_key !== null;
  return (
    <div role="status" aria-label="Оценка генерации" className="space-y-3 rounded-md bg-zinc-50 p-3 text-sm dark:bg-zinc-800/60">
      <div className="flex flex-wrap gap-4">
        {e.source?.image_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={e.source.image_url} alt={`Текущее превью: ${e.source.title}`} className="aspect-video w-56 rounded object-cover" />
        ) : null}
        <div className="min-w-0 flex-1 space-y-1">
          <p>
            <span className="text-zinc-500">Канал:</span> <b>{b.channel_title}</b>
            {b.niches.length ? <span className="text-zinc-500"> · {b.niches.join(", ")}</span> : null}
          </p>
          <p>
            <span className="text-zinc-500">Видео:</span> {b.video_title ?? "без конкретного видео"}
            {e.source?.overall_score ? (
              <>
                {" "}
                <Badge tone={scoreTone(e.source.overall_score)}>балл {formatScore(e.source.overall_score)}</Badge>
              </>
            ) : null}
          </p>
          <p className="text-xs text-zinc-600 dark:text-zinc-400">
            {b.base_image ? BASE_IMAGE_TEXT[b.base_image] : "Изображение не отправляется — модель рисует по описанию."}
          </p>
          {b.palette.length ? (
            <p className="flex items-center gap-1 text-xs text-zinc-500">
              Палитра:
              {b.palette.map((hex) => (
                <span key={hex} title={hex} className="size-3 rounded-sm border border-white dark:border-zinc-900" style={{ backgroundColor: hex }} />
              ))}
            </p>
          ) : null}
        </div>
      </div>
      <div>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">Что исправляем</h3>
        {b.fixes.length === 0 ? (
          <p className="text-xs text-zinc-500">Проблем из аудита нет — сначала запустите AI-аудит превью, чтобы генерация знала, что исправлять.</p>
        ) : (
          <ul className="mt-1 space-y-1">
            {b.fixes.map((f) => (
              <li key={f.code} className="text-sm">
                <Badge tone={f.severity === "high" ? "red" : f.severity === "medium" ? "amber" : "neutral"}>
                  {problemLabel(f.code)}
                </Badge>{" "}
                <span className="text-xs text-zinc-500">{f.source === "channel" ? "повторяется на канале" : "это превью"}</span>
                {f.problem ? <span className="block text-xs text-zinc-600 dark:text-zinc-400">{f.problem}</span> : null}
                <span className="block text-xs text-zinc-500">→ {f.directive}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
      {b.recommendations.length || b.channel_summary || b.references.length || b.instructions ? (
        <dl className="grid gap-x-6 gap-y-1 text-xs sm:grid-cols-2">
          {b.recommendations.length ? (
            <div>
              <dt className="text-zinc-500">Рекомендации аудита</dt>
              <dd>{b.recommendations.join(" · ")}</dd>
            </div>
          ) : null}
          {b.channel_summary ? (
            <div>
              <dt className="text-zinc-500">Стиль канала</dt>
              <dd>{b.channel_summary}</dd>
            </div>
          ) : null}
          {b.references.length ? (
            <div>
              <dt className="text-zinc-500">Референсы</dt>
              <dd>{b.references.map((r) => r.title).join(", ")}</dd>
            </div>
          ) : null}
          {b.instructions ? (
            <div>
              <dt className="text-zinc-500">Инструкции</dt>
              <dd className="whitespace-pre-line">{b.instructions}</dd>
            </div>
          ) : null}
        </dl>
      ) : null}
      {approved.length || rejected.length ? (
        <p className="text-xs text-zinc-600 dark:text-zinc-400">
          Учитываем прошлые решения ({b.history.previous_requests ?? 0} генераций):
          {approved.length ? <> нравилось — {approved.map(strategyLabel).join(", ")}.</> : null}
          {rejected.length ? <> отклонено — {rejected.map(strategyLabel).join(", ")}.</> : null}
        </p>
      ) : null}
      <details className="text-xs">
        <summary className="cursor-pointer text-zinc-500">Промпт варианта A — ровно как будет отправлен</summary>
        <pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap rounded bg-white p-2 font-mono text-[11px] dark:bg-zinc-900">
          {e.prompt_preview}
        </pre>
      </details>
      <div className="border-t border-zinc-200 pt-2 dark:border-zinc-700">
        {e.model_key ? (
          <p>
            Модель: <code>{e.model_key}</code> ({e.api_model_id}) · за изображение <b>{formatUsd(e.price_per_image_usd)}</b> ·{" "}
            {e.variant_count} вар. · всего <b>{formatUsd(e.estimated_cost_usd)}</b>{" "}
            {e.pricing_verified ? null : (
              <Badge tone="amber" title="Цена модели не задана или не проверена в реестре моделей">
                цена не проверена
              </Badge>
            )}
            {e.provider === "mock" ? (
              <>
                {" "}
                <Badge tone="violet">mock — демо-картинки</Badge>
              </>
            ) : null}
          </p>
        ) : (
          <p className="text-amber-700 dark:text-amber-400">
            Модель генерации не настроена: задайте <code>YTL_VIBECODE_API_KEY</code> (или <code>YTL_AI_PROVIDER=mock</code>{" "}
            для демо). Задание уже собрано.
          </p>
        )}
        {e.model_key && e.estimated_cost_usd === null ? (
          <p className="text-xs text-amber-700 dark:text-amber-400">
            Цена неизвестна: при лимите бюджета в USD запуск будет отклонён.
          </p>
        ) : null}
        <p className="text-xs text-zinc-500">Подтверждённая сумма — жёсткий потолок задачи: дороже она не потратит.</p>
        {e.budgets.length === 0 ? (
          <p className="text-xs text-zinc-500">Лимиты бюджета не заданы — запуск только после вашего подтверждения.</p>
        ) : (
          <ul className="text-xs">
            {e.budgets.map((bd) => (
              <li key={bd.budget_id} className={bd.would_exceed ? "text-red-700 dark:text-red-400" : "text-zinc-600"}>
                {bd.description}: потрачено {formatUsd(bd.spent_usd)}
                {bd.limit_usd !== null ? ` из ${formatUsd(bd.limit_usd)}` : ""}, операций {bd.operations}
                {bd.max_ai_operations !== null ? ` из ${bd.max_ai_operations}` : ""}
                {bd.would_exceed ? ` — превысит: ${bd.reason}` : ""}
              </li>
            ))}
          </ul>
        )}
        {!e.within_budgets ? (
          <p className="text-xs text-amber-700 dark:text-amber-400">
            Оценка превышает лимит бюджета: задача остановится на лимите, следующий вариант не будет отправлен. Готовые
            варианты сохранятся.
          </p>
        ) : null}
      </div>
      {notice ? (
        <p role="alert" className="text-xs text-amber-700 dark:text-amber-400">
          {notice}
        </p>
      ) : null}
      {stale ? (
        <p className="text-xs text-amber-700 dark:text-amber-400">
          Форма изменилась после оценки — пересчитайте оценку, чтобы запустить.
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" disabled={!enabled} onClick={onStart}>
          <Play className="size-3.5" aria-hidden />
          {starting ? "Запуск…" : `Подтвердить и запустить: ${e.variant_count} вар. · ${formatUsd(e.estimated_cost_usd)}`}
        </Button>
        <span className="text-xs text-zinc-500">
          Запускается ровно эта оценка (код <code>{e.confirm_token.slice(0, 8)}</code>)
        </span>
      </div>
      <GenerationError error={startError} />
    </div>
  );
}

// --- results: Original vs Generated -------------------------------------------------------------

function GenerationResults({
  leadId,
  data,
  onMakeMoreLike,
}: {
  leadId: number;
  data: LeadGenerations;
  onMakeMoreLike: (v: GenerationVariant) => void;
}) {
  if (data.items.length === 0) {
    return <p className="text-sm text-zinc-500">Генераций ещё не было. Оцените задание выше и подтвердите запуск.</p>;
  }
  const originals = new Map(data.originals.map((o) => [o.video_id, o]));
  return (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold">
        Оригинал vs варианты <span className="font-normal text-zinc-500">({data.items.length} генераций)</span>
      </h3>
      {data.items.map((g) => (
        <GenerationBlock
          key={g.id}
          leadId={leadId}
          g={g}
          original={g.source_video_id !== null ? originals.get(g.source_video_id) : undefined}
          parent={findVariant(data, g.parent_variant_id)}
          onMakeMoreLike={onMakeMoreLike}
        />
      ))}
    </div>
  );
}

function defaultSelected(g: Generation): number | null {
  const withImage = g.variants.filter((v) => v.image_url);
  return (withImage.find((v) => v.status === "final") ?? withImage[0])?.id ?? null;
}

function GenerationBlock({
  leadId,
  g,
  original,
  parent,
  onMakeMoreLike,
}: {
  leadId: number;
  g: Generation;
  original: ComparisonOriginal | undefined;
  parent: GenerationVariant | undefined;
  onMakeMoreLike: (v: GenerationVariant) => void;
}) {
  const [picked, setPicked] = useState<number | null>(null);
  const selectedId = picked ?? defaultSelected(g);
  const selected = g.variants.find((v) => v.id === selectedId && v.image_url);
  const done = g.variants.filter((v) => v.status !== "pending").length;

  return (
    <section aria-label={`Генерация #${g.id}`} className="rounded-md border border-zinc-200 p-3 dark:border-zinc-800">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
        <Badge tone={g.status === "failed" ? "red" : g.status === "completed" ? "green" : "blue"}>
          {isGenerationActive(g) ? <Loader2 className="mr-1 size-3 animate-spin" aria-hidden /> : null}
          {GENERATION_STATUS_LABELS[g.status]}
        </Badge>
        <span className="tabular-nums text-zinc-500">
          #{g.id} · {formatDateTime(g.created_at)} · {done}/{g.variant_count} готово
        </span>
        <span className="text-zinc-500">
          <code>{g.model_key}</code> · потрачено {formatUsd(g.spent_usd)} из {formatUsd(g.estimated_cost_usd)}
        </span>
        {g.provider_is_mock ? <Badge tone="violet">mock</Badge> : null}
        {g.job ? <JobStatusBadge status={g.job.status} /> : null}
        {g.parent_variant_id !== null ? <Badge>«ещё такие же» от {parent ? `варианта ${parent.label}` : `#${g.parent_variant_id}`}</Badge> : null}
      </div>
      {g.error || g.job?.error_human ? (
        <p className="mb-2 text-xs text-red-700 dark:text-red-400">{g.job?.error_human ?? g.error}</p>
      ) : null}
      <div className="grid gap-3 md:grid-cols-2">
        <OriginalPane original={original} fallbackUrl={g.source_image_url} parent={parent} />
        <figure>
          <figcaption className="mb-1 text-xs font-medium text-zinc-500">
            {selected ? `Вариант ${selected.label} · ${strategyLabel(selected.strategy)}` : "Вариант"}
          </figcaption>
          <div className="aspect-video overflow-hidden rounded-md bg-zinc-100 dark:bg-zinc-800">
            {selected?.image_url ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={selected.image_url} alt={`Вариант ${selected.label}`} className="size-full object-cover" />
            ) : (
              <div className="flex size-full items-center justify-center text-xs text-zinc-500">
                {isGenerationActive(g) ? "Варианты генерируются…" : "Готовых вариантов нет"}
              </div>
            )}
          </div>
        </figure>
      </div>
      <ul className="mt-3 grid grid-cols-[repeat(auto-fill,minmax(13rem,1fr))] gap-3">
        {g.variants.map((v) => (
          <VariantCard
            key={v.id}
            leadId={leadId}
            v={v}
            selected={v.id === selected?.id}
            onSelect={() => setPicked(v.id)}
            onMakeMoreLike={() => onMakeMoreLike(v)}
          />
        ))}
      </ul>
    </section>
  );
}

function OriginalPane({
  original,
  fallbackUrl,
  parent,
}: {
  original: ComparisonOriginal | undefined;
  fallbackUrl: string | null;
  parent: GenerationVariant | undefined;
}) {
  const url = original?.image_url ?? fallbackUrl;
  const problems = original ? originalProblems(original.problems) : [];
  return (
    <figure>
      <figcaption className="mb-1 flex items-center gap-1.5 text-xs font-medium text-zinc-500">
        Оригинал{original ? <span className="truncate font-normal">· {original.title}</span> : null}
        {original?.overall_score ? <Badge tone={scoreTone(original.overall_score)}>{formatScore(original.overall_score)}</Badge> : null}
      </figcaption>
      <div className="aspect-video overflow-hidden rounded-md bg-zinc-100 dark:bg-zinc-800">
        {url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={url} alt={`Оригинал${original ? `: ${original.title}` : ""}`} className="size-full object-cover" />
        ) : (
          <div className="flex size-full flex-col items-center justify-center gap-1 text-xs text-zinc-500">
            <ImageOff className="size-5" aria-hidden /> Без исходного превью
          </div>
        )}
      </div>
      {problems.length ? (
        <p className="mt-1 flex flex-wrap gap-1">
          {problems.slice(0, 6).map((p) => (
            <Badge key={p.code} tone={p.severity === "high" ? "red" : p.severity === "medium" ? "amber" : "neutral"} title={p.description}>
              {problemLabel(p.code)}
            </Badge>
          ))}
        </p>
      ) : null}
      {parent ? <p className="mt-1 text-xs text-zinc-500">Основа — вариант {parent.label} ({strategyLabel(parent.strategy)}).</p> : null}
    </figure>
  );
}

const DECISION_ICONS = { approve: Check, reject: X, final: Star, reset: null } as const;

function VariantCard({
  leadId,
  v,
  selected,
  onSelect,
  onMakeMoreLike,
}: {
  leadId: number;
  v: GenerationVariant;
  selected: boolean;
  onSelect: () => void;
  onMakeMoreLike: () => void;
}) {
  const decide = useDecideVariant(leadId);
  return (
    <li
      aria-label={`Вариант ${v.label}`}
      className={cn(
        "min-w-0 rounded-md border p-1.5",
        v.status === "final" ? "border-violet-500 ring-2 ring-violet-500/30" : selected ? "border-blue-500" : "border-zinc-200 dark:border-zinc-800",
      )}
    >
      <button
        type="button"
        onClick={onSelect}
        disabled={!v.image_url}
        aria-label={`Сравнить вариант ${v.label} с оригиналом`}
        className="block aspect-video w-full overflow-hidden rounded bg-zinc-100 disabled:cursor-default dark:bg-zinc-800"
      >
        {v.image_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={v.image_url} alt="" loading="lazy" className="size-full object-cover" />
        ) : (
          <span className="flex size-full items-center justify-center gap-1 text-xs text-zinc-500">
            {v.status === "pending" ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <ImageOff className="size-4" aria-hidden />}
            {VARIANT_STATUS_LABELS[v.status]}
          </span>
        )}
      </button>
      <div className="mt-1 flex flex-wrap items-center gap-1 text-xs">
        <b>{v.label}</b>
        <span className="truncate text-zinc-500">{strategyLabel(v.strategy)}</span>
        <span className="flex-1" />
        <Badge tone={VARIANT_STATUS_TONES[v.status]}>{VARIANT_STATUS_LABELS[v.status]}</Badge>
      </div>
      {v.cost_usd !== null ? <p className="text-[11px] tabular-nums text-zinc-500">{formatUsd(v.cost_usd)}</p> : null}
      {v.error ? <p className="text-[11px] text-red-700 dark:text-red-400">{v.error}</p> : null}
      <div className="mt-1 flex flex-wrap gap-1">
        {variantDecisions(v.status).map((d) => {
          const Icon = DECISION_ICONS[d];
          return (
            <Button
              key={d}
              size="sm"
              variant={d === "final" ? "primary" : "secondary"}
              disabled={decide.isPending}
              aria-label={`${DECISION_LABELS[d]}: вариант ${v.label}`}
              onClick={() => decide.mutate({ id: v.id, decision: d })}
            >
              {Icon ? <Icon className="size-3" aria-hidden /> : null}
              {DECISION_LABELS[d]}
            </Button>
          );
        })}
        {canMakeMoreLike(v) ? (
          <Button size="sm" variant="ghost" aria-label={`Ещё такие же: вариант ${v.label}`} onClick={onMakeMoreLike}>
            <Sparkles className="size-3" aria-hidden /> Ещё такие же
          </Button>
        ) : null}
      </div>
      <GenerationError error={decide.error} />
      <details className="mt-1 text-[11px]">
        <summary className="cursor-pointer text-zinc-500">Промпт и настройки</summary>
        <p className="mt-1 text-zinc-500">
          {v.model_key}
          {v.api_model_id ? ` (${v.api_model_id})` : ""} · {Object.entries(v.settings).map(([k, val]) => `${k}=${String(val)}`).join(", ")}
        </p>
        <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-zinc-50 p-1.5 font-mono dark:bg-zinc-800">{v.prompt}</pre>
      </details>
    </li>
  );
}
