import type { JobStatus, ProjectStatus, SearchQuery } from "@/lib/api/hooks";

import { Badge } from "./ui";

const JOB: Record<JobStatus, { label: string; tone: Parameters<typeof Badge>[0]["tone"] }> = {
  queued: { label: "В очереди", tone: "neutral" },
  running: { label: "Выполняется", tone: "blue" },
  retrying: { label: "Повтор", tone: "amber" },
  completed: { label: "Готово", tone: "green" },
  failed: { label: "Ошибка", tone: "red" },
  cancelled: { label: "Отменена", tone: "neutral" },
};

export const JOB_STATUS_LABELS = Object.fromEntries(Object.entries(JOB).map(([k, v]) => [k, v.label])) as Record<
  JobStatus,
  string
>;

export function JobStatusBadge({ status }: { status: JobStatus }) {
  const s = JOB[status];
  return <Badge tone={s.tone}>{s.label}</Badge>;
}

export function ProjectStatusBadge({ status }: { status: ProjectStatus }) {
  return status === "active" ? <Badge tone="green">Активен</Badge> : <Badge>Архив</Badge>;
}

const QUERY: Record<SearchQuery["status"], { label: string; tone: Parameters<typeof Badge>[0]["tone"] }> = {
  pending: { label: "Ожидает", tone: "neutral" },
  running: { label: "Идёт поиск", tone: "blue" },
  done: { label: "Выполнен", tone: "green" },
  failed: { label: "Ошибка", tone: "red" },
};

export function QueryStatusBadge({ status }: { status: SearchQuery["status"] }) {
  const s = QUERY[status];
  return <Badge tone={s.tone}>{s.label}</Badge>;
}

export function DemoBadge({ show }: { show: boolean }) {
  return show ? (
    <Badge tone="violet" title="Демо-данные (не из YouTube)">
      demo
    </Badge>
  ) : null;
}
