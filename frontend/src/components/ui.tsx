/**
 * Minimal UI primitives in the shadcn/ui style (Tailwind, copy-in, no runtime UI kit).
 * Replace with generated shadcn components when richer widgets (dialogs, menus) are needed.
 */
import { forwardRef, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode } from "react";

import { cn } from "@/lib/utils";

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary: "bg-zinc-900 text-white hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300",
  secondary:
    "border border-zinc-300 bg-white text-zinc-900 hover:bg-zinc-100 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100 dark:hover:bg-zinc-800",
  ghost: "text-zinc-700 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800",
  danger: "bg-red-600 text-white hover:bg-red-500",
};

export const Button = forwardRef<
  HTMLButtonElement,
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant; size?: "sm" | "md" }
>(function Button({ className, variant = "primary", size = "md", type = "button", ...props }, ref) {
  return (
    <button
      ref={ref}
      type={type}
      className={cn(
        "inline-flex items-center justify-center gap-1.5 rounded-md font-medium transition-colors",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-500",
        "disabled:pointer-events-none disabled:opacity-50",
        size === "sm" ? "h-7 px-2.5 text-xs" : "h-9 px-3.5 text-sm",
        BUTTON_VARIANTS[variant],
        className,
      )}
      {...props}
    />
  );
});

const FIELD =
  "w-full rounded-md border border-zinc-300 bg-white px-3 text-sm text-zinc-900 placeholder:text-zinc-400 " +
  "focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/30 " +
  "dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100 aria-invalid:border-red-500";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input(
  { className, ...props },
  ref,
) {
  return <input ref={ref} className={cn(FIELD, "h-9", className)} {...props} />;
});

export function Textarea({ className, ...props }: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn(FIELD, "py-2 font-mono", className)} {...props} />;
}

export function Select({ className, ...props }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cn(FIELD, "h-9 pr-8", className)} {...props} />;
}

export function Label({ children, htmlFor }: { children: ReactNode; htmlFor: string }) {
  return (
    <label htmlFor={htmlFor} className="mb-1 block text-xs font-medium text-zinc-600 dark:text-zinc-400">
      {children}
    </label>
  );
}

export function FieldError({ id, message }: { id: string; message?: string }) {
  if (!message) return null;
  return (
    <p id={id} className="mt-1 text-xs text-red-600">
      {message}
    </p>
  );
}

type Tone = "neutral" | "green" | "blue" | "amber" | "red" | "violet";
const TONES: Record<Tone, string> = {
  neutral: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  green: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  blue: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
  amber: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  red: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
  violet: "bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-300",
};

export function Badge({ tone = "neutral", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span title={title} className={cn("inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium", TONES[tone])}>
      {children}
    </span>
  );
}

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <section
      className={cn("rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900", className)}
    >
      {children}
    </section>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {subtitle ? <p className="mt-0.5 text-sm text-zinc-500">{subtitle}</p> : null}
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function Pager({
  offset,
  limit,
  total,
  onChange,
}: {
  offset: number;
  limit: number;
  total: number;
  onChange: (offset: number) => void;
}) {
  if (total <= limit) return null;
  const from = offset + 1;
  const to = Math.min(offset + limit, total);
  return (
    <div className="mt-3 flex items-center justify-end gap-2 text-sm text-zinc-500">
      <span>
        {from}–{to} из {total}
      </span>
      <Button size="sm" variant="secondary" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>
        Назад
      </Button>
      <Button size="sm" variant="secondary" disabled={to >= total} onClick={() => onChange(offset + limit)}>
        Вперёд
      </Button>
    </div>
  );
}
