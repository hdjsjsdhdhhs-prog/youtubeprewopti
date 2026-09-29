"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";
import { z } from "zod";

import { InlineError } from "@/components/states";
import { Button, Card, FieldError, Input, Label } from "@/components/ui";
import { useLogin } from "@/lib/api/hooks";
import { safeNext } from "@/lib/navigation";

const schema = z.object({
  email: z.email("Введите корректный email"),
  password: z.string().min(1, "Введите пароль"),
});
type Errors = Partial<Record<keyof z.infer<typeof schema>, string>>;

export function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const login = useLogin();
  const [errors, setErrors] = useState<Errors>({});

  function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const parsed = schema.safeParse({ email: form.get("email"), password: form.get("password") });
    if (!parsed.success) {
      const next: Errors = {};
      for (const issue of parsed.error.issues) next[issue.path[0] as keyof Errors] ??= issue.message;
      setErrors(next);
      return;
    }
    setErrors({});
    login.mutate(parsed.data, { onSuccess: () => router.replace(safeNext(params.get("next"))) });
  }

  return (
    <Card>
      <form onSubmit={onSubmit} noValidate className="space-y-4">
        <div>
          <Label htmlFor="email">Email</Label>
          <Input
            id="email"
            name="email"
            type="email"
            autoComplete="username"
            autoFocus
            aria-invalid={!!errors.email}
            aria-describedby="email-error"
          />
          <FieldError id="email-error" message={errors.email} />
        </div>
        <div>
          <Label htmlFor="password">Пароль</Label>
          <Input
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            aria-invalid={!!errors.password}
            aria-describedby="password-error"
          />
          <FieldError id="password-error" message={errors.password} />
        </div>
        <InlineError error={login.error} />
        <Button type="submit" className="w-full" disabled={login.isPending}>
          {login.isPending ? "Вход…" : "Войти"}
        </Button>
      </form>
    </Card>
  );
}
