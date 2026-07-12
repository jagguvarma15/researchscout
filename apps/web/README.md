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

## Auth (BFF)

Login is an OIDC code + PKCE flow against Keycloak, terminated server-side: tokens live in a
Redis-backed session keyed by the opaque `rs_session` cookie, and `/api/*` proxies to the API
swapping the cookie for a Bearer token. Blog pages stay public; the API decides what needs auth.

Env vars (defaults suit `docker compose --profile core up` with the app on the host):
`KEYCLOAK_PUBLIC_URL` (browser-facing, default `http://localhost:8080`),
`KEYCLOAK_INTERNAL_URL` (backchannel, defaults to public), `KEYCLOAK_REALM` (`researchscout`),
`OIDC_CLIENT_ID` (`web-bff`), `OIDC_CLIENT_SECRET`, `REDIS_URL` (session store — on
Vercel this is exactly what the Upstash integration injects), `SITE_URL`.
The dev realm ships a `demo` / `demo` user (see `config/keycloak/realm.json`).
