import type { TaxonomyLevel, TaxonomyNode } from "@/lib/api/hooks";

export const LEVEL_LABELS: Record<TaxonomyLevel, string> = { niche: "Ниша", topic: "Тема", subtopic: "Подтема" };

export type TaxonomyOption = { id: number; label: string; depth: number; node: TaxonomyNode };

/** Flat list of nodes in tree order (niche → its topics → their subtopics), labels indented by depth. */
export function taxonomyOptions(nodes: readonly TaxonomyNode[]): TaxonomyOption[] {
  const children = new Map<number | null, TaxonomyNode[]>();
  for (const n of nodes) {
    const key = n.parent_id ?? null;
    children.set(key, [...(children.get(key) ?? []), n]);
  }
  for (const list of children.values()) list.sort((a, b) => a.name.localeCompare(b.name, "ru"));

  const out: TaxonomyOption[] = [];
  const seen = new Set<number>();
  const walk = (parent: number | null, depth: number) => {
    for (const n of children.get(parent) ?? []) {
      if (seen.has(n.id)) continue; // defensive: never loop on malformed data
      seen.add(n.id);
      out.push({ id: n.id, label: `${"\u00a0\u00a0".repeat(depth)}${n.name}`, depth, node: n });
      walk(n.id, depth + 1);
    }
  };
  walk(null, 0);
  return out;
}

/** "Finance › Investing › ETF" for a node id. */
export function taxonomyPath(nodes: readonly TaxonomyNode[], id: number | null | undefined): string | null {
  if (id === null || id === undefined) return null;
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const parts: string[] = [];
  let cur = byId.get(id);
  while (cur && parts.length < 5) {
    parts.unshift(cur.name);
    cur = cur.parent_id !== null && cur.parent_id !== undefined ? byId.get(cur.parent_id) : undefined;
  }
  return parts.length ? parts.join(" › ") : null;
}
