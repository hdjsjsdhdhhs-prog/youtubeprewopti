"use client";

import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { ApiError } from "@/lib/api/client";
import { loginHref } from "@/lib/navigation";

function onUnauthorized(error: unknown) {
  // Session expired or revoked: go to login and come back afterwards.
  if (error instanceof ApiError && error.isUnauthorized && !window.location.pathname.startsWith("/login")) {
    window.location.replace(loginHref(window.location.pathname + window.location.search));
  }
}

function makeClient() {
  return new QueryClient({
    queryCache: new QueryCache({ onError: onUnauthorized }),
    mutationCache: new MutationCache({ onError: onUnauthorized }),
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        refetchOnWindowFocus: false,
        // Client errors (401/403/404/422) will not fix themselves; retry only network/5xx.
        retry: (count, error) =>
          count < 2 && !(error instanceof ApiError && error.status >= 400 && error.status < 500),
      },
    },
  });
}

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(makeClient);
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
