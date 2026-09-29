"use client";

import { Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { EmptyState, ErrorState, InlineError, LoadingState } from "@/components/states";
import { DemoBadge, ProjectStatusBadge } from "@/components/status-badges";
import { Button, Card, FieldError, Input, Label, PageHeader, Pager, Textarea } from "@/components/ui";
import { PROJECTS_PAGE, useCreateProject, useProjects, type ProjectStatus } from "@/lib/api/hooks";
import { formatCount, formatDate } from "@/lib/format";
import { parseProjectForm, type FormErrors } from "@/lib/forms";
import { enumParam, intParam, useUrlState } from "@/lib/url-state";
import { cn } from "@/lib/utils";

const STATUS_TABS: { value: ProjectStatus | undefined; label: string }[] = [
  { value: "active", label: "Активные" },
  { value: "archived", label: "Архив" },
  { value: undefined, label: "Все" },
];

export function ProjectsView() {
  const [params, setParams] = useUrlState();
  // Default tab is "active"; "all" is explicit in the URL.
  const rawStatus = params.get("status");
  const status = rawStatus === "all" ? undefined : (enumParam(params, "status", ["active", "archived"]) ?? "active");
  const offset = intParam(params, "offset") ?? 0;
  const projects = useProjects({ status, offset });
  const [creating, setCreating] = useState(false);

  return (
    <>
      <PageHeader
        title="Проекты"
        subtitle="Проект — набор поисковых запросов и найденных по ним каналов"
        actions={
          <Button onClick={() => setCreating((v) => !v)} aria-expanded={creating}>
            <Plus className="size-4" aria-hidden /> Новый проект
          </Button>
        }
      />

      {creating ? <CreateProjectForm onCancel={() => setCreating(false)} /> : null}

      <div role="tablist" aria-label="Статус" className="mb-3 flex gap-1">
        {STATUS_TABS.map((t) => {
          const active = t.value === status;
          return (
            <button
              key={t.label}
              role="tab"
              aria-selected={active}
              onClick={() => setParams({ status: t.value ?? "all", offset: null })}
              className={cn(
                "rounded-md px-3 py-1 text-sm",
                active ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900" : "text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800",
              )}
            >
              {t.label}
            </button>
          );
        })}
      </div>

      {projects.isPending ? (
        <LoadingState />
      ) : projects.isError ? (
        <ErrorState error={projects.error} onRetry={() => projects.refetch()} />
      ) : projects.data.items.length === 0 ? (
        <EmptyState
          title={status === "archived" ? "В архиве пусто" : "Проектов пока нет"}
          hint={status === "archived" ? undefined : "Создайте проект и добавьте в него поисковые запросы."}
          action={
            status !== "archived" && !creating ? (
              <Button onClick={() => setCreating(true)}>
                <Plus className="size-4" aria-hidden /> Новый проект
              </Button>
            ) : undefined
          }
        />
      ) : (
        <>
          <div className="overflow-hidden rounded-lg border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
            <table className="w-full text-sm">
              <thead className="bg-zinc-50 text-left text-xs uppercase tracking-wide text-zinc-500 dark:bg-zinc-900">
                <tr>
                  <th className="px-3 py-2 font-medium">Название</th>
                  <th className="px-3 py-2 font-medium">Статус</th>
                  <th className="px-3 py-2 text-right font-medium">Запросы</th>
                  <th className="px-3 py-2 text-right font-medium">Каналы</th>
                  <th className="px-3 py-2 font-medium">Язык / регион</th>
                  <th className="px-3 py-2 font-medium">Создан</th>
                </tr>
              </thead>
              <tbody className={cn("divide-y divide-zinc-100 dark:divide-zinc-800", projects.isPlaceholderData && "opacity-60")}>
                {projects.data.items.map((p) => (
                  <tr key={p.id} className="hover:bg-zinc-50 dark:hover:bg-zinc-800/50">
                    <td className="px-3 py-2">
                      <Link href={`/projects/${p.id}`} className="font-medium hover:underline">
                        {p.name}
                      </Link>{" "}
                      <DemoBadge show={p.is_demo} />
                      {p.description ? <p className="line-clamp-1 text-xs text-zinc-500">{p.description}</p> : null}
                    </td>
                    <td className="px-3 py-2">
                      <ProjectStatusBadge status={p.status} />
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">{formatCount(p.queries_count)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{formatCount(p.channels_count)}</td>
                    <td className="px-3 py-2 text-zinc-500">
                      {[p.language, p.region_code].filter(Boolean).join(" / ") || "—"}
                    </td>
                    <td className="px-3 py-2 text-zinc-500">{formatDate(p.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager
            offset={offset}
            limit={PROJECTS_PAGE}
            total={projects.data.total}
            onChange={(o) => setParams({ offset: o || null })}
          />
        </>
      )}
    </>
  );
}

type Field = "name" | "description" | "language" | "region_code";

function CreateProjectForm({ onCancel }: { onCancel: () => void }) {
  const router = useRouter();
  const create = useCreateProject();
  const [errors, setErrors] = useState<FormErrors<Field>>({});

  function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const parsed = parseProjectForm({
      name: form.get("name"),
      description: form.get("description"),
      language: form.get("language"),
      region_code: form.get("region_code"),
    });
    if (!parsed.ok) return setErrors(parsed.errors);
    setErrors({});
    create.mutate(parsed.data, { onSuccess: (p) => router.push(`/projects/${p.id}`) });
  }

  const field = (name: Field, label: string, props: React.InputHTMLAttributes<HTMLInputElement> = {}) => (
    <div>
      <Label htmlFor={`p-${name}`}>{label}</Label>
      <Input id={`p-${name}`} name={name} aria-invalid={!!errors[name]} aria-describedby={`p-${name}-err`} {...props} />
      <FieldError id={`p-${name}-err`} message={errors[name]} />
    </div>
  );

  return (
    <Card className="mb-4">
      <form onSubmit={onSubmit} noValidate className="grid gap-3 sm:grid-cols-4">
        <div className="sm:col-span-2">{field("name", "Название *", { autoFocus: true, maxLength: 200 })}</div>
        {field("language", "Язык", { placeholder: "ru" })}
        {field("region_code", "Регион", { placeholder: "RU", maxLength: 2 })}
        <div className="sm:col-span-4">
          <Label htmlFor="p-description">Описание</Label>
          <Textarea id="p-description" name="description" rows={2} className="font-sans" maxLength={5000} />
          <FieldError id="p-description-err" message={errors.description} />
        </div>
        <div className="flex items-center gap-2 sm:col-span-4">
          <Button type="submit" disabled={create.isPending}>
            {create.isPending ? "Создание…" : "Создать"}
          </Button>
          <Button variant="ghost" onClick={onCancel}>
            Отмена
          </Button>
          <InlineError error={create.error} />
        </div>
      </form>
    </Card>
  );
}
