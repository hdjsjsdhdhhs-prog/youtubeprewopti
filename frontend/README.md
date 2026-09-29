# Frontend (Next.js 16)

Operator UI for YT Lead Intelligence — ADR-0005. All data comes from the FastAPI backend through the
`/api/*` rewrite in `next.config.ts` (same-origin cookie session + CSRF double-submit).

## Commands

| Command | What it does |
|---|---|
| `npm run dev` | Dev server on :3000 (API expected at `YTL_API_ORIGIN`, default `http://127.0.0.1:8000`). Usually started by `scripts\dev.ps1`. |
| `npm run build` / `npm start` | Production build / server. `YTL_API_ORIGIN` is baked in **at build time**. |
| `npm run gen:api` | Regenerate `src/lib/api/schema.d.ts` from the running API's OpenAPI schema. Run after backend schema changes. |
| `npm run typecheck` | `next typegen` + `tsc --noEmit`. |
| `npm run lint` | ESLint (flat config from `eslint-config-next`). |
| `npm test` | Vitest (jsdom). Runs through `scripts/vitest.mjs` to avoid a Windows drive-letter casing issue. |

## Layout

- `src/app/login` — login page (public).
- `src/app/(app)` — authenticated area: `layout.tsx` resolves `/api/auth/me`; any 401 redirects to
  `/login?next=…` (see `providers.tsx`).
  - `projects`, `projects/[id]` — projects, query bulk import.
  - `channels` — server-filtered/sorted list; offset pages of 200, rows virtualized (TanStack Table v9 + Virtual).
  - `jobs` — background jobs; polls every 2 s while any job is queued/running/retrying.
- `src/lib/api` — typed `openapi-fetch` client (`client.ts`), query keys + hooks (`hooks.ts`).
- Filters live in URL search params (`src/lib/url-state.ts`).
- Shortcuts: `g p` / `g c` / `g j` — sections, `/` — channel search.
