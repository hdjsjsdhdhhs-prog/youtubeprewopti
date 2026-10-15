"use client";

import Link from "next/link";
import { useState } from "react";

import { FavoriteStar } from "@/components/lead-workflow";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { DemoBadge } from "@/components/status-badges";
import { Button, PageHeader, Pager } from "@/components/ui";
import type { Favorite, FavoriteEntity } from "@/lib/api/hooks";
import { FAVORITES_PAGE, useFavorites } from "@/lib/api/workflow-hooks";
import { formatCount, formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

const KINDS: { value: FavoriteEntity | undefined; label: string }[] = [
  { value: undefined, label: "Всё" },
  { value: "channel", label: "Каналы" },
  { value: "thumbnail", label: "Превью" },
];

/** ТЗ §37: personal favorite channels and thumbnails (generated concepts arrive with Phase 5). */
export function FavoritesView() {
  const [kind, setKind] = useState<FavoriteEntity | undefined>(undefined);
  const [offset, setOffset] = useState(0);
  const list = useFavorites({ entity_type: kind, offset });

  return (
    <div>
      <PageHeader
        title="Избранное"
        subtitle="Личное: видно только вам. Звёздочка — в таблице лидов, на карточке канала и в результатах аудита превью"
      />
      <div className="mb-3 flex gap-1.5">
        {KINDS.map((k) => (
          <Button
            key={k.label}
            size="sm"
            variant={kind === k.value ? "primary" : "secondary"}
            onClick={() => {
              setKind(k.value);
              setOffset(0);
            }}
          >
            {k.label}
          </Button>
        ))}
        <Link href="/leads?favorite=true" className="ml-2 self-center text-xs text-zinc-500 hover:underline">
          избранные каналы как лиды →
        </Link>
      </div>
      {list.isPending ? (
        <LoadingState />
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => void list.refetch()} />
      ) : list.data.items.length === 0 ? (
        <EmptyState title="В избранном пока ничего нет" hint="Отмечайте каналы и превью звёздочкой." />
      ) : (
        <>
          <ul className={cn("grid gap-3 sm:grid-cols-2 xl:grid-cols-3", list.isPlaceholderData && "opacity-60")}>
            {list.data.items.map((f) => (
              <FavoriteCard key={`${f.entity_type}-${f.entity_id}`} f={f} />
            ))}
          </ul>
          <Pager offset={offset} limit={FAVORITES_PAGE} total={list.data.total} onChange={setOffset} />
        </>
      )}
    </div>
  );
}

function FavoriteCard({ f }: { f: Favorite }) {
  if (f.channel) {
    const c = f.channel;
    return (
      <li className="flex items-center gap-3 rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900">
        {c.avatar_url ? (
          // External YouTube avatar.
          // eslint-disable-next-line @next/next/no-img-element
          <img src={c.avatar_url} alt="" referrerPolicy="no-referrer" loading="lazy" className="size-10 rounded-full bg-zinc-200" />
        ) : (
          <div className="size-10 rounded-full bg-zinc-200 dark:bg-zinc-700" aria-hidden />
        )}
        <div className="min-w-0 flex-1">
          <Link href={`/channels/${c.id}`} className="block truncate font-medium hover:underline" title={c.title}>
            {c.title}
          </Link>
          <p className="text-xs text-zinc-500">
            канал · {formatCount(c.subscriber_count)} подп. · добавлен {formatDate(f.created_at)} <DemoBadge show={c.is_demo} />
          </p>
        </div>
        <FavoriteStar type="channel" id={c.id} on label={c.title} />
      </li>
    );
  }
  if (f.video) {
    const v = f.video;
    return (
      <li className="overflow-hidden rounded-lg border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
        <div className="aspect-video bg-zinc-100 dark:bg-zinc-800">
          {v.image_url ? (
            // Served by our API with the session cookie and an access check.
            // eslint-disable-next-line @next/next/no-img-element
            <img src={v.image_url} alt={`Превью: ${v.title}`} loading="lazy" className="size-full object-cover" />
          ) : null}
        </div>
        <div className="flex items-start gap-2 p-2">
          <div className="min-w-0 flex-1">
            <p className="line-clamp-2 text-sm font-medium" title={v.title}>
              {v.title}
            </p>
            <Link href={`/channels/${v.channel_id}`} className="text-xs text-zinc-500 hover:underline">
              {v.channel_title}
            </Link>
          </div>
          <FavoriteStar type="thumbnail" id={v.id} on label={v.title} />
        </div>
      </li>
    );
  }
  return null;
}
