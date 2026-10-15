"use client";

import { Trash2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { TagChip } from "@/components/lead-workflow";
import { EmptyState, ErrorState, InlineError, LoadingState } from "@/components/states";
import { Badge, Button, Card, Input, Label, PageHeader } from "@/components/ui";
import type { Tag } from "@/lib/api/hooks";
import { useCreateTag, useDeleteTag, useTags, useUpdateTag } from "@/lib/api/workflow-hooks";

const DEFAULT_COLOR = "#3b82f6";

/** ТЗ §36: user tags (create, rename, recolor, delete) and system tags assigned by rules. */
export function TagsView() {
  const tags = useTags();
  const create = useCreateTag();
  const [name, setName] = useState("");
  const [color, setColor] = useState(DEFAULT_COLOR);
  const system = (tags.data ?? []).filter((t) => t.is_system);
  const user = (tags.data ?? []).filter((t) => !t.is_system);

  return (
    <div className="max-w-3xl">
      <PageHeader
        title="Теги"
        subtitle="Свои теги назначаются на странице лидов (по одному или массово); системные — правилами при пересчёте"
      />
      <Card className="mb-4">
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (!name.trim()) return;
            create.mutate({ name: name.trim(), color }, { onSuccess: () => setName("") });
          }}
        >
          <div>
            <Label htmlFor="tag-name">Новый тег</Label>
            <Input
              id="tag-name"
              value={name}
              maxLength={60}
              placeholder="например, finance или sample-ready"
              onChange={(e) => setName(e.target.value)}
              className="w-72"
            />
          </div>
          <div>
            <Label htmlFor="tag-color">Цвет</Label>
            <Input id="tag-color" type="color" value={color} onChange={(e) => setColor(e.target.value)} className="w-14 p-1" />
          </div>
          <Button type="submit" disabled={create.isPending || !name.trim()}>
            Создать
          </Button>
        </form>
        <div className="mt-2">
          <InlineError error={create.error} />
        </div>
      </Card>

      {tags.isPending ? (
        <LoadingState />
      ) : tags.isError ? (
        <ErrorState error={tags.error} onRetry={() => void tags.refetch()} />
      ) : (
        <div className="space-y-4">
          <Card>
            <h2 className="mb-2 text-sm font-semibold">Мои теги</h2>
            {user.length === 0 ? (
              <EmptyState title="Своих тегов пока нет" hint="Создайте тег выше и назначьте его лидам." />
            ) : (
              <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
                {user.map((t) => (
                  <TagRow key={t.id} tag={t} />
                ))}
              </ul>
            )}
          </Card>
          <Card>
            <h2 className="mb-2 text-sm font-semibold">Системные теги</h2>
            <p className="mb-2 text-xs text-zinc-500">
              Назначаются и снимаются автоматически при каждом пересчёте лидов (без AI). Переименовать, удалить или
              назначить вручную нельзя — только сменить цвет.
            </p>
            <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
              {system.map((t) => (
                <TagRow key={t.id} tag={t} />
              ))}
            </ul>
          </Card>
        </div>
      )}
    </div>
  );
}

function TagRow({ tag }: { tag: Tag }) {
  const update = useUpdateTag();
  const remove = useDeleteTag();
  const [name, setName] = useState(tag.name);
  const [editing, setEditing] = useState(false);
  return (
    <li className="py-2">
      <div className="flex flex-wrap items-center gap-2">
        {editing ? (
          <form
            className="flex items-center gap-1"
            onSubmit={(e) => {
              e.preventDefault();
              update.mutate({ id: tag.id, body: { name: name.trim() } }, { onSuccess: () => setEditing(false) });
            }}
          >
            <Input
              aria-label="Название тега"
              value={name}
              maxLength={60}
              onChange={(e) => setName(e.target.value)}
              className="h-7 w-56 text-xs"
              autoFocus
            />
            <Button size="sm" type="submit" disabled={update.isPending || !name.trim()}>
              Сохранить
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
              Отмена
            </Button>
          </form>
        ) : (
          <TagChip tag={tag} />
        )}
        <Link href={`/leads?tag=${tag.id}`} className="text-xs text-zinc-500 hover:underline">
          лидов: {tag.leads}
        </Link>
        {tag.rule ? <Badge title="Правило назначения">{tag.rule}</Badge> : null}
        <span className="flex-1" />
        <input
          type="color"
          aria-label={`Цвет тега «${tag.name}»`}
          value={tag.color ?? DEFAULT_COLOR}
          disabled={update.isPending}
          onChange={(e) => update.mutate({ id: tag.id, body: { color: e.target.value } })}
          className="h-7 w-9 cursor-pointer rounded border border-zinc-300 dark:border-zinc-700"
        />
        {tag.color ? (
          <Button size="sm" variant="ghost" onClick={() => update.mutate({ id: tag.id, body: { color: null } })}>
            без цвета
          </Button>
        ) : null}
        {!tag.is_system && !editing ? (
          <>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setName(tag.name);
                setEditing(true);
              }}
            >
              Переименовать
            </Button>
            <Button
              size="sm"
              variant="ghost"
              aria-label={`Удалить тег «${tag.name}»`}
              disabled={remove.isPending}
              onClick={() => {
                if (window.confirm(`Удалить тег «${tag.name}»? Он будет снят с ${tag.leads} лидов.`)) remove.mutate(tag.id);
              }}
            >
              <Trash2 className="size-3.5" aria-hidden />
            </Button>
          </>
        ) : null}
      </div>
      <InlineError error={update.error ?? remove.error} />
    </li>
  );
}
