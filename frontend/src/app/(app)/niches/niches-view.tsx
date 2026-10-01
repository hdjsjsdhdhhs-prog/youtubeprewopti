"use client";

import { useMemo, useState } from "react";

import { EmptyState, ErrorState, InlineError, LoadingState } from "@/components/states";
import { Badge, Button, Card, Label, PageHeader, Select, Textarea } from "@/components/ui";
import { useCreateTaxonomy, useTaxonomy, type TaxonomyNode } from "@/lib/api/hooks";
import { countQueryLines } from "@/lib/forms";
import { LEVEL_LABELS, taxonomyOptions } from "@/lib/taxonomy";

/** Niche > Topic > Subtopic vocabulary (§54): used to tag queries and to filter channels. */
export function NichesView() {
  const taxonomy = useTaxonomy();
  const options = useMemo(() => taxonomyOptions(taxonomy.data ?? []), [taxonomy.data]);

  return (
    <>
      <PageHeader
        title="Ниши"
        subtitle="Ниша › тема › подтема. Запросы помечаются нишей, по ней же фильтруются каналы (с учётом вложенных тем)."
      />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <Card>
          {taxonomy.isPending ? (
            <LoadingState />
          ) : taxonomy.isError ? (
            <ErrorState error={taxonomy.error} onRetry={() => taxonomy.refetch()} />
          ) : options.length === 0 ? (
            <EmptyState title="Ниш пока нет" hint="Добавьте их списком справа — по одной на строку." />
          ) : (
            <ul className="space-y-1 text-sm">
              {options.map((o) => (
                <li key={o.id} className="flex items-center gap-2" style={{ paddingLeft: `${o.depth * 1.25}rem` }}>
                  <span className={o.depth === 0 ? "font-medium" : undefined}>{o.node.name}</span>
                  <Badge tone={o.depth === 0 ? "blue" : "neutral"}>{LEVEL_LABELS[o.node.level]}</Badge>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <AddNodes nodes={taxonomy.data ?? []} />
      </div>
    </>
  );
}

function AddNodes({ nodes }: { nodes: TaxonomyNode[] }) {
  const [text, setText] = useState("");
  const [parent, setParent] = useState("");
  const create = useCreateTaxonomy();
  // Subtopics cannot have children.
  const parents = useMemo(() => taxonomyOptions(nodes).filter((o) => o.node.level !== "subtopic"), [nodes]);
  const lines = countQueryLines(text);

  return (
    <Card>
      <h2 className="mb-2 text-sm font-semibold">Добавить</h2>
      <form
        className="space-y-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (lines > 0) {
            create.mutate({ text, parent_id: parent ? Number(parent) : null }, { onSuccess: () => setText("") });
          }
        }}
      >
        <Label htmlFor="node-parent">Куда</Label>
        <Select id="node-parent" value={parent} onChange={(e) => setParent(e.target.value)}>
          <option value="">Новые ниши (верхний уровень)</option>
          {parents.map((o) => (
            <option key={o.id} value={o.id}>
              {o.label} — {o.node.level === "niche" ? "темы" : "подтемы"}
            </option>
          ))}
        </Select>
        <Label htmlFor="node-names">Названия, по одному на строку</Label>
        <Textarea
          id="node-names"
          rows={8}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={"Finance\nGaming\nEducation\n…"}
        />
        <div className="flex items-center justify-between">
          <span className="text-xs text-zinc-500">{lines} строк</span>
          <Button type="submit" disabled={lines === 0 || create.isPending}>
            {create.isPending ? "Добавление…" : "Добавить"}
          </Button>
        </div>
        <InlineError error={create.error} />
        {create.data ? (
          <p role="status" className="text-xs text-zinc-600">
            Добавлено: <b>{create.data.created}</b> · уже были: <b>{create.data.duplicates}</b>
            {create.data.rejected.length ? ` · отклонено: ${create.data.rejected.length}` : ""}
          </p>
        ) : null}
      </form>
    </Card>
  );
}
