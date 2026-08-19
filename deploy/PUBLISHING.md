# Publishing: Railway, Auth0, Vercel

The backend runs on Railway; the frontend on Vercel; sign-in through Auth0. This is the
account-level setup in the order it needs to happen. Each step ends with something you can
check, because a half-configured deployment fails in ways that look like code bugs.

Nothing here can be scripted from the repository: every step needs credentials only you can
create.

## 1. The backend on Railway

One project, two services, one region (pick the same US region for both; Vercel's functions
default to us-east, so us-east keeps the proxy hop short).

### Postgres

New service from the Docker image `pgvector/pgvector:pg17` (the plain Railway Postgres
template ships without the vector extension this schema needs):

- Volume mounted at `/var/lib/postgresql/data` - the pg17 data path; without a volume the
  database is erased on every redeploy.
- Service variables: `POSTGRES_USER=researchscout`, `POSTGRES_DB=researchscout`,
  `POSTGRES_PASSWORD` (generate with `openssl rand -hex 24` - hex, not base64: the value is
  substituted into connection URLs, where a `/` or `@` breaks parsing rather than failing
  cleanly), and `PGDATA=/var/lib/postgresql/data/pgdata`. The last one is not optional: the
  volume mounts with a `lost+found` directory at its root, and initdb refuses a non-empty
  data directory - pointing PGDATA one level down is the standard fix.
- Restores that build HNSW indexes need parallelism off (`SET
  max_parallel_maintenance_workers = 0`): parallel index builds allocate dynamic shared
  memory, and the container's 64MB `/dev/shm` is too small for these tables.
- Enable the TCP proxy (Settings -> Networking) and note the public connection string -
  restores and `make backup` go through it. It is password-authed; leave it on.

### The api service

New service from this GitHub repository, `main` branch, auto-deploy on push. `railway.json`
at the repo root carries the config as code: the Dockerfile build
(`docker/api.Dockerfile`), the pre-deploy migration command (`scout db upgrade`), and the
`/healthz` healthcheck. The container runs `scout serve all` - API and scheduler in one
process.

Variables to set on the service: the full list with explanations is in
`deploy/.env.example`. The one that is not a plain value:

```
RS_DATABASE_URL=postgresql+psycopg://${{postgres.POSTGRES_USER}}:${{postgres.POSTGRES_PASSWORD}}@${{postgres.RAILWAY_PRIVATE_DOMAIN}}:5432/${{postgres.POSTGRES_DB}}
```

It exists because Railway's own `DATABASE_URL` lacks the `+psycopg` driver marker
SQLAlchemy needs. The build identity needs nothing: the app reads Railway's per-deploy
`RAILWAY_GIT_COMMIT_SHA` stamp directly (a `${{...}}` reference cannot reach it), which is
what lets `make deploy-verify` prove the deployed commit is origin/main. Then Settings ->
Networking -> Generate Domain; that public https URL goes into Vercel's `API_URL` and
`deploy/.env`'s `RAILWAY_API_URL`.

Check: `curl https://<the-domain>/healthz` returns `{"status":"ok"}`.

### Close the front door

That hostname is public, so anyone who learns it can call the API - including the routes
open to signed-out visitors. One shared secret separates the site's own server from
everyone else:

```bash
openssl rand -base64 36        # put it in the Railway service as RS_SERVICE_TOKEN,
                               # in Vercel as API_SERVICE_TOKEN,
                               # and in deploy/.env for verify.sh
```

The frontend proxies every browser request server-side, so the token never reaches a
browser. Requests without it get a 404 rather than a 403 - there is no reason to confirm to
a scanner that an API lives there. `/healthz` stays open so Railway's healthcheck works.

That token is also what makes rate limiting mean anything. Every request arrives from the
same place (the site's server), so the socket address is useless for telling visitors
apart; the proxy forwards each visitor's address, and the API believes it only because the
token proved where the request came from.

With no edge in front of the API, that in-process limiter is the only limiter. It is per
process and resets on restart - adequate for this scale, and worth remembering before
posting the link somewhere busy.

### Prove the deployment is current

Merging to main deploys both halves (Railway builds the backend, Vercel the web). After a
merge:

```bash
make deploy-verify
```

It reads `GET /v1/system/status` through the public URL and answers the questions that
matter: is the running image built from origin/main (`RS_BUILD_SHA`), did the migrations
land, and are the scheduled runs actually happening (the `scheduler_runs` ledger, with the
newest paper's age beside it). It exits nonzero when the deployment is unreachable, stale,
or missing the catalogue routes - each with a message saying which. The web footer's
"Newest paper" line reads the same endpoint, so staleness is also visible on the page.

## 2. Auth0

Three things get created, and each hands back values that go somewhere specific. The gotchas
below are the ones that cost an afternoon rather than a minute.

### The application people sign in through

Applications -> Create Application -> **Regular Web Application** (not Single Page: this flow
runs on the server and keeps a client secret). Skip the quickstart, go to Settings.

- **Allowed Callback URLs**: `http://localhost:4321/callback` while testing locally, and
  `https://<your-site>.vercel.app/callback` once Vercel has given you a name. Both can sit in
  the list, comma separated.
- **Allowed Logout URLs**: `http://localhost:4321` and `https://<your-site>.vercel.app`.
- Advanced Settings -> Grant Types: **Authorization Code** and **Refresh Token** both ticked.
  Without the second, sessions end when the access token expires instead of renewing.

Copy the Domain, Client ID and Client Secret.

### The API the tokens are for

APIs -> Create API.

- **Name**: anything. **Identifier**: `https://researchscout.api` - this is the audience the
  backend validates, it is a name rather than a URL that resolves, and it must match
  `RS_OIDC_AUDIENCE` character for character.
- **Signing Algorithm**: RS256. The backend accepts nothing else.
- After creating it, Settings -> **Allow Offline Access: on**. This is the one people miss:
  without it the `offline_access` scope is ignored, no refresh token is issued, and everyone
  is signed out when their access token expires.

### The application that deletes accounts

Applications -> Create Application -> **Machine to Machine**, authorized for the **Auth0
Management API**, with the **`delete:users`** scope and nothing else.

Copy its Domain, Client ID and Client Secret - they are different from the first application's.

### Where the values go

| Auth0 | Setting | Format |
| --- | --- | --- |
| Domain | `RS_OIDC_ISSUER` on the Railway service | `https://tenant.us.auth0.com/` - scheme and trailing slash |
| Domain | `RS_AUTH0_DOMAIN` on the Railway service | `tenant.us.auth0.com` - bare, no scheme, no slash |
| Domain | `AUTH0_DOMAIN` in Vercel | bare, as above |
| API Identifier | `RS_OIDC_AUDIENCE` and Vercel's `AUTH0_AUDIENCE` | identical in both |
| Web app Client ID / Secret | Vercel `AUTH0_CLIENT_ID` / `AUTH0_CLIENT_SECRET` | |
| M2M Client ID / Secret | `RS_AUTH0_MGMT_CLIENT_ID` / `RS_AUTH0_MGMT_CLIENT_SECRET` | |

The trailing slash on the issuer matters: it is compared against the `iss` claim in every
token, which Auth0 writes with one. A mismatch is a 401 on every request and says nothing
about why.

The signing keys need no configuration - the backend reads the issuer's discovery document to
find them.

One thing to look at before inviting anyone: Authentication -> Social. A new tenant enables
Google using Auth0's shared development keys, which are rate limited and show an Auth0 notice
on the consent screen. Either turn the connection off or give it your own Google credentials.

Changing a variable on the Railway service redeploys it, so the new issuer is picked up on
save.

Check: `curl -H "x-rs-service-token: <token>" https://<the-domain>/v1/me` returns 401 (no
account token), and the same request to `/v1/papers` still returns papers.

## 3. Vercel

The frontend is `apps/web`. Nothing to change before deploying: the config picks its adapter
from the environment, so Vercel's builds (where `VERCEL=1` is set) get the serverless adapter
and everything local keeps the standalone Node server.

Project settings: root directory `apps/web`, framework Astro. Environment variables:

| Variable | Value |
| --- | --- |
| `API_URL` | the Railway api service's public https URL |
| `SITE_URL` | `https://<site>` |
| `AUTH0_DOMAIN` | `<tenant>.us.auth0.com` |
| `AUTH0_CLIENT_ID` | the regular web application's id |
| `AUTH0_CLIENT_SECRET` | its secret |
| `AUTH0_AUDIENCE` | the API identifier from step 2 |
| `SESSION_SECRET` | `openssl rand -base64 48` |
| `API_SERVICE_TOKEN` | the same value as `RS_SERVICE_TOKEN` on the Railway service |

Keep the Hobby plan's terms in view: non-commercial personal use only, and that includes
donation links and advertising.

Check: sign in on the deployed site, accept the terms once, save a paper, sign out and back in.
The terms dialog must not appear the second time.

## Monitoring

There is no external monitoring stack. The scheduler's health task self-checks every half
hour (ingest cadence, failing streaks, weekend-aware corpus freshness, hung runs,
retention), `GET /v1/system/status` reports the verdict, and the about page renders it.
`make deploy-verify` reads the same payload after every merge. Railway's own dashboard
carries the service logs, restart history, and resource graphs; its healthcheck restarts
the container when `/healthz` stops answering.
