# Publishing: the tunnel, Vercel, Auth0, Grafana Cloud

The compose stack in this directory runs the backend. This is everything outside it - the four
accounts that make the site public, in the order they need to happen. Each step ends with
something you can check, because a half-configured deployment fails in ways that look like code
bugs.

Nothing here can be scripted from the repository: every step needs credentials only you can
create.

## 1. The domain and the tunnel

The backend runs at home. Cloudflare Tunnel publishes it without opening a port: `cloudflared`
makes an outbound connection, Cloudflare terminates TLS, and the origin stays unreachable
otherwise.

1. Put the domain on Cloudflare's nameservers (Cloudflare dashboard, Add a site).
2. Zero Trust -> Networks -> Tunnels -> Create a tunnel (Cloudflared). Copy the token into
   `CLOUDFLARE_TUNNEL_TOKEN` in `deploy/.env`. It is shown once.
3. Add a public hostname on the tunnel: `api.<domain>` -> `HTTP` -> `api:8000`. The service
   name resolves inside the compose network, which is why the API itself is only bound to
   loopback on the host.
4. Start it: `docker compose -f deploy/docker-compose.yml --profile tunnel up -d`.

Check: `curl https://api.<domain>/healthz` returns `{"status":"ok"}` from another network.

### Lock it to the frontend

Anyone who learns the hostname can otherwise call the API directly. Zero Trust -> Access ->
Applications -> Add a self-hosted application for `api.<domain>`, with one policy:
`Service Auth` -> `Service Token is <your token>`. Create the token under Access -> Service
Auth, and put its two halves into Vercel as `CF_ACCESS_CLIENT_ID` and
`CF_ACCESS_CLIENT_SECRET`; the proxy already sends them on every request.

Check: `curl https://api.<domain>/v1/papers` now returns 403, and the site still works.

Also add one WAF rate-limiting rule (Security -> WAF -> Rate limiting rules) on
`api.<domain>`: something like 300 requests per minute per IP. The application limiter is
per-process and cannot see a flood before it arrives.

## 2. Auth0

1. Create a tenant. Applications -> Create -> Regular Web Application.
   - Allowed Callback URLs: `https://<site>/callback`
   - Allowed Logout URLs: `https://<site>`
   - Note the domain, client id and client secret.
2. APIs -> Create API. Identifier (the audience) is what the backend validates - use something
   stable like `https://api.<domain>`. Set `RS_OIDC_AUDIENCE` to exactly that string, and
   `RS_OIDC_ISSUER` to `https://<tenant>/` (with the trailing slash).
3. Applications -> Create -> Machine to Machine, authorized for the Auth0 Management API with
   the `delete:users` scope. Its credentials go in `RS_AUTH0_DOMAIN`,
   `RS_AUTH0_MGMT_CLIENT_ID` and `RS_AUTH0_MGMT_CLIENT_SECRET`. Without these, account
   deletion refuses rather than deleting rows and leaving the login behind.
4. Restart the backend so it picks up the issuer: `make deploy-down && make deploy-up`.

Check: `curl https://api.<domain>/v1/me` returns 401, and `/v1/papers` still returns papers.

## 3. Vercel

The frontend is `apps/web`. It needs the Vercel adapter instead of the Node one - a one-line
change kept out of the repository until you are ready, because the Node adapter is what the
local stack runs on:

```js
// apps/web/astro.config.mjs
import vercel from '@astrojs/vercel';
// adapter: node({ mode: 'standalone' })  ->
adapter: vercel(),
```

```bash
cd apps/web && pnpm add @astrojs/vercel
```

Project settings: root directory `apps/web`, framework Astro. Environment variables:

| Variable | Value |
| --- | --- |
| `API_URL` | `https://api.<domain>` |
| `SITE_URL` | `https://<site>` |
| `AUTH0_DOMAIN` | `<tenant>.us.auth0.com` |
| `AUTH0_CLIENT_ID` | the regular web application's id |
| `AUTH0_CLIENT_SECRET` | its secret |
| `AUTH0_AUDIENCE` | the API identifier from step 2 |
| `SESSION_SECRET` | `openssl rand -base64 48` |
| `CF_ACCESS_CLIENT_ID` | the service token id |
| `CF_ACCESS_CLIENT_SECRET` | the service token secret |

Keep the Hobby plan's terms in view: non-commercial personal use only, and that includes
donation links and advertising.

Check: sign in on the deployed site, accept the terms once, save a paper, sign out and back in.
The terms dialog must not appear the second time.

## 4. Grafana Cloud

The dashboards in `config/grafana/dashboards/` are SQL against Postgres, so Grafana Cloud needs
a route to a database that is not exposed. Private Data Source Connect is that route: an agent
in the compose stack opens an outbound tunnel, and no inbound rule is needed.

1. Create a free Grafana Cloud stack.
2. Connections -> Private data source connect -> create for your region. Copy the token,
   cluster and stack id into `GRAFANA_PDC_TOKEN`, `GRAFANA_PDC_CLUSTER` and `GRAFANA_STACK_ID`.
3. `docker compose -f deploy/docker-compose.yml --profile monitoring up -d`
4. Add a PostgreSQL data source in Grafana Cloud with host `postgres:5432`, database
   `researchscout`, and Private data source connect set to your PDC network.
5. Give it its own read-only login rather than the application's:

   ```sql
   CREATE ROLE grafana LOGIN PASSWORD '...';
   GRANT CONNECT ON DATABASE researchscout TO grafana;
   GRANT USAGE ON SCHEMA public TO grafana;
   GRANT SELECT ON ALL TABLES IN SCHEMA public TO grafana;
   ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO grafana;
   ```

6. Import the four dashboards from `config/grafana/dashboards/`, pointing them at the new data
   source. The local Grafana stays as it is for development.

Check: the Pipeline dashboard renders with data.

## Afterwards

Rewrite `/about`. It currently says the app "runs entirely on this machine, and it publishes
nothing", which is true until the moment any of the above is done, and false immediately after.
