"use client";

import { Pencil, Trash2 } from "lucide-react";
import { useState } from "react";

import { EmptyState, ErrorState, InlineError, LoadingState } from "@/components/states";
import { Badge, Button, Card, Input, Label, PageHeader, Textarea } from "@/components/ui";
import {
  useArchiveOfferTemplate,
  useOfferTemplates,
  useSaveOfferTemplate,
  type OfferTemplate,
} from "@/lib/api/offers-hooks";
import { formatDateTime } from "@/lib/format";

const MAX_BODY = 4000;

/** Offer templates: the owner's guidance for the AI (tone, structure, what to offer). Editing keeps the old
 * version for offers already written from it. */
export function OfferTemplatesView() {
  const templates = useOfferTemplates();
  const save = useSaveOfferTemplate();
  const archive = useArchiveOfferTemplate();
  const [editing, setEditing] = useState<OfferTemplate | null>(null);
  const [name, setName] = useState("");
  const [body, setBody] = useState("");

  const reset = () => {
    setEditing(null);
    setName("");
    setBody("");
  };

  return (
    <div className="max-w-4xl">
      <PageHeader
        title="Шаблоны офферов"
        subtitle="Указания для AI: тон, структура, что предложить. Цифры и факты о канале AI берёт только из данных"
      />
      <Card className="mb-4">
        <form
          className="space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (!name.trim() || !body.trim()) return;
            save.mutate({ id: editing?.id, body: { name: name.trim(), body: body.trim() } }, { onSuccess: reset });
          }}
        >
          <h2 className="text-sm font-semibold">{editing ? `Новая версия шаблона «${editing.name}»` : "Новый шаблон"}</h2>
          <div>
            <Label htmlFor="tpl-name">Название</Label>
            <Input id="tpl-name" value={name} maxLength={200} onChange={(e) => setName(e.currentTarget.value)} />
          </div>
          <div>
            <Label htmlFor="tpl-body">Текст шаблона</Label>
            <Textarea
              id="tpl-body"
              rows={6}
              maxLength={MAX_BODY}
              value={body}
              className="font-sans"
              placeholder="Например: коротко, на «вы»; начни с конкретного наблюдения о превью; предложи бесплатно переделать одно превью"
              onChange={(e) => setBody(e.currentTarget.value)}
            />
          </div>
          <div className="flex gap-2">
            <Button type="submit" disabled={save.isPending || !name.trim() || !body.trim()}>
              {editing ? "Сохранить как новую версию" : "Добавить"}
            </Button>
            {editing ? (
              <Button variant="ghost" onClick={reset}>
                Отмена
              </Button>
            ) : null}
          </div>
          <InlineError error={save.error} />
        </form>
      </Card>

      {templates.isPending ? (
        <LoadingState />
      ) : templates.isError ? (
        <ErrorState error={templates.error} onRetry={() => void templates.refetch()} />
      ) : templates.data.length === 0 ? (
        <EmptyState title="Шаблонов нет" hint="Без шаблона AI пишет по своей структуре: наблюдение → проблема → инсайт → ценность → образец → CTA." />
      ) : (
        <ul className="space-y-3">
          {templates.data.map((t) => (
            <li key={t.id}>
              <Card>
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="text-sm font-semibold">{t.name}</h3>
                  <Badge>v{t.version}</Badge>
                  <span className="text-xs text-zinc-500">{formatDateTime(t.created_at)}</span>
                  <span className="flex-1" />
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={`Изменить шаблон ${t.name}`}
                    onClick={() => {
                      setEditing(t);
                      setName(t.name);
                      setBody(t.body);
                    }}
                  >
                    <Pencil className="size-3.5" aria-hidden />
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={`Удалить шаблон ${t.name}`}
                    disabled={archive.isPending}
                    onClick={() => archive.mutate(t.id)}
                  >
                    <Trash2 className="size-3.5" aria-hidden />
                  </Button>
                </div>
                <p className="mt-2 whitespace-pre-line text-sm text-zinc-700 dark:text-zinc-300">{t.body}</p>
              </Card>
            </li>
          ))}
        </ul>
      )}
      <InlineError error={archive.error} />
    </div>
  );
}
