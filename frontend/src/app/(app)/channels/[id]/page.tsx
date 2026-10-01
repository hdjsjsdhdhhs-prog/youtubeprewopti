"use client";

import { ArrowLeft, Download, ExternalLink, ImageOff } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { EmptyState, ErrorState, InlineError, LoadingState } from "@/components/states";
import { DemoBadge } from "@/components/status-badges";
import { Badge, Button, Card, PageHeader, Pager } from "@/components/ui";
import {
  VIDEOS_PAGE,
  useChannel,
  useChannelVideos,
  useDownloadChannelThumbnails,
  type ChannelDetail,
  type Video,
} from "@/lib/api/hooks";
import { formatCount, formatDate, formatDateTime, formatDuration, formatRatio } from "@/lib/format";
import { cn } from "@/lib/utils";

export default function ChannelDetailPage() {
  const { id: rawId } = useParams<{ id: string }>();
  const id = Number(rawId);
  if (!Number.isInteger(id) || id <= 0) return <EmptyState title="Канал не найден" />;
  return <ChannelView id={id} />;
}

function ChannelView({ id }: { id: number }) {
  const channel = useChannel(id);

  if (channel.isPending) return <LoadingState />;
  if (channel.isError) {
    return (
      <>
        <BackLink />
        <ErrorState error={channel.error} onRetry={() => channel.refetch()} />
      </>
    );
  }
  const c = channel.data;
  return (
    <>
      <BackLink />
      <PageHeader
        title={
          <span className="flex items-center gap-2">
            {c.avatar_url ? (
              // External YouTube avatar; next/image would proxy it through the server for no benefit.
              // eslint-disable-next-line @next/next/no-img-element
              <img src={c.avatar_url} alt="" referrerPolicy="no-referrer" className="size-9 rounded-full bg-zinc-200" />
            ) : null}
            {c.title} <DemoBadge show={c.is_demo} />
          </span>
        }
        subtitle={
          c.is_demo ? (
            "Синтетический демо-канал — на YouTube его нет."
          ) : (
            <a href={c.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 hover:underline">
              {c.handle ?? c.youtube_channel_id} <ExternalLink className="size-3" aria-hidden />
            </a>
          )
        }
      />
      <Stats channel={c} />
      <Performance channel={c} />
      {c.description ? <p className="mt-3 max-w-3xl whitespace-pre-line text-sm text-zinc-600 dark:text-zinc-400">{c.description}</p> : null}
      <Discoveries channel={c} />
      <Thumbnails channel={c} />
    </>
  );
}

function BackLink() {
  return (
    <Link href="/channels" className="mb-3 inline-flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100">
      <ArrowLeft className="size-4" aria-hidden /> Каналы
    </Link>
  );
}

function Stats({ channel: c }: { channel: ChannelDetail }) {
  const rows: [string, string][] = [
    ["Подписчики", c.subscribers_hidden ? "скрыто" : formatCount(c.subscriber_count)],
    ["Просмотры", formatCount(c.view_count)],
    ["Видео на канале", formatCount(c.video_count)],
    ["Видео в базе", formatCount(c.videos_stored)],
    ["Страна", c.country ?? "—"],
    ["Язык", c.default_language ?? "—"],
    ["Создан", formatDate(c.published_at)],
    ["Обновлён", formatDateTime(c.last_fetched_at)],
  ];
  return (
    <Card>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
        {rows.map(([k, v]) => (
          <div key={k}>
            <dt className="text-xs text-zinc-500">{k}</dt>
            <dd className="tabular-nums">{v}</dd>
          </div>
        ))}
      </dl>
      {c.projects.length > 0 ? (
        <div className="mt-3 flex flex-wrap items-center gap-1.5 border-t border-zinc-100 pt-3 text-sm dark:border-zinc-800">
          <span className="text-xs text-zinc-500">Проекты:</span>
          {c.projects.map((p) => (
            <Link key={p.id} href={`/projects/${p.id}`} className="rounded bg-zinc-100 px-1.5 py-0.5 text-xs hover:bg-zinc-200 dark:bg-zinc-800 dark:hover:bg-zinc-700">
              {p.name}
            </Link>
          ))}
        </div>
      ) : null}
    </Card>
  );
}

const METHOD_LABELS: Record<string, string> = {
  keyword_search: "поиск видео",
  channel_search: "поиск каналов",
  related_video: "связанные видео",
  manual_import: "ручной импорт",
  monitoring: "мониторинг",
  demo: "демо",
};

function percent(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return `${n > 0 ? "+" : ""}${Math.round(n * 100)} %`;
}

/** §3/§4 performance & activity metrics computed from the newest stored videos. */
function Performance({ channel: c }: { channel: ChannelDetail }) {
  const m = c.metrics;
  if (!m) {
    return (
      <Card className="mt-4">
        <p className="text-sm text-zinc-500">Метрики появятся после сбора видео канала поиском.</p>
      </Card>
    );
  }
  const rows: [string, string, string?][] = [
    ["Средние просмотры", formatCount(m.avg_views)],
    ["Медиана просмотров", formatCount(m.median_views)],
    ["Последнее видео", formatCount(m.last_video_views)],
    ["Среднее последних 10", formatCount(m.avg_views_recent)],
    ["Просмотры / подписчики", formatRatio(m.views_to_subs_ratio), "среднее; медиана " + formatRatio(m.median_views_to_subs_ratio)],
    ["Динамика просмотров", percent(m.views_trend), "новая половина окна к старой"],
    ["Скорость (30 дн.)", m.recent_views_velocity === null ? "—" : `${formatCount(Math.round(m.recent_views_velocity))}/день`],
    ["Видео 7 / 30 / 90 дн.", `${m.videos_7d} / ${m.videos_30d} / ${m.videos_90d}`],
    ["Интервал публикаций", m.avg_days_between_uploads === null ? "—" : `${formatRatio(m.avg_days_between_uploads)} дн.`],
    ["Регулярность", m.upload_consistency === null ? "—" : formatRatio(m.upload_consistency), "1 — строго по графику"],
    ["Дата последнего видео", formatDate(m.last_video_at)],
    ["Окно расчёта", `${m.window_videos} видео с ${formatDate(m.oldest_window_video_at)}`],
  ];
  return (
    <Card className="mt-4">
      <h2 className="mb-2 text-sm font-semibold">
        Performance <span className="font-normal text-zinc-500">· рассчитано {formatDateTime(m.computed_at)}</span>
      </h2>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
        {rows.map(([k, v, hint]) => (
          <div key={k} title={hint}>
            <dt className="text-xs text-zinc-500">{k}</dt>
            <dd className="tabular-nums">{v}</dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}

/** Where, when and how the channel was found (§2, §50). */
function Discoveries({ channel: c }: { channel: ChannelDetail }) {
  if (c.discoveries.length === 0) return null;
  return (
    <Card className="mt-4">
      <h2 className="mb-2 text-sm font-semibold">
        Источники обнаружения <span className="font-normal text-zinc-500">({c.discoveries_total})</span>
      </h2>
      {c.niches.length > 0 ? (
        <div className="mb-2 flex flex-wrap items-center gap-1">
          <span className="text-xs text-zinc-500">Ниши:</span>
          {c.niches.map((n) => (
            <Badge key={n.id} tone="blue">
              {n.name}
            </Badge>
          ))}
        </div>
      ) : null}
      <table className="w-full text-sm">
        <thead className="text-left text-xs uppercase tracking-wide text-zinc-500">
          <tr>
            <th className="py-1 font-medium">Когда</th>
            <th className="py-1 font-medium">Проект</th>
            <th className="py-1 font-medium">Запрос</th>
            <th className="py-1 font-medium">Ниша</th>
            <th className="py-1 font-medium">Способ</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
          {c.discoveries.map((d, i) => (
            <tr key={`${d.project.id}-${d.query_id}-${d.method}-${i}`}>
              <td className="py-1 pr-2 tabular-nums text-zinc-500">{formatDateTime(d.discovered_at)}</td>
              <td className="py-1 pr-2">
                <Link href={`/projects/${d.project.id}`} className="hover:underline">
                  {d.project.name}
                </Link>
              </td>
              <td className="py-1 pr-2">{d.query_text ?? "—"}</td>
              <td className="py-1 pr-2">{d.niche?.name ?? "—"}</td>
              <td className="py-1">{METHOD_LABELS[d.method] ?? d.method}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function Thumbnails({ channel }: { channel: ChannelDetail }) {
  const [offset, setOffset] = useState(0);
  const videos = useChannelVideos(channel.id, offset);
  const download = useDownloadChannelThumbnails(channel.id);
  const missing = videos.data?.items.some((v) => v.thumbnail && v.thumbnail.fetch_status !== "ok") ?? false;

  return (
    <Card className="mt-4 p-0">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-zinc-200 px-4 py-3 dark:border-zinc-800">
        <h2 className="text-sm font-semibold">
          Превью видео {videos.data ? <span className="font-normal text-zinc-500">({videos.data.total})</span> : null}
        </h2>
        {missing ? (
          <div className="flex items-center gap-2">
            {download.data ? (
              <span role="status" className="text-xs text-zinc-500">
                {download.data.job ? (
                  <>
                    {download.data.created ? "Задача поставлена" : "Задача уже идёт"}: {download.data.items} превью ·{" "}
                    <Link href="/jobs" className="underline">
                      Задачи
                    </Link>
                  </>
                ) : (
                  "Все превью уже загружены"
                )}
              </span>
            ) : null}
            <Button size="sm" variant="secondary" disabled={download.isPending} onClick={() => download.mutate()}>
              <Download className="size-3.5" aria-hidden /> Скачать превью
            </Button>
          </div>
        ) : null}
      </div>
      <div className="p-4">
        <InlineError error={download.error} />
        {videos.isPending ? (
          <LoadingState />
        ) : videos.isError ? (
          <ErrorState error={videos.error} onRetry={() => videos.refetch()} />
        ) : videos.data.items.length === 0 ? (
          <EmptyState title="Видео нет" hint="Видео появятся после сбора данных канала." />
        ) : (
          <>
            <ul className={cn("grid grid-cols-[repeat(auto-fill,minmax(14rem,1fr))] gap-4", videos.isPlaceholderData && "opacity-60")}>
              {videos.data.items.map((v) => (
                <VideoCard key={v.id} video={v} />
              ))}
            </ul>
            <Pager offset={offset} limit={VIDEOS_PAGE} total={videos.data.total} onChange={setOffset} />
          </>
        )}
      </div>
    </Card>
  );
}

function VideoCard({ video: v }: { video: Video }) {
  const t = v.thumbnail;
  return (
    <li className="min-w-0">
      <div className="relative aspect-video overflow-hidden rounded-md bg-zinc-100 dark:bg-zinc-800">
        {t?.image_url ? (
          // Served by our API with a session cookie and access check; the next/image optimizer would
          // fetch it server-side without that cookie.
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={t.image_url}
            alt={`Превью: ${v.title}`}
            width={t.width ?? undefined}
            height={t.height ?? undefined}
            loading="lazy"
            className="size-full object-cover"
          />
        ) : (
          <div className="flex size-full flex-col items-center justify-center gap-1 text-xs text-zinc-500">
            <ImageOff className="size-5" aria-hidden />
            {t?.fetch_status === "failed" ? "Не удалось загрузить" : t ? "Не загружено" : "Нет превью"}
          </div>
        )}
        {v.duration_seconds ? (
          <span className="absolute bottom-1 right-1 rounded bg-black/75 px-1 text-[11px] font-medium tabular-nums text-white">
            {formatDuration(v.duration_seconds)}
          </span>
        ) : null}
      </div>
      <p className="mt-1.5 line-clamp-2 text-sm font-medium" title={v.title}>
        {v.title}
      </p>
      <p className="mt-0.5 flex items-center gap-1.5 text-xs text-zinc-500">
        <span>{formatCount(v.view_count)} просм.</span>·<span>{formatDate(v.published_at)}</span>
        {t?.fetch_status === "failed" ? <Badge tone="red">ошибка</Badge> : null}
      </p>
    </li>
  );
}
