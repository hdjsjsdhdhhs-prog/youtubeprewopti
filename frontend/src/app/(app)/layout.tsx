"use client";

import type { ReactNode } from "react";

import { AppShell } from "@/components/app-shell";
import { ErrorState, LoadingState } from "@/components/states";
import { ApiError } from "@/lib/api/client";
import { useMe } from "@/lib/api/hooks";

/** Authenticated area: resolves the session once; 401 is redirected to /login by the query cache. */
export default function AuthenticatedLayout({ children }: { children: ReactNode }) {
  const me = useMe();

  if (me.isPending || (me.error instanceof ApiError && me.error.isUnauthorized)) {
    return <LoadingState label="Проверка сессии…" />;
  }
  if (me.isError) {
    return (
      <div className="p-6">
        <ErrorState error={me.error} onRetry={() => me.refetch()} />
      </div>
    );
  }
  return <AppShell me={me.data}>{children}</AppShell>;
}
