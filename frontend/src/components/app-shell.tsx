"use client";

import { FolderKanban, ListChecks, LogOut, Network, Tv } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import type { Me } from "@/lib/api/hooks";
import { useLogout } from "@/lib/api/hooks";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/projects", label: "Проекты", icon: FolderKanban, key: "p" },
  { href: "/channels", label: "Каналы", icon: Tv, key: "c" },
  { href: "/niches", label: "Ниши", icon: Network, key: "n" },
  { href: "/jobs", label: "Задачи", icon: ListChecks, key: "j" },
] as const;

function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  return !!el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));
}

/** "g" then a letter jumps to a section (§62); ignored while typing in a field. */
function useGoToShortcuts() {
  const router = useRouter();
  useEffect(() => {
    let armedUntil = 0;
    function onKey(e: KeyboardEvent) {
      if (e.ctrlKey || e.metaKey || e.altKey || isTyping(e.target)) return;
      if (e.key === "g") {
        armedUntil = Date.now() + 1000;
        return;
      }
      if (Date.now() < armedUntil) {
        const item = NAV.find((n) => n.key === e.key);
        armedUntil = 0;
        if (item) {
          e.preventDefault();
          router.push(item.href);
        }
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [router]);
}

export function AppShell({ me, children }: { me: Me; children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const logout = useLogout();
  useGoToShortcuts();

  return (
    <div className="flex h-full">
      <aside className="flex w-56 shrink-0 flex-col border-r border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
        <div className="px-4 py-4">
          <p className="text-sm font-semibold tracking-tight">YT Lead Intelligence</p>
          <p className="truncate text-xs text-zinc-500" title={me.workspace.name}>
            {me.workspace.name}
          </p>
        </div>
        <nav aria-label="Основная навигация" className="flex-1 space-y-0.5 px-2">
          {NAV.map(({ href, label, icon: Icon, key }) => {
            const active = pathname === href || pathname.startsWith(`${href}/`);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex items-center gap-2 rounded-md px-2.5 py-1.5 text-sm",
                  active
                    ? "bg-zinc-100 font-medium text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100"
                    : "text-zinc-600 hover:bg-zinc-50 dark:text-zinc-400 dark:hover:bg-zinc-800/60",
                )}
              >
                <Icon className="size-4" aria-hidden />
                <span className="flex-1">{label}</span>
                <kbd className="hidden font-mono text-[10px] text-zinc-400 lg:inline">g {key}</kbd>
              </Link>
            );
          })}
        </nav>
        <div className="border-t border-zinc-200 p-3 dark:border-zinc-800">
          <p className="truncate text-sm" title={me.user.email}>
            {me.user.display_name}
          </p>
          <p className="mb-2 truncate text-xs text-zinc-500">
            {me.user.email} · {me.role}
          </p>
          <button
            type="button"
            disabled={logout.isPending}
            onClick={() => logout.mutate(undefined, { onSettled: () => router.replace("/login") })}
            className="flex items-center gap-1.5 text-xs text-zinc-500 hover:text-zinc-900 disabled:opacity-50 dark:hover:text-zinc-100"
          >
            <LogOut className="size-3.5" aria-hidden />
            Выйти
          </button>
        </div>
      </aside>
      <main className="min-w-0 flex-1 overflow-auto p-6">{children}</main>
    </div>
  );
}
