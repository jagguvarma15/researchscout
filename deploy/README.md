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
by default): pipeline 00:30 (ingest, categorize, index, full text), revisions 01:30,
citations 06:00, report 07:00, fast signals 08:00 and 18:00, daily set 17:00, health every
30 minutes. A redeploy or restart re-arms the next future slot; a slot missed while a
deploy was in flight is covered by the watermark-derived ingest window on the next run,
and a slot whose run fails retries after half an hour - an hour when the failure was
upstream rate limiting - twice, before conceding the day. Only the failure that concedes
the slot reaches the error reporter; the attempts a retry absorbs stay in the ledger.

## Optional capability switches

Each of these is an environment change on the right half of the deployment - the code is
already live, off, and byte-identical to before until the value exists.

On the Railway `api` service:

- `RS_FORYOU_EVENTS=true` - clicked and dwelled papers join the For You profile;
  dismissed papers never come back (needs `RS_FORYOU_CENTROIDS` at 1 or more).
- `RS_HIGHLIGHTS_SYNC=true` - reader highlights follow the account across devices.
- `RS_PUSH_ENABLED=true` with `RS_VAPID_PRIVATE_KEY`, `RS_VAPID_PUBLIC_KEY` and
  `RS_VAPID_SUBJECT` (mint a keypair with `uv run python -m py_vapid` or openssl) -
  browser notices when a digest or report publishes; the account panel then shows the
  toggle.
- `RS_STREAM_LABELS_ENABLED=true` - the categorize task classifies papers against
  `config/labels.yaml`.
- `RS_SCHEDULER_REVISIONS_AT=01:30` - the arXiv revisions sweep (v2s, DOIs, journal
  references) gets its nightly slot; unset, it never runs.
- Signal source credentials: `GITHUB_TOKEN` (then flip `code_adoption.enabled` in
  `config/sources.yaml` - the connector burns anonymous rate limits without it),
  `OPENALEX_API_KEY` (the citation fallback self-skips keyless), `BLUESKY_IDENTIFIER`
  plus `BLUESKY_APP_PASSWORD` (then flip `bluesky.enabled`), and `openreview.enabled`
  seasonally around review cycles.

On Vercel:

- `FEED_TOKEN` - enables the Atom feed at `/feeds/digests.xml?token=<value>`; unset,
  the path answers 404. Share the full URL only with people who should have the feed.

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
  `railway.json` covers crashes - including a scheduler thread death, which now exits the
  process on purpose after writing a `scheduler` failure row to the ledger.
- With the DSNs configured, Sentry holds the tracebacks: backend task and request errors
  in the Python project, page-render and browser errors in the JavaScript one. LangSmith
  holds every LLM call's prompt, tokens, and latency. Both are documented in
  `PUBLISHING.md` under Monitoring.
