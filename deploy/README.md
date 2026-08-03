# Deploying the backend

The public site is two halves: the Astro frontend on Vercel, and this - the API, the database
and the refresh loop - running in Docker on the machine you are reading this on, published
through Tailscale Funnel. No inbound port is opened and this machine's address stays hidden.

```
visitor -> researchscout.vercel.app   Vercel, Astro SSR, holds the session cookie
              |  server-side proxy attaches the account token, the service
              |  token, and the visitor's address
              v
           <machine>.<tailnet>.ts.net Tailscale Funnel: TLS, outbound only
              |
              v
           api:8001                   this compose stack
                                      postgres, scheduler, [stream], [pdc-agent]
                                      Ollama stays on the host
```

The API is open on localhost and closed to everyone else by one shared secret
(`RS_SERVICE_TOKEN`); requests without it get a 404. Setting up Funnel, the token and the rest
is `deploy/PUBLISHING.md`.

## Before the first run

1. `cp deploy/.env.example deploy/.env` and fill it in. `POSTGRES_PASSWORD` is chosen once: it
   is baked into the database volume on first start.
2. Ollama on the host: `brew services start ollama` and `ollama pull qwen2.5:3b-instruct`. Use
   the service rather than `ollama serve` in a terminal - answers and the digest depend on it,
   and a model that only runs until the next reboot fails silently afterwards. It stays outside
   Docker deliberately: Docker on macOS has no Metal passthrough, so a containerised model
   would run on CPU at a fraction of the speed.
3. Docker Desktop needs enough memory for Postgres plus the API's models. 4 GB works without
   the stream profile; with it, expect pressure on an 8 GB machine.

## Running it

```bash
make deploy-build     # build the backend image (large: torch is a runtime dependency)
make deploy-up        # postgres, migrations, api, scheduler
make deploy-ps        # what is running
make deploy-logs      # follow all of it
make deploy-down      # stop, keeping the data volume
```

The API listens on `127.0.0.1:8001` so it never collides with the development stack on 8000.
Migrations run as their own service and must finish before the API starts, so a deploy never
serves against a schema it does not match.

Optional profiles:

```bash
make deploy-up-stream       # adds kafka and the streaming worker
docker compose -f deploy/docker-compose.yml --profile monitoring up -d  # Grafana Cloud PDC
tailscale funnel --bg 8001  # publishes the API (on the host, not in compose)
```

## Moving the existing data in

The development stack keeps its data in `.local/pgdata` under Homebrew Postgres; the deployment
keeps its own volume. Copy one into the other once.

Note the drop and recreate: `make deploy-up` has already run the migrations, so the target
holds an empty schema, and restoring a plain dump on top of it fails on every `CREATE TABLE`.
The dump carries its own schema and its own `alembic_version`, so the cleanest target is an
empty database rather than a migrated one.

```bash
# 1. Dump the development database (with the local stack running).
pg_dump -h localhost -p 5432 -U researchscout --format plain --no-owner researchscout \
  > /tmp/researchscout-dev.sql

# 2. Free the database: the API and scheduler hold connections that block a drop.
docker compose -f deploy/docker-compose.yml stop api scheduler

# 3. Recreate it empty, then restore into it.
docker compose -f deploy/docker-compose.yml exec -T postgres \
  psql -U researchscout -d postgres \
  -c 'DROP DATABASE researchscout WITH (FORCE)' \
  -c 'CREATE DATABASE researchscout OWNER researchscout'

docker compose -f deploy/docker-compose.yml exec -T postgres \
  psql -U researchscout -d researchscout -v ON_ERROR_STOP=1 < /tmp/researchscout-dev.sql

docker compose -f deploy/docker-compose.yml start api scheduler
```

Check the counts match before trusting it:

```bash
psql -h localhost -p 5432 -U researchscout -d researchscout -c 'select count(*) from papers'
docker compose -f deploy/docker-compose.yml exec -T postgres \
  psql -U researchscout -d researchscout -c 'select count(*) from papers'
```

The Homebrew cluster is left untouched, so it stays the rollback.

## Only one of them may ingest

The development stack and the deployment both know how to fetch from arXiv - `scout stream
serve` on the host, the scheduler in compose - and they share this machine's address. The
three-second floor arXiv asks for is held per process, so running both halves it, and the
first sign is 429s on every request for a while afterwards.

So: with the deployment running, stop the development ingest.

```bash
[ -f .local/run/stream.pid ] && kill $(cat .local/run/stream.pid) && rm .local/run/stream.pid
```

`make start` brings it back for development; just do not leave both running. The development
API and web app are fine either way - they only reach arXiv when somebody asks them to.

## Backups

```bash
make backup             # dump to ~/backups/researchscout, keep 7 days, verify the file
make backup-schedule    # and nightly at 03:30, under launchd
make backup-unschedule  # stop it
```

`backup-schedule` copies the script to `~/Library/Application Support/researchscout/` and
points the agent there. That indirection is not fussiness: macOS denies launchd jobs access to
`~/Desktop`, `~/Documents` and `~/Downloads`, so a job pointed at a checkout in one of those
fails with "Operation not permitted" before it reaches Postgres - and writes that message to a
log it also cannot create. The script addresses the database container by name for the same
reason, so it needs nothing from the repository. **After changing `deploy/backup.sh`, rerun
`make backup-schedule`** to refresh the copy.

Check it: `launchctl kickstart -p gui/$(id -u)/com.researchscout.backup`, then read
`~/Library/Application Support/researchscout/backup.log`.

To restore a dump into an empty database:

```bash
gunzip -c ~/backups/researchscout/researchscout-20260801-033000.sql.gz |
  docker compose -f deploy/docker-compose.yml exec -T postgres \
    psql --username researchscout --dbname researchscout
```

## What this machine owes the site

The backend is a desktop at home, which is a better arrangement than a laptop - it is not
going to close its lid mid-request - but it is still one machine on one domestic connection.
Reboots, updates, power cuts and outages all reach the site. The frontend keeps serving cached
pages with a banner saying so, and the terms disclaim availability. Moving Postgres and the API
to a hosted machine is the fix when that stops being acceptable.
