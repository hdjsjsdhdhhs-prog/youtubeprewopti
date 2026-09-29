import { AlertTriangle, Inbox, Loader2 } from "lucide-react";
import type { ReactNode } from "react";

import { ApiError } from "@/lib/api/client";

import { Button } from "./ui";

/** Mandatory loading / empty / error states for every data view (§65). */

export function LoadingState({ label = "Загрузка…" }: { label?: string }) {
  return (
    <div role="status" className="flex items-center justify-center gap-2 py-16 text-sm text-zinc-500">
      <Loader2 className="size-4 animate-spin" aria-hidden />
      {label}
    </div>
  );
}

export function EmptyState({ title, hint, action }: { title: string; hint?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-zinc-300 py-14 text-center dark:border-zinc-700">
      <Inbox className="size-6 text-zinc-400" aria-hidden />
      <p className="text-sm font-medium">{title}</p>
      {hint ? <p className="max-w-md text-sm text-zinc-500">{hint}</p> : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "Непредвиденная ошибка. Обновите страницу.";
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const code = error instanceof ApiError ? error.code : undefined;
  return (
    <div
      role="alert"
      className="flex flex-col items-center justify-center gap-2 rounded-lg border border-red-200 bg-red-50 py-12 text-center dark:border-red-900 dark:bg-red-950/40"
    >
      <AlertTriangle className="size-6 text-red-500" aria-hidden />
      <p className="text-sm font-medium text-red-800 dark:text-red-300">{errorMessage(error)}</p>
      {code ? <p className="font-mono text-xs text-red-600/80">{code}</p> : null}
      {onRetry ? (
        <Button variant="secondary" size="sm" className="mt-2" onClick={onRetry}>
          Повторить
        </Button>
      ) : null}
    </div>
  );
}

/** Inline error for mutations (forms, row actions). */
export function InlineError({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <p role="alert" className="text-sm text-red-600">
      {errorMessage(error)}
    </p>
  );
}
