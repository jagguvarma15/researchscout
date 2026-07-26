# ResearchScout web

The public site: a server-rendered Astro app over the ResearchScout API — paper feed with
semantic search on `/`, paper detail at `/papers/{id}`.

```bash
pnpm install
pnpm dev          # http://localhost:4321, expects the API on http://localhost:8000
pnpm build        # SSR build via @astrojs/node (standalone)
node dist/server/entry.mjs
```

Env vars: `API_URL` (default `http://localhost:8000`) points at the API; `SITE_URL` (default
`http://localhost:4321`) anchors the same-origin check on mutating `/api/*` requests. Pages
degrade gracefully when the API is unreachable instead of failing the render.

There is no sign-in: the app always acts as the built-in local user, matching the API's
local no-auth mode, and `/api/*` proxies to the API without credentials.
