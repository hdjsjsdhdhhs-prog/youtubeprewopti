/** The landing page after login: the dashboard (Phase 9). */
export const HOME = "/";

/** Only same-app relative paths are accepted as a post-login target (no open redirect). */
export function safeNext(next: string | null | undefined): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) return HOME;
  if (next.startsWith("/login")) return HOME;
  return next;
}

export function loginHref(next: string): string {
  const target = safeNext(next);
  return target === HOME ? "/login" : `/login?next=${encodeURIComponent(target)}`;
}
