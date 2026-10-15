/** Thumbnail generation (Phase 5): labels, the generation form and helpers. Records keyed by API enums make `tsc`
 * fail when the backend adds a status without a label here. */
import { ApiError, type Schemas } from "@/lib/api/client";

export type VariantStatus = Schemas["VariantStatus"];
export type GenerationStatus = Schemas["GenerationStatus"];
type Decision = Schemas["VariantDecision"]["decision"];
type GenerationIn = Schemas["GenerationIn"];
type Size = NonNullable<GenerationIn["size"]>;
type Quality = NonNullable<GenerationIn["quality"]>;

/** Backend `MAX_VARIANTS` / `MAX_REFERENCES` / instructions length (generation/schemas.py). */
export const MAX_VARIANTS = 6;
export const MAX_REFERENCES = 3;
export const MAX_INSTRUCTIONS = 2000;
/** Backend limit of a reference upload. */
export const MAX_REFERENCE_BYTES = 10 * 1024 * 1024;
export const REFERENCE_TYPES = ["image/jpeg", "image/png", "image/webp"] as const;
export const MAX_REFERENCE_TAGS = 20;

export const VARIANT_STATUS_LABELS: Record<VariantStatus, string> = {
  pending: "генерируется",
  ready: "ждёт решения",
  failed: "ошибка",
  refused: "отклонено модерацией",
  approved: "одобрен",
  rejected: "отклонён",
  final: "финальный образец",
};

export const VARIANT_STATUS_TONES: Record<VariantStatus, "neutral" | "green" | "blue" | "amber" | "red" | "violet"> = {
  pending: "neutral",
  ready: "blue",
  failed: "red",
  refused: "amber",
  approved: "green",
  rejected: "neutral",
  final: "violet",
};

export const GENERATION_STATUS_LABELS: Record<GenerationStatus, string> = {
  queued: "в очереди",
  running: "идёт",
  completed: "завершена",
  failed: "остановлена",
};

/** Composition directions of the variants (backend `brief.STRATEGIES`). */
export const STRATEGY_LABELS: Record<string, string> = {
  subject_closeup: "Крупный план и эмоция",
  object_contrast: "Ключевой объект, контраст",
  minimal: "Минимализм",
  dynamic_depth: "Диагональ и глубина",
  cinematic: "Кинематографичный свет",
  high_energy: "Яркий, энергичный",
};

export function strategyLabel(key: string): string {
  return STRATEGY_LABELS[key] ?? key;
}

export const DECISION_LABELS: Record<Decision, string> = {
  approve: "Одобрить",
  reject: "Отклонить",
  final: "Финальный образец",
  reset: "Сбросить решение",
};

/** Decisions that make sense from the current status (the backend accepts any of them for a generated variant). */
export function variantDecisions(status: VariantStatus): Decision[] {
  switch (status) {
    case "ready":
      return ["approve", "reject", "final"];
    case "approved":
      return ["final", "reject", "reset"];
    case "rejected":
      return ["approve", "reset"];
    case "final":
      return ["approve", "reset"];
    default:
      return [];
  }
}

/** A variant with an image can be the base of «create more like this». */
export function canMakeMoreLike(v: { status: VariantStatus; image_url: string | null }): boolean {
  return v.image_url !== null && v.status !== "pending" && v.status !== "failed" && v.status !== "refused";
}

/** Lead statuses a generation can start from (backend `generation.service.STARTABLE`). */
export const GENERATION_STARTABLE: ReadonlySet<Schemas["LeadStatus"]> = new Set([
  "discovered",
  "analyzed",
  "shortlisted",
  "selected",
  "generation_pending",
  "generated",
  "failed",
]);

/** Problems of an original arrive as free-form objects: keep the fields the comparison shows. */
export function originalProblems(problems: { [key: string]: unknown }[]): { code: string; severity: string; description: string }[] {
  return problems.map((p) => ({
    code: typeof p.code === "string" ? p.code : "other",
    severity: typeof p.severity === "string" ? p.severity : "",
    description: typeof p.description === "string" ? p.description : "",
  }));
}

export function isGenerationActive(g: { status: GenerationStatus }): boolean {
  return g.status === "queued" || g.status === "running";
}

/** Known conflict codes in Russian; everything else shows the backend message. */
const ERROR_MESSAGES: Record<string, string> = {
  generation_running: "Для этого лида генерация уже идёт — дождитесь её завершения.",
  lead_status: "В текущем статусе лида генерация недоступна.",
  variant_not_ready: "Решение можно принять только по сгенерированному варианту.",
  ai_not_configured: "Модель генерации не настроена: задайте YTL_VIBECODE_API_KEY (или YTL_AI_PROVIDER=mock для демо).",
};

export function generationErrorMessage(error: unknown): string | null {
  return error instanceof ApiError ? (ERROR_MESSAGES[error.code] ?? null) : null;
}

// --- the generation form ----------------------------------------------------------------------

export const SIZE_LABELS: Record<Size, string> = {
  "1536x1024": "1536×1024 (16:9, как YouTube)",
  "1024x1024": "1024×1024",
  "1024x1536": "1024×1536",
  auto: "auto",
};
export const QUALITY_LABELS: Record<Quality, string> = {
  low: "low — дешевле",
  medium: "medium",
  high: "high — дороже",
  auto: "auto",
};

export type GenerationForm = {
  variant_count: number;
  instructions: string;
  reference_ids: number[];
  source_video_id: number | null; // null = the backend default (the weakest audited thumbnail)
  use_source_image: boolean;
  parent_variant_id: number | null;
  model_key: string; // "" = first model of the route
  size: Size;
  quality: "" | Quality; // "" = the model's default
};

export const DEFAULT_GENERATION_FORM: GenerationForm = {
  variant_count: 4,
  instructions: "",
  reference_ids: [],
  source_video_id: null,
  use_source_image: true,
  parent_variant_id: null,
  model_key: "",
  size: "1536x1024",
  quality: "",
};

export function generationFormErrors(f: GenerationForm): string[] {
  const errors: string[] = [];
  if (!Number.isInteger(f.variant_count) || f.variant_count < 1 || f.variant_count > MAX_VARIANTS) {
    errors.push(`Число вариантов — от 1 до ${MAX_VARIANTS}.`);
  }
  if (f.reference_ids.length > MAX_REFERENCES) errors.push(`Не больше ${MAX_REFERENCES} референсов.`);
  if (f.instructions.trim().length > MAX_INSTRUCTIONS) errors.push(`Инструкции — не длиннее ${MAX_INSTRUCTIONS} символов.`);
  return errors;
}

/** Form -> API body; unset optional fields are omitted so the backend applies its defaults. */
export function generationRequest(f: GenerationForm): GenerationIn {
  const body: GenerationIn = {
    variant_count: f.variant_count,
    instructions: f.instructions.trim(),
    reference_ids: [...new Set(f.reference_ids)],
    use_source_image: f.use_source_image,
    size: f.size,
  };
  if (f.source_video_id !== null) body.source_video_id = f.source_video_id;
  if (f.parent_variant_id !== null) body.parent_variant_id = f.parent_variant_id;
  if (f.model_key) body.model_key = f.model_key;
  if (f.quality) body.quality = f.quality;
  return body;
}

/** Whether the form still describes exactly the estimate on screen (the start confirms that estimate only). */
export function sameRequest(a: GenerationIn | null, b: GenerationIn | null): boolean {
  if (a === null || b === null) return a === b;
  const norm = (r: GenerationIn) =>
    JSON.stringify(Object.keys(r).sort().map((k) => {
      const v = r[k as keyof GenerationIn];
      return [k, Array.isArray(v) ? [...v].sort((x, y) => Number(x) - Number(y)) : v];
    }));
  return norm(a) === norm(b);
}

// --- references -------------------------------------------------------------------------------

export function referenceFileError(file: { type: string; size: number }): string | null {
  if (!(REFERENCE_TYPES as readonly string[]).includes(file.type)) return "Поддерживаются JPEG, PNG и WebP.";
  if (file.size > MAX_REFERENCE_BYTES) return "Файл больше 10 МБ.";
  return null;
}

/** "a, b , a" -> ["a", "b"]: trimmed, unique, at most 20 tags of ≤ 60 characters. */
export function parseTags(text: string): string[] {
  const tags = text
    .split(",")
    .map((t) => t.trim().slice(0, 60))
    .filter(Boolean);
  return [...new Set(tags)].slice(0, MAX_REFERENCE_TAGS);
}

/** "cover.final.png" -> "cover.final" (default title of an uploaded reference). */
export function titleFromFileName(name: string): string {
  const base = name.replace(/\.[^.]+$/, "").trim();
  return (base || name).slice(0, 200);
}
