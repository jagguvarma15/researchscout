# Deploying the backend

The backend runs on Railway: one container (API + scheduler via `scout serve all`, built
from `docker/api.Dockerfile`) beside a `pgvector/pgvector:pg17` Postgres with a volume. The
frontend deploys separately on Vercel and reaches the API through its public Railway URL.

```
GitHub main ──push──> Railway build ──migrate (pre-deploy)──> api service ── /healthz
     │                                                            │
     └────push──────> Vercel build ──────────> site ──proxy──────>┘
                                                          Postgres (private network)
```

Account-level setup (Railway project, Auth0, Vercel) is in `PUBLISHING.md`. The full
variable list for the api service is in `.env.example`, which is also the template for the
local `deploy/.env` the two scripts here read.

## Deploying

Merge to main. Railway builds the image, runs `scout db upgrade` as the pre-deploy command,
health-checks `/healthz`, and swaps traffic; Vercel ships the web in parallel. Then, from
the repo root:

```bash
make deploy-verify
```

which proves through the public URL that the deployed commit is origin/main, migrations are
at head, papers are fresh, and the scheduled runs are landing. There is no build or start
step on this machine - if verify says the SHA is stale, look at the Railway deploy logs,
not for a restart command.

One sequencing note: Vercel ships the web before Railway finishes building the backend, so
a PR that changes an API shape has a short window where the new UI talks to the old API.
Requests that 422 during that window are sequencing, not bugs.

## Scheduling

Everything is wall-clock slots inside the one container (`RS_SCHEDULER_*_AT` variables, ET
by default): pipeline 00:30, citations 06:00, report 07:00, fast signals 08:00 and 18:00,
daily set 17:00, health every 30 minutes. A redeploy or restart re-arms the next future
slot; a slot missed while a deploy was in flight is covered by the watermark-derived ingest
window on the next run.

## Moving data in or out

The Postgres service's TCP proxy is the path for both directions; its connection string
lives in `deploy/.env` as `DATABASE_PUBLIC_URL`.

Restore a dump (custom format) into a fresh database:

```bash
pg_restore --no-owner --dbname "$DATABASE_PUBLIC_URL" the-dump.dump
```

Restore one of `backup.sh`'s plain dumps:

```bash
gunzip -c researchscout-<stamp>.sql.gz | psql "$DATABASE_PUBLIC_URL"
```

The pg17 client tools come from `brew install postgresql@17` (the dev stack already needs
them).

## Backups

```bash
make backup
```

dumps through the TCP proxy into `~/backups/researchscout`, keeps a week, and verifies the
file. Railway's volume backups cover the routine case; this is the copy you hold yourself.
Run it before risky migrations.

## When something is wrong

- `make deploy-verify` names the failure: stale SHA, missed slot, failing health check, or
  an unreachable service - each with where to look next.
- The scheduler's health task writes its verdict to the `scheduler_runs` ledger every half
  hour; `GET /v1/system/status` (service token required) and the about page's Status
  section show it.
- Service logs, restarts, and resource graphs are on the Railway dashboard. The healthcheck
  restarts the container when `/healthz` stops answering; `restartPolicyType` in
  `railway.json` covers crashes.
