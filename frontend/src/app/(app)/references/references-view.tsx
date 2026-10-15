"use client";

import { Trash2, Upload } from "lucide-react";
import { useRef, useState } from "react";

import { EmptyState, ErrorState, InlineError, LoadingState } from "@/components/states";
import { Badge, Button, Card, Input, Label, PageHeader, Textarea } from "@/components/ui";
import {
  useCreateReference,
  useDeleteReference,
  useReferences,
  useUpdateReference,
  type StyleReference,
} from "@/lib/api/generation-hooks";
import { formatDate } from "@/lib/format";
import { REFERENCE_TYPES, parseTags, referenceFileError, titleFromFileName } from "@/lib/generation";

type Meta = { title: string; description: string; style: string; category: string; tags: string };
const EMPTY_META: Meta = { title: "", description: "", style: "", category: "", tags: "" };

function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error ?? new Error("read failed"));
    reader.readAsDataURL(file);
  });
}

/** ТЗ §43: style references the image model borrows style, colour and mood from (never copies). */
export function ReferencesView() {
  const refs = useReferences();
  return (
    <div className="max-w-5xl">
      <PageHeader
        title="Референсы"
        subtitle="Примеры стиля для генерации превью: в задании можно выбрать до 3 референсов — модель берёт стиль, цвет и настроение, но не копирует"
      />
      <UploadForm />
      {refs.isPending ? (
        <LoadingState />
      ) : refs.isError ? (
        <ErrorState error={refs.error} onRetry={() => void refs.refetch()} />
      ) : refs.data.length === 0 ? (
        <EmptyState title="Референсов пока нет" hint="Загрузите изображение выше: JPEG, PNG или WebP до 10 МБ." />
      ) : (
        <ul className="grid grid-cols-[repeat(auto-fill,minmax(16rem,1fr))] gap-4">
          {refs.data.map((r) => (
            <ReferenceCard key={r.id} r={r} />
          ))}
        </ul>
      )}
    </div>
  );
}

function MetaFields({ prefix, meta, onChange }: { prefix: string; meta: Meta; onChange: (m: Meta) => void }) {
  const field = (key: keyof Meta, label: string, props: { maxLength: number; placeholder?: string }) => (
    <div>
      <Label htmlFor={`${prefix}-${key}`}>{label}</Label>
      <Input id={`${prefix}-${key}`} value={meta[key]} onChange={(e) => onChange({ ...meta, [key]: e.target.value })} {...props} />
    </div>
  );
  return (
    <>
      {field("title", "Название", { maxLength: 200 })}
      {field("style", "Стиль", { maxLength: 100, placeholder: "например, неон, минимализм" })}
      {field("category", "Категория", { maxLength: 100, placeholder: "например, финансы" })}
      {field("tags", "Теги через запятую", { maxLength: 1300 })}
      <div className="sm:col-span-2">
        <Label htmlFor={`${prefix}-description`}>Описание</Label>
        <Textarea
          id={`${prefix}-description`}
          rows={2}
          maxLength={500}
          className="font-sans"
          value={meta.description}
          onChange={(e) => onChange({ ...meta, description: e.target.value })}
        />
      </div>
    </>
  );
}

function UploadForm() {
  const create = useCreateReference();
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [meta, setMeta] = useState<Meta>(EMPTY_META);
  const [reading, setReading] = useState(false);

  const pick = (f: File | null) => {
    create.reset();
    setFile(f);
    setFileError(f ? referenceFileError(f) : null);
    if (f && !meta.title.trim()) setMeta((m) => ({ ...m, title: titleFromFileName(f.name) }));
  };

  return (
    <Card className="mb-4">
      <form
        className="grid gap-3 sm:grid-cols-2"
        onSubmit={async (e) => {
          e.preventDefault();
          if (!file || fileError || !meta.title.trim()) return;
          setReading(true);
          let image_base64: string;
          try {
            image_base64 = await readAsDataUrl(file);
          } catch {
            setFileError("Не удалось прочитать файл.");
            return;
          } finally {
            setReading(false);
          }
          create.mutate(
            {
              image_base64,
              title: meta.title.trim(),
              description: meta.description.trim(),
              style: meta.style.trim(),
              category: meta.category.trim(),
              tags: parseTags(meta.tags),
            },
            {
              onSuccess: () => {
                setMeta(EMPTY_META);
                setFile(null);
                if (fileRef.current) fileRef.current.value = "";
              },
            },
          );
        }}
      >
        <div className="sm:col-span-2">
          <Label htmlFor="ref-file">Изображение (JPEG, PNG, WebP, до 10 МБ)</Label>
          <input
            ref={fileRef}
            id="ref-file"
            type="file"
            accept={REFERENCE_TYPES.join(",")}
            onChange={(e) => pick(e.target.files?.[0] ?? null)}
            className="block text-sm"
          />
          {fileError ? (
            <p role="alert" className="mt-1 text-xs text-red-600">
              {fileError}
            </p>
          ) : null}
        </div>
        <MetaFields prefix="ref-new" meta={meta} onChange={setMeta} />
        <div className="flex items-center gap-2 sm:col-span-2">
          <Button type="submit" disabled={!file || !!fileError || !meta.title.trim() || reading || create.isPending}>
            <Upload className="size-4" aria-hidden /> {create.isPending || reading ? "Загрузка…" : "Загрузить референс"}
          </Button>
          <InlineError error={create.error} />
        </div>
      </form>
    </Card>
  );
}

const metaOf = (r: StyleReference): Meta => ({
  title: r.title,
  description: r.description,
  style: r.style,
  category: r.category,
  tags: r.tags.join(", "),
});

function ReferenceCard({ r }: { r: StyleReference }) {
  const update = useUpdateReference();
  const remove = useDeleteReference();
  const [editing, setEditing] = useState(false);
  const [meta, setMeta] = useState<Meta>(() => metaOf(r));

  return (
    <li className="min-w-0 rounded-lg border border-zinc-200 bg-white p-2 dark:border-zinc-800 dark:bg-zinc-900">
      {/* Served by our API with the session cookie; next/image would fetch it server-side without it. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={r.image_url}
        alt={`Референс: ${r.title}`}
        width={r.width}
        height={r.height}
        loading="lazy"
        className="aspect-video w-full rounded object-cover"
      />
      {editing ? (
        <form
          className="mt-2 grid gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (!meta.title.trim()) return;
            update.mutate(
              {
                id: r.id,
                body: {
                  title: meta.title.trim(),
                  description: meta.description.trim(),
                  style: meta.style.trim(),
                  category: meta.category.trim(),
                  tags: parseTags(meta.tags),
                },
              },
              { onSuccess: () => setEditing(false) },
            );
          }}
        >
          <MetaFields prefix={`ref-${r.id}`} meta={meta} onChange={setMeta} />
          <div className="flex gap-1">
            <Button size="sm" type="submit" disabled={update.isPending || !meta.title.trim()}>
              Сохранить
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
              Отмена
            </Button>
          </div>
        </form>
      ) : (
        <div className="mt-2 space-y-1 text-sm">
          <p className="font-medium" title={r.title}>
            {r.title}
          </p>
          <p className="flex flex-wrap gap-1">
            {r.style ? <Badge tone="blue">{r.style}</Badge> : null}
            {r.category ? <Badge>{r.category}</Badge> : null}
            {r.tags.map((t) => (
              <Badge key={t} tone="violet">
                {t}
              </Badge>
            ))}
          </p>
          {r.description ? <p className="text-xs text-zinc-600 dark:text-zinc-400">{r.description}</p> : null}
          <p className="text-xs text-zinc-500">
            {r.width}×{r.height} · {formatDate(r.created_at)}
          </p>
          <div className="flex gap-1">
            <Button
              size="sm"
              variant="secondary"
              onClick={() => {
                setMeta(metaOf(r));
                setEditing(true);
              }}
            >
              Изменить
            </Button>
            <Button
              size="sm"
              variant="ghost"
              aria-label={`Удалить референс «${r.title}»`}
              disabled={remove.isPending}
              onClick={() => {
                if (window.confirm(`Удалить референс «${r.title}»? Прошлые генерации сохранят своё изображение.`)) {
                  remove.mutate(r.id);
                }
              }}
            >
              <Trash2 className="size-3.5" aria-hidden />
            </Button>
          </div>
        </div>
      )}
      <InlineError error={update.error ?? remove.error} />
    </li>
  );
}
