"use client";

import { Plus, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

import { InlineError, LoadingState } from "@/components/states";
import { Button, Card, Input, Select } from "@/components/ui";
import {
  useDeleteIdealProfile,
  useIdealProfiles,
  useLeadProfile,
  useMe,
  usePriorityProfile,
  useSaveIdealProfile,
  useSetLeadProfile,
  useSetPriorityProfile,
  useTaxonomy,
  type IdealLeadProfile,
  type LeadWeights,
  type PriorityWeights,
  type RescoreResult,
} from "@/lib/api/hooks";
import {
  ACTIVITY_LABELS,
  ACTIVITY_LEVELS,
  COMPONENTS,
  COMPONENT_HINTS,
  COMPONENT_LABELS,
  criteriaFromForm,
  formFromProfile,
  weightsValid,
  type ComponentKey,
  type CriteriaForm,
} from "@/lib/leads";
import { taxonomyOptions, taxonomyPath } from "@/lib/taxonomy";

/** Weights of Lead / Priority Score and the Ideal Lead Profile (ТЗ §8, §52–53): every save rescores the leads. */
export function ScoringSettings() {
  const me = useMe();
  const isAdmin = me.data ? ["owner", "admin"].includes(me.data.role) : false;
  return (
    <div className="mb-4 grid gap-4 xl:grid-cols-3">
      <LeadWeightsCard canEdit={isAdmin} />
      <PriorityWeightsCard canEdit={isAdmin} />
      <IdealProfileCard />
    </div>
  );
}

function Rescored({ r }: { r: RescoreResult | undefined }) {
  if (!r) return null;
  return (
    <span role="status" className="text-xs text-emerald-700">
      Сохранено, пересчитано: изменились {r.scores_changed}, без изменений {r.scores_unchanged}
    </span>
  );
}

function WeightInputs({
  values,
  onChange,
  disabled,
}: {
  values: Record<ComponentKey, number>;
  onChange: (key: ComponentKey, v: number) => void;
  disabled: boolean;
}) {
  return (
    <div className="grid grid-cols-[1fr_5.5rem] items-center gap-x-2 gap-y-1.5 text-sm">
      {COMPONENTS.map((k) => (
        <label key={k} className="contents">
          <span title={COMPONENT_HINTS[k]}>{COMPONENT_LABELS[k]}</span>
          <Input
            type="number"
            min={0}
            max={100}
            step="1"
            value={values[k]}
            disabled={disabled}
            onChange={(e) => onChange(k, e.currentTarget.value === "" ? 0 : Number(e.currentTarget.value))}
            className="h-8 text-right tabular-nums"
          />
        </label>
      ))}
    </div>
  );
}

function pickWeights(w: LeadWeights | PriorityWeights): Record<ComponentKey, number> {
  return Object.fromEntries(COMPONENTS.map((k) => [k, w[k]])) as Record<ComponentKey, number>;
}

function LeadWeightsCard({ canEdit }: { canEdit: boolean }) {
  const profile = useLeadProfile();
  const save = useSetLeadProfile();
  if (profile.isPending) return <Card><LoadingState /></Card>;
  if (profile.isError) return <Card><InlineError error={profile.error} /></Card>;
  return (
    <LeadWeightsForm
      key={profile.data.version}
      version={profile.data.version}
      initial={profile.data.weights}
      canEdit={canEdit}
      saving={save.isPending}
      onSave={(w) => save.mutate(w)}
      footer={
        <>
          <Rescored r={save.data?.rescored} />
          <InlineError error={save.error} />
        </>
      }
    />
  );
}

function LeadWeightsForm({
  version,
  initial,
  canEdit,
  saving,
  onSave,
  footer,
}: {
  version: number;
  initial: LeadWeights;
  canEdit: boolean;
  saving: boolean;
  onSave: (w: LeadWeights) => void;
  footer: React.ReactNode;
}) {
  const [weights, setWeights] = useState(() => pickWeights(initial));
  const [niches, setNiches] = useState<[number, number][]>(() =>
    Object.entries(initial.niche_values ?? {}).map(([k, v]) => [Number(k), v]),
  );
  const taxonomy = useTaxonomy();
  const nodes = useMemo(() => taxonomy.data ?? [], [taxonomy.data]);
  const options = useMemo(() => taxonomyOptions(nodes), [nodes]);
  const [pick, setPick] = useState("");
  const total = COMPONENTS.reduce((acc, k) => acc + (weights[k] || 0), 0);
  const valid = weightsValid(weights) && niches.every(([, v]) => v >= 0 && v <= 10);

  return (
    <Card className="space-y-3">
      <div>
        <h2 className="text-sm font-semibold">Lead Score: максимум баллов</h2>
        <p className="text-xs text-zinc-500">
          Версия {version === 0 ? "по умолчанию" : version}. Сумма {total} — итог всегда приводится к 0–100.
        </p>
      </div>
      <WeightInputs values={weights} disabled={!canEdit} onChange={(k, v) => setWeights((w) => ({ ...w, [k]: v }))} />
      <div className="space-y-1.5 border-t border-zinc-100 pt-2 dark:border-zinc-800">
        <h3 className="text-xs font-medium uppercase tracking-wide text-zinc-500">Ценность ниш (0–10)</h3>
        {niches.length === 0 ? <p className="text-xs text-zinc-500">Не задана — компонент «Ценность ниши» без данных.</p> : null}
        {niches.map(([id, value], i) => (
          <div key={id} className="flex items-center gap-2 text-sm">
            <span className="flex-1 truncate">{taxonomyPath(nodes, id) ?? `#${id}`}</span>
            <Input
              type="number"
              min={0}
              max={10}
              step="0.5"
              value={value}
              disabled={!canEdit}
              aria-label={`Ценность: ${taxonomyPath(nodes, id) ?? id}`}
              onChange={(e) => {
                const v = Number(e.currentTarget.value);
                setNiches((list) => list.map((row, j) => (j === i ? [row[0], v] : row)));
              }}
              className="h-8 w-20 text-right"
            />
            <Button size="sm" variant="ghost" disabled={!canEdit} aria-label="Убрать" onClick={() => setNiches((list) => list.filter((_, j) => j !== i))}>
              <Trash2 className="size-3.5" aria-hidden />
            </Button>
          </div>
        ))}
        {canEdit ? (
          <div className="flex items-center gap-2">
            <Select aria-label="Ниша для оценки" value={pick} onChange={(e) => setPick(e.target.value)} className="h-8 flex-1">
              <option value="">Выберите нишу…</option>
              {options
                .filter((o) => !niches.some(([id]) => id === o.id))
                .map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.label}
                  </option>
                ))}
            </Select>
            <Button
              size="sm"
              variant="secondary"
              disabled={!pick}
              onClick={() => {
                setNiches((list) => [...list, [Number(pick), 5]]);
                setPick("");
              }}
            >
              <Plus className="size-3.5" aria-hidden /> Добавить
            </Button>
          </div>
        ) : null}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          disabled={!canEdit || !valid || saving}
          onClick={() => onSave({ ...weights, niche_values: Object.fromEntries(niches.map(([k, v]) => [String(k), v])) })}
        >
          Сохранить и пересчитать
        </Button>
        {!canEdit ? <span className="text-xs text-zinc-500">Менять веса может администратор.</span> : null}
        {!valid ? <span className="text-xs text-red-600">Нужен хотя бы один вес больше 0; значения 0–100.</span> : null}
        {footer}
      </div>
    </Card>
  );
}

function PriorityWeightsCard({ canEdit }: { canEdit: boolean }) {
  const profile = usePriorityProfile();
  const save = useSetPriorityProfile();
  if (profile.isPending) return <Card><LoadingState /></Card>;
  if (profile.isError) return <Card><InlineError error={profile.error} /></Card>;
  return <PriorityForm key={profile.data.version} version={profile.data.version} initial={profile.data.weights}
    canEdit={canEdit} saving={save.isPending} onSave={(w) => save.mutate(w)}
    footer={<><Rescored r={save.data?.rescored} /><InlineError error={save.error} /></>} />;
}

function PriorityForm({
  version,
  initial,
  canEdit,
  saving,
  onSave,
  footer,
}: {
  version: number;
  initial: PriorityWeights;
  canEdit: boolean;
  saving: boolean;
  onSave: (w: PriorityWeights) => void;
  footer: React.ReactNode;
}) {
  const [weights, setWeights] = useState(() => pickWeights(initial));
  const valid = weightsValid(weights);
  return (
    <Card className="space-y-3">
      <div>
        <h2 className="text-sm font-semibold">Приоритет (Smart Prioritization)</h2>
        <p className="text-xs text-zinc-500">
          Версия {version === 0 ? "по умолчанию" : version}. Отдельные веса тех же компонентов; «коммерческий потенциал»
          — ценность ниши. Сортировка «Приоритет».
        </p>
      </div>
      <WeightInputs values={weights} disabled={!canEdit} onChange={(k, v) => setWeights((w) => ({ ...w, [k]: v }))} />
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" disabled={!canEdit || !valid || saving} onClick={() => onSave(weights)}>
          Сохранить и пересчитать
        </Button>
        {!valid ? <span className="text-xs text-red-600">Нужен хотя бы один вес больше 0.</span> : null}
        {footer}
      </div>
    </Card>
  );
}

function IdealProfileCard() {
  const profiles = useIdealProfiles();
  const [selected, setSelected] = useState<number | "new" | undefined>(undefined);
  const list = profiles.data ?? [];
  const current =
    selected === "new" ? null : (list.find((p) => p.id === selected) ?? list.find((p) => p.is_default) ?? list[0] ?? null);

  return (
    <Card className="space-y-3">
      <div>
        <h2 className="text-sm font-semibold">Профиль идеального лида</h2>
        <p className="text-xs text-zinc-500">
          Channel Fit = доля заданных критериев, которым канал соответствует (по профилю «по умолчанию»). Критерий без
          данных считается несовпавшим; контакты учитываются с Phase 6.
        </p>
      </div>
      {profiles.isPending ? <LoadingState /> : <InlineError error={profiles.error} />}
      {profiles.data ? (
        <>
          <div className="flex items-center gap-2">
            <Select
              aria-label="Профиль"
              value={current ? String(current.id) : "new"}
              onChange={(e) => setSelected(e.target.value === "new" ? "new" : Number(e.target.value))}
              className="h-8 flex-1"
            >
              {list.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                  {p.is_default ? " (по умолчанию)" : ""}
                </option>
              ))}
              <option value="new">+ Новый профиль</option>
            </Select>
          </div>
          <IdealProfileForm
            key={current ? `${current.id}-${current.updated_at}` : "new"}
            profile={current}
            onSaved={(id) => setSelected(id)}
            onDeleted={() => setSelected(undefined)}
          />
        </>
      ) : null}
    </Card>
  );
}

function IdealProfileForm({
  profile,
  onSaved,
  onDeleted,
}: {
  profile: IdealLeadProfile | null;
  onSaved: (id: number) => void;
  onDeleted: () => void;
}) {
  const [form, setForm] = useState(() => formFromProfile(profile));
  const save = useSaveIdealProfile();
  const remove = useDeleteIdealProfile();
  const taxonomy = useTaxonomy();
  const options = useMemo(() => taxonomyOptions(taxonomy.data ?? []), [taxonomy.data]);
  const set = <K extends keyof CriteriaForm>(k: K, v: CriteriaForm[K]) => setForm((f) => ({ ...f, [k]: v }));
  const field = (k: keyof CriteriaForm, label: string, props: React.InputHTMLAttributes<HTMLInputElement> = {}) => (
    <label className="contents">
      <span>{label}</span>
      <Input
        value={form[k] as string}
        onChange={(e) => set(k, e.currentTarget.value as never)}
        className="h-8"
        {...props}
      />
    </label>
  );

  return (
    <form
      className="space-y-2"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate(
          { id: profile?.id, body: { name: form.name, is_default: form.is_default, criteria: criteriaFromForm(form) } },
          { onSuccess: (saved) => onSaved(saved.id) },
        );
      }}
    >
      <div className="grid grid-cols-[1fr_9rem] items-center gap-x-2 gap-y-1.5 text-sm">
        {field("name", "Название", { required: true, maxLength: 200 })}
        {field("min_subscribers", "Подписчиков от", { type: "number", min: 0 })}
        {field("max_subscribers", "Подписчиков до", { type: "number", min: 0 })}
        {field("min_avg_views", "Ср. просмотров от", { type: "number", min: 0 })}
        <label className="contents">
          <span>Активность не ниже</span>
          <Select value={form.min_activity} onChange={(e) => set("min_activity", e.currentTarget.value as CriteriaForm["min_activity"])} className="h-8">
            <option value="">—</option>
            {ACTIVITY_LEVELS.map((a) => (
              <option key={a} value={a}>
                {ACTIVITY_LABELS[a]}
              </option>
            ))}
          </Select>
        </label>
        {field("max_days_since_last_upload", "Посл. видео ≤ дн.", { type: "number", min: 0 })}
        {field("max_thumbnail_score", "Балл превью ≤", { type: "number", min: 1, max: 10, step: "0.1" })}
        {field("countries", "Страны (US, RU)")}
        {field("languages", "Языки (en, ru)")}
        <label className="contents">
          <span>Ниши</span>
          <Select
            multiple
            value={form.niches.map(String)}
            onChange={(e) => set("niches", Array.from(e.currentTarget.selectedOptions, (o) => Number(o.value)))}
            className="h-16 py-1"
          >
            {options.map((o) => (
              <option key={o.id} value={o.id}>
                {o.label}
              </option>
            ))}
          </Select>
        </label>
        <span>Контакты</span>
        <span className="flex gap-3 text-xs">
          {(["telegram", "email"] as const).map((t) => (
            <label key={t} className="inline-flex items-center gap-1" title="Учитывается с Phase 6 (сбор контактов)">
              <input
                type="checkbox"
                checked={form.contact_types.includes(t)}
                onChange={(e) =>
                  set("contact_types", e.currentTarget.checked ? [...form.contact_types, t] : form.contact_types.filter((x) => x !== t))
                }
              />
              {t}
            </label>
          ))}
        </span>
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={form.is_default} onChange={(e) => set("is_default", e.currentTarget.checked)} />
        Использовать для Channel Fit (по умолчанию)
      </label>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" type="submit" disabled={save.isPending || !form.name.trim()}>
          {profile ? "Сохранить и пересчитать" : "Создать и пересчитать"}
        </Button>
        {profile ? (
          <Button
            size="sm"
            variant="ghost"
            disabled={remove.isPending}
            onClick={() => remove.mutate(profile.id, { onSuccess: onDeleted })}
          >
            <Trash2 className="size-3.5" aria-hidden /> Удалить
          </Button>
        ) : null}
        <Rescored r={save.data?.rescored ?? remove.data} />
        <InlineError error={save.error ?? remove.error} />
      </div>
    </form>
  );
}
