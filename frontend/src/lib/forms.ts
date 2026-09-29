import { z } from "zod";

import type { ProjectCreate } from "./api/hooks";

/** Mirrors backend constraints (app/domains/projects/schemas.py) so most errors are caught client-side. */
const optional = (s: z.ZodString) =>
  z
    .string()
    .trim()
    .transform((v) => (v === "" ? undefined : v))
    .pipe(s.optional());

export const projectFormSchema = z.object({
  name: z.string().trim().min(1, "Введите название").max(200, "Не длиннее 200 символов"),
  description: optional(z.string().max(5000, "Не длиннее 5000 символов")),
  language: optional(z.string().regex(/^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$/, "Код языка, например ru или en-US")),
  region_code: optional(z.string().regex(/^[A-Za-z]{2}$/, "Двухбуквенный код страны, например RU")).transform((v) =>
    v?.toUpperCase(),
  ),
});

export type FormErrors<K extends string> = Partial<Record<K, string>>;

export function parseProjectForm(
  values: Record<string, FormDataEntryValue | null>,
): { ok: true; data: ProjectCreate } | { ok: false; errors: FormErrors<keyof z.input<typeof projectFormSchema>> } {
  const parsed = projectFormSchema.safeParse(
    Object.fromEntries(Object.entries(values).map(([k, v]) => [k, typeof v === "string" ? v : ""])),
  );
  if (parsed.success) return { ok: true, data: parsed.data };
  const errors: FormErrors<keyof z.input<typeof projectFormSchema>> = {};
  for (const issue of parsed.error.issues) {
    const key = issue.path[0] as keyof typeof errors;
    errors[key] ??= issue.message;
  }
  return { ok: false, errors };
}

/** Lines that will be sent to the bulk import endpoint (blank lines are ignored server-side too). */
export function countQueryLines(text: string): number {
  return text.split(/\r?\n/).filter((l) => l.trim() !== "").length;
}
