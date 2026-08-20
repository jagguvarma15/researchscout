# ResearchScout web

The public site: a server-rendered Astro app over the ResearchScout API — paper feed with
semantic search on `/`, paper detail at `/papers/{id}`.

```bash
pnpm install
pnpm dev          # http://localhost:4321, expects the API on http://localhost:8000
pnpm build        # SSR build via @astrojs/node (standalone)
node dist/server/entry.mjs
```

Env vars: `API_URL` (default `http://localhost:8000`) points at the API - in production the
Railway service's public URL; `API_SERVICE_TOKEN` rides every proxied request as
`x-rs-service-token` and must match the backend's `RS_SERVICE_TOKEN`; `SITE_URL` (default
`http://localhost:4321`) anchors the same-origin check on mutating `/api/*` requests; the
`AUTH0_*` variables and `SESSION_SECRET` drive sign-in (see `deploy/PUBLISHING.md`). Pages
degrade gracefully when the API is unreachable instead of failing the render.

Without an Auth0 configuration the app acts as the built-in local user, matching the API's
local no-auth mode.

`SITE_PRIVATE=true` plus an `ENTRY_CODE` (and a working Auth0 configuration) turn on the
members-only gate: anonymous visitors see only `/welcome`, `/terms`, and `/privacy`, the
`/api` proxy answers 401 without an approved session, and full access requires signing in
plus redeeming the entry code once - entered before or after sign-in, remembered in the
session cookie. Unset (the default, and always locally) the site is public exactly as
before.

## Filters, math, and the reader

The feed is URL-driven: the filter sidebar's Extract button serializes its controls into query
params (`kind`, `group`, `category`, `days` or `year`/`month`, `author`, `venue`,
`min_citations`, `sort`) and navigates, so every filtered view is a shareable URL and applied
filters render as removable chips. `src/lib/taxonomy.ts` is a display-only mirror of the Python
taxonomy — the server is authoritative for filtering, so drift shows as an empty result, never
wrong data.

Titles and abstracts render inline LaTeX server-side (`src/lib/math.ts`, KaTeX): the raw string
is segmented first, text segments are HTML-escaped exactly like the digest body, math segments
render, and broken TeX falls back to escaped source. `src/lib/math-text.ts` has the client-safe
`stripMath` used by the command palette and document titles — no KaTeX ships to the browser.

`?read=1` on a paper page deep-links into the in-app PDF reader (pdf.js, loaded only when the
overlay opens; arXiv PDFs are fetched directly thanks to their wildcard CORS header — the
`/api/[...path]` proxy shape is the fallback if that ever changes).
