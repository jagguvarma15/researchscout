# Publishing: Funnel, Auth0, Vercel, Grafana Cloud

The compose stack in this directory runs the backend. This is everything outside it - the three
accounts that make the site public, in the order they need to happen. Each step ends with
something you can check, because a half-configured deployment fails in ways that look like code
bugs.

Nothing here can be scripted from the repository: every step needs credentials only you can
create.

## 1. Publishing the API with Tailscale Funnel

The backend runs at home. Funnel gives it a public HTTPS hostname without a domain, without an
inbound firewall rule, and without exposing this machine's address: the daemon holds an
outbound connection and Tailscale relays to it.

1. Install Tailscale on this Mac and sign in (`brew install --cask tailscale`).
2. Enable HTTPS and Funnel for the tailnet: admin console -> DNS -> enable MagicDNS and HTTPS
   certificates, then Access controls -> add `funnel` to the node attributes for this machine.
   The admin console prompts for both the first time you run the command below.
3. Publish the API, which compose binds to `127.0.0.1:8001`:

   ```bash
   tailscale funnel --bg 8001
   tailscale funnel status        # prints the public https://<machine>.<tailnet>.ts.net
   ```

Check: `curl https://<machine>.<tailnet>.ts.net/healthz` from a phone off wifi returns
`{"status":"ok"}`.

Funnel listens only on 443, 8443 and 10000, only over TLS, and its bandwidth is capped at a
level Tailscale does not publish. For a personal research radar that is fine; it is not a CDN.

### Close the front door

That hostname is public, so anyone who learns it can call the API - including the routes open
to signed-out visitors. One shared secret separates the site's own server from everyone else:

```bash
openssl rand -base64 36        # put it in deploy/.env as RS_SERVICE_TOKEN
                               # and in Vercel as API_SERVICE_TOKEN
```

The frontend proxies every browser request server-side, so the token never reaches a browser.
Requests without it get a 404 rather than a 403 - there is no reason to confirm to a scanner
that an API lives there. `/healthz` stays open so the container healthcheck works.

That token is also what makes rate limiting mean anything. Every request arrives from the same
place (the site's server), so the socket address is useless for telling visitors apart; the
proxy forwards each visitor's address, and the API believes it only because the token proved
where the request came from.

With no edge in front of the API any more, that in-process limiter is the only limiter. It is
per process and resets on restart - adequate for this scale, and worth remembering before
posting the link somewhere busy.

## 2. Auth0

1. Create a tenant. Applications -> Create -> Regular Web Application.
   - Allowed Callback URLs: `https://<site>/callback`
   - Allowed Logout URLs: `https://<site>`
   - Note the domain, client id and client secret.
2. APIs -> Create API. Identifier (the audience) is what the backend validates - it is a
   name, not a URL that has to resolve, so something stable like `https://researchscout.api`
   is fine. Set `RS_OIDC_AUDIENCE` to exactly that string, and
   `RS_OIDC_ISSUER` to `https://<tenant>/` (with the trailing slash).
3. Applications -> Create -> Machine to Machine, authorized for the Auth0 Management API with
   the `delete:users` scope. Its credentials go in `RS_AUTH0_DOMAIN`,
   `RS_AUTH0_MGMT_CLIENT_ID` and `RS_AUTH0_MGMT_CLIENT_SECRET`. Without these, account
   deletion refuses rather than deleting rows and leaving the login behind.
4. Restart the backend so it picks up the issuer: `make deploy-down && make deploy-up`.

Check: with the service token header, `/v1/me` returns 401 without a bearer token, and
`/v1/papers` still returns papers.

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
| `API_URL` | `https://<machine>.<tailnet>.ts.net` |
| `SITE_URL` | `https://<site>` |
| `AUTH0_DOMAIN` | `<tenant>.us.auth0.com` |
| `AUTH0_CLIENT_ID` | the regular web application's id |
| `AUTH0_CLIENT_SECRET` | its secret |
| `AUTH0_AUDIENCE` | the API identifier from step 2 |
| `SESSION_SECRET` | `openssl rand -base64 48` |
| `API_SERVICE_TOKEN` | the same value as `RS_SERVICE_TOKEN` in `deploy/.env` |

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
