# ResearchScout web

The public site: a server-rendered Astro app over the ResearchScout API — paper feed with
semantic search on `/`, paper detail at `/papers/{id}`.

```bash
pnpm install
pnpm dev          # http://localhost:4321, expects the API on http://localhost:8000
pnpm build        # SSR build via @astrojs/node (standalone)
node dist/server/entry.mjs
```

Set `API_URL` to point at the API from elsewhere (in compose it is `http://api:8000`). Pages
degrade gracefully when the API is unreachable instead of failing the render.
