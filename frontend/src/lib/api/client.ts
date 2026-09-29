import createClient, { type Middleware } from "openapi-fetch";

import type { components, paths } from "./schema";

export type Schemas = components["schemas"];

/** Must match backend settings (``csrf_cookie_name`` / ``csrf_header_name``). */
export const CSRF_COOKIE = "ytl_csrf";
export const CSRF_HEADER = "X-CSRF-Token";
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

/** Uniform backend error body: ``{"error": {"code", "message", "details"}}`` (app/api/errors.py). */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: unknown = {},
  ) {
    super(message);
    this.name = "ApiError";
  }

  get isUnauthorized(): boolean {
    return this.status === 401;
  }
}

export function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  for (const part of document.cookie.split(";")) {
    const [k, ...v] = part.trim().split("=");
    if (k === name) return decodeURIComponent(v.join("="));
  }
  return null;
}

const csrfMiddleware: Middleware = {
  onRequest({ request }) {
    if (!SAFE_METHODS.has(request.method)) {
      const token = readCookie(CSRF_COOKIE);
      if (token) request.headers.set(CSRF_HEADER, token);
    }
    return request;
  },
};

export function toApiError(status: number, body: unknown): ApiError {
  const err = (body as { error?: { code?: unknown; message?: unknown; details?: unknown } } | null)?.error;
  if (err && typeof err.code === "string" && typeof err.message === "string") {
    return new ApiError(status, err.code, err.message, err.details);
  }
  if (status >= 500 || status === 0) {
    return new ApiError(status, "server_unavailable", "Сервер недоступен или вернул ошибку. Попробуйте ещё раз.");
  }
  return new ApiError(status, `http_${status}`, `Запрос завершился с кодом ${status}.`);
}

function baseUrl(): string {
  // All calls are made from the browser through the Next rewrite (same origin).
  return typeof window === "undefined" ? "http://localhost" : window.location.origin;
}

export const api = createClient<paths>({
  baseUrl: baseUrl(),
  credentials: "same-origin",
  // Resolve fetch per call (openapi-fetch would otherwise capture it once at import time).
  fetch: (request) => globalThis.fetch(request),
});
api.use(csrfMiddleware);

type Result<T> = { data?: T; error?: unknown; response: Response };

/** Await an openapi-fetch call and return its data, throwing ``ApiError`` for non-2xx responses. */
export async function unwrap<T>(call: Promise<Result<T>>): Promise<T> {
  let res: Result<T>;
  try {
    res = await call;
  } catch {
    throw new ApiError(0, "network_error", "Нет связи с сервером. Проверьте, что API запущен.");
  }
  if (!res.response.ok) throw toApiError(res.response.status, res.error);
  return res.data as T;
}
