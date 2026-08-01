# Deploying the backend

The public site is two halves: the Astro frontend on Vercel, and this - the API, the database
and the refresh loop - running in Docker on the machine you are reading this on, published
through an outbound Cloudflare tunnel. No inbound port is opened.

```
visitor -> researchscout.<domain>   Vercel, Astro SSR, holds the session cookie
              |  server-side proxy attaches the access token
              v
           api.<domain>             Cloudflare: TLS, WAF rate limit, Access service token
              |  outbound tunnel only
              v
           cloudflared -> api:8000  this compose stack
                          postgres, scheduler, [stream], [pdc-agent]
                          Ollama stays on the host
```

## Before the first run

1. `cp deploy/.env.example deploy/.env` and fill it in. `POSTGRES_PASSWORD` is chosen once: it
   is baked into the database volume on first start.
2. Ollama on the host: `ollama serve` and `ollama pull qwen2.5:3b-instruct`. It stays outside
   Docker deliberately - Docker on macOS has no Metal passthrough, so a containerised model
   runs on CPU at a fraction of the speed.
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
docker compose -f deploy/docker-compose.yml --profile tunnel up -d      # publishes the API
docker compose -f deploy/docker-compose.yml --profile monitoring up -d  # Grafana Cloud PDC
```

## Moving the existing data in

The development stack keeps its data in `.local/pgdata` under Homebrew Postgres; the deployment
keeps its own volume. Copy one into the other once:

```bash
# 1. Dump the development database (with the local stack running).
pg_dump -h localhost -p 5432 -U researchscout --format plain --no-owner researchscout \
  > /tmp/researchscout-dev.sql

# 2. Start only the database, and let migrations create the schema.
docker compose -f deploy/docker-compose.yml up -d postgres
docker compose -f deploy/docker-compose.yml run --rm migrate

# 3. Restore into it.
docker compose -f deploy/docker-compose.yml exec -T postgres \
  psql --username researchscout --dbname researchscout < /tmp/researchscout-dev.sql

# 4. Check the counts match before trusting it.
psql -h localhost -p 5432 -U researchscout -d researchscout -c 'select count(*) from papers'
psql -h localhost -p 5433 -U researchscout -d researchscout -c 'select count(*) from papers'
```

The Homebrew cluster is left untouched, so it stays the rollback.

## Backups

```bash
make backup                    # dump to ~/backups/researchscout, keep 7 days, verify
BACKUP_DIR=/elsewhere make backup
```

Run it nightly. On macOS, `launchd` survives reboots where `cron` is deprecated:

```bash
# ~/Library/LaunchAgents/com.researchscout.backup.plist -> runs deploy/backup.sh at 03:30
launchctl load ~/Library/LaunchAgents/com.researchscout.backup.plist
```

To restore a dump into an empty database:

```bash
gunzip -c ~/backups/researchscout/researchscout-20260801-033000.sql.gz |
  docker compose -f deploy/docker-compose.yml exec -T postgres \
    psql --username researchscout --dbname researchscout
```

## What this machine owes the site

The backend is a laptop. When it sleeps, reboots or travels, the API is unreachable: the
frontend keeps serving cached pages with a banner saying so, and the terms disclaim
availability. That is the honest arrangement, not a bug to be surprised by later. Moving
Postgres and the API to a small always-on host is the fix when it stops being acceptable.
