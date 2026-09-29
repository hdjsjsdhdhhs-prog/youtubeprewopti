import type { Metadata } from "next";
import { Suspense } from "react";

import { LoginForm } from "./login-form";

export const metadata: Metadata = { title: "Вход" };

export default function LoginPage() {
  return (
    <main className="flex min-h-full items-center justify-center p-6">
      <div className="w-full max-w-sm">
        <h1 className="mb-1 text-xl font-semibold tracking-tight">YT Lead Intelligence</h1>
        <p className="mb-6 text-sm text-zinc-500">Войдите, чтобы продолжить</p>
        {/* useSearchParams (?next=) needs a Suspense boundary for static prerendering. */}
        <Suspense>
          <LoginForm />
        </Suspense>
      </div>
    </main>
  );
}
