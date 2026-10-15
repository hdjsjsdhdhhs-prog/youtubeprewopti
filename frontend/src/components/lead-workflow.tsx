"use client";

/** Human workflow widgets (Phase 4): favorite star, tag chips, manual status actions, human score editor. */
import { Pencil, Star, Tag as TagIcon, X } from "lucide-react";
import { useState } from "react";

import { InlineError } from "@/components/states";
import { Button, Input, Select } from "@/components/ui";
import type { FavoriteEntity, Lead, LeadTag, ScoreTarget } from "@/lib/api/hooks";
import {
  useBulkLeads,
  useRemoveScoreOverride,
  useSetLeadStatus,
  useSetScoreOverride,
  useTags,
  useToggleFavorite,
} from "@/lib/api/workflow-hooks";
import { cn } from "@/lib/utils";
import { actionLabel, humanValue, manualTargets, parseScore, tagStyle, type ScoreOverride } from "@/lib/workflow";

export function FavoriteStar({
  type,
  id,
  on,
  label,
}: {
  type: FavoriteEntity;
  id: number;
  on: boolean;
  label: string;
}) {
  const toggle = useToggleFavorite();
  // Optimistic: the star flips at once and falls back if the request fails.
  const [pending, setPending] = useState<boolean | null>(null);
  const shown = pending ?? on;
  return (
    <button
      type="button"
      aria-pressed={shown}
      aria-label={shown ? `Убрать «${label}» из избранного` : `Добавить «${label}» в избранное`}
      title={toggle.error ? "Не удалось сохранить избранное" : shown ? "В избранном" : "В избранное"}
      disabled={toggle.isPending}
      onClick={(e) => {
        e.stopPropagation();
        setPending(!shown);
        toggle.mutate({ type, id, on: !shown }, { onSettled: () => setPending(null) });
      }}
      className={cn(
        "rounded p-1 hover:bg-zinc-200 disabled:opacity-60 dark:hover:bg-zinc-700",
        shown ? "text-amber-500" : "text-zinc-400",
        toggle.error && "text-red-500",
      )}
    >
      <Star className="size-4" fill={shown ? "currentColor" : "none"} aria-hidden />
    </button>
  );
}

export function TagChip({ tag, onRemove }: { tag: Pick<LeadTag, "name" | "color" | "is_system">; onRemove?: () => void }) {
  return (
    <span
      style={tagStyle(tag.color)}
      title={tag.is_system ? "Системный тег: назначается правилом" : "Тег"}
      className={cn(
        "inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-xs",
        tag.color ? "" : "border-zinc-200 bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-800",
        tag.is_system && "italic",
      )}
    >
      {tag.name}
      {onRemove ? (
        <button type="button" aria-label={`Снять тег «${tag.name}»`} onClick={onRemove} className="hover:text-red-600">
          <X className="size-3" aria-hidden />
        </button>
      ) : null}
    </span>
  );
}

export function TagChips({ tags, max = 4 }: { tags: LeadTag[]; max?: number }) {
  if (tags.length === 0) return null;
  const shown = tags.slice(0, max);
  return (
    <span className="flex flex-wrap gap-1">
      {shown.map((t) => (
        <TagChip key={t.id} tag={t} />
      ))}
      {tags.length > max ? <span className="text-xs text-zinc-500">+{tags.length - max}</span> : null}
    </span>
  );
}

/** Manual decisions allowed from the lead's status; rejecting asks for an optional reason. */
export function LeadStatusActions({ lead }: { lead: Pick<Lead, "id" | "status"> }) {
  const setStatus = useSetLeadStatus();
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const targets = manualTargets(lead.status);
  if (targets.length === 0) return <p className="text-xs text-zinc-500">Статус меняется только системой.</p>;
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-1.5">
        {targets.map((to) =>
          to === "rejected" ? (
            <Button key={to} size="sm" variant="secondary" disabled={setStatus.isPending} onClick={() => setRejecting((v) => !v)}>
              {actionLabel(lead.status, to)}
            </Button>
          ) : (
            <Button
              key={to}
              size="sm"
              variant={to === "selected" ? "primary" : "secondary"}
              disabled={setStatus.isPending}
              onClick={() => setStatus.mutate({ id: lead.id, status: to })}
            >
              {actionLabel(lead.status, to)}
            </Button>
          ),
        )}
      </div>
      {rejecting ? (
        <form
          className="flex flex-wrap items-center gap-1.5"
          onSubmit={(e) => {
            e.preventDefault();
            setStatus.mutate(
              { id: lead.id, status: "rejected", reason: reason.trim() },
              {
                onSuccess: () => {
                  setRejecting(false);
                  setReason("");
                },
              },
            );
          }}
        >
          <Input
            aria-label="Причина отклонения"
            placeholder="Причина (необязательно): не та ниша, нет контактов…"
            value={reason}
            maxLength={500}
            onChange={(e) => setReason(e.target.value)}
            className="h-7 w-80 text-xs"
          />
          <Button size="sm" variant="danger" type="submit" disabled={setStatus.isPending}>
            Отклонить
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setRejecting(false)}>
            Отмена
          </Button>
        </form>
      ) : null}
      <InlineError error={setStatus.error} />
    </div>
  );
}

/** User tags of one lead: remove with ×, add from the workspace's tags. System tags are shown, not editable. */
export function LeadTagsEditor({ lead }: { lead: Pick<Lead, "id" | "tags"> }) {
  const tags = useTags();
  const bulk = useBulkLeads();
  const has = new Set(lead.tags.map((t) => t.id));
  const addable = (tags.data ?? []).filter((t) => !t.is_system && !has.has(t.id));
  const act = (type: "add_tag" | "remove_tag", tagId: number) =>
    bulk.mutate({ selection: { lead_ids: [lead.id] }, action: { type, tag_id: tagId } });
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center gap-1">
        <TagIcon className="size-3.5 text-zinc-400" aria-hidden />
        {lead.tags.length === 0 ? <span className="text-xs text-zinc-500">тегов нет</span> : null}
        {lead.tags.map((t) => (
          <TagChip key={t.id} tag={t} onRemove={t.is_system ? undefined : () => act("remove_tag", t.id)} />
        ))}
        {addable.length > 0 ? (
          <Select
            aria-label="Добавить тег"
            value=""
            disabled={bulk.isPending}
            onChange={(e) => e.target.value && act("add_tag", Number(e.target.value))}
            className="h-7 w-40 py-0 text-xs"
          >
            <option value="">+ тег…</option>
            {addable.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </Select>
        ) : null}
      </div>
      <InlineError error={bulk.error} />
    </div>
  );
}

/** «AI: 6,8 · я: 5,5» (ТЗ §47): shows the human value next to the AI one; set, change or remove it. */
export function HumanScore({
  target,
  targetId,
  field,
  aiValue,
  overrides,
  min,
  max,
  label,
  format,
}: {
  target: ScoreTarget;
  targetId: number;
  field: string;
  aiValue: string | number | null | undefined;
  overrides: readonly ScoreOverride[] | undefined;
  min: number;
  max: number;
  label: string;
  format: (v: string | number | null | undefined) => string;
}) {
  const human = humanValue(overrides, field);
  const set = useSetScoreOverride();
  const remove = useRemoveScoreOverride();
  const [editing, setEditing] = useState(false);
  const [raw, setRaw] = useState("");
  const [reason, setReason] = useState("");
  const parsed = parseScore(raw, min, max);
  const busy = set.isPending || remove.isPending;

  return (
    <div className="text-xs">
      <span className="text-zinc-500">{label}: </span>
      <span title="Значение AI / кода — не перезаписывается">AI {format(aiValue)}</span>
      {human ? (
        <>
          <span className="mx-1 text-zinc-300">·</span>
          <b title={human.reason || "Ручная оценка"}>я {format(human.human_value)}</b>
        </>
      ) : null}
      {!editing ? (
        <>
          <button
            type="button"
            aria-label={`${human ? "Изменить" : "Поставить"} свою оценку: ${label}`}
            onClick={() => {
              setRaw(human ? String(Number(human.human_value)) : "");
              setReason(human?.reason ?? "");
              setEditing(true);
            }}
            className="ml-1 rounded p-0.5 text-zinc-400 hover:bg-zinc-200 hover:text-zinc-700 dark:hover:bg-zinc-700"
          >
            <Pencil className="inline size-3" aria-hidden />
          </button>
          {human ? (
            <button
              type="button"
              disabled={busy}
              aria-label={`Вернуть оценку AI: ${label}`}
              onClick={() => remove.mutate(human.id)}
              className="ml-0.5 rounded p-0.5 text-zinc-400 hover:bg-zinc-200 hover:text-red-600 dark:hover:bg-zinc-700"
            >
              <X className="inline size-3" aria-hidden />
            </button>
          ) : null}
        </>
      ) : (
        <form
          className="mt-1 flex flex-wrap items-center gap-1"
          onSubmit={(e) => {
            e.preventDefault();
            if (parsed === null) return;
            set.mutate(
              { target_type: target, target_id: targetId, field, human_value: parsed, reason: reason.trim() },
              { onSuccess: () => setEditing(false) },
            );
          }}
        >
          <Input
            aria-label={`Своя оценка: ${label} (${min}–${max})`}
            aria-invalid={raw !== "" && parsed === null}
            inputMode="decimal"
            placeholder={`${min}–${max}`}
            value={raw}
            onChange={(e) => setRaw(e.target.value)}
            className="h-7 w-20 text-xs"
            autoFocus
          />
          <Input
            aria-label="Комментарий к оценке"
            placeholder="почему (необязательно)"
            value={reason}
            maxLength={500}
            onChange={(e) => setReason(e.target.value)}
            className="h-7 w-48 text-xs"
          />
          <Button size="sm" type="submit" disabled={busy || parsed === null}>
            Сохранить
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
            Отмена
          </Button>
        </form>
      )}
      <InlineError error={set.error ?? remove.error} />
    </div>
  );
}
