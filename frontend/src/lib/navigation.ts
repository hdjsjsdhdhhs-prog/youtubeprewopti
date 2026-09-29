/** Only same-app relative paths are accepted as a post-login target (no open redirect). */
export function safeNext(next: string | null | undefined): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) return "/projects";
  if (next.startsWith("/login")) return "/projects";
  return next;
}

export function loginHref(next: string): string {
  const target = safeNext(next);
  return target === "/projects" ? "/login" : `/login?next=${encodeURIComponent(target)}`;
}
