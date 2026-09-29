"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback } from "react";

export type UrlPatch = Record<string, string | number | null | undefined>;

/**
 * Filters live in the URL (shareable, back/forward work) — ADR-0005.
 * Empty values remove the key; ``replace`` avoids a history entry per keystroke.
 */
export function useUrlState(): [URLSearchParams, (patch: UrlPatch) => void] {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();

  const update = useCallback(
    (patch: UrlPatch) => {
      const next = new URLSearchParams(params.toString());
      for (const [k, v] of Object.entries(patch)) {
        if (v === null || v === undefined || v === "") next.delete(k);
        else next.set(k, String(v));
      }
      const qs = next.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [params, pathname, router],
  );

  return [params, update];
}

export function intParam(params: URLSearchParams, key: string): number | undefined {
  const raw = params.get(key);
  if (raw === null || raw.trim() === "") return undefined;
  const n = Number(raw);
  return Number.isInteger(n) && n >= 0 ? n : undefined;
}

export function enumParam<T extends string>(params: URLSearchParams, key: string, allowed: readonly T[]): T | undefined {
  const raw = params.get(key);
  return allowed.includes(raw as T) ? (raw as T) : undefined;
}
