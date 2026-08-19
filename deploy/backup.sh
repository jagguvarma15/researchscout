#!/usr/bin/env bash
# A backup that exists.
#
# The database holds things users cannot get back: their reading lists, their interests, the
# terms they accepted. This dumps it, keeps a week, and says plainly whether it worked - a
# backup script that fails quietly is worse than none, because it also removes the worry
# that would have made you check.
#
#   deploy/backup.sh              write today's dump and prune old ones
#   BACKUP_DIR=/path deploy/backup.sh
#
# Dumps over the Railway Postgres TCP proxy: DATABASE_PUBLIC_URL in deploy/.env is the
# proxy's connection string (Railway prints it on the Postgres service). Run manually via
# `make backup`; Railway's own volume backups cover the routine case, this covers the
# "export a copy I hold myself" case.
#
# Restore is in the runbook: deploy/README.md.

set -euo pipefail

ENV_FILE="${ENV_FILE:-deploy/.env}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups/researchscout}"
KEEP_DAYS="${KEEP_DAYS:-7}"
stamp="$(date +%Y%m%d-%H%M%S)"
target="$BACKUP_DIR/researchscout-$stamp.sql.gz"

url="${DATABASE_PUBLIC_URL:-}"
if [ -z "$url" ] && [ -f "$ENV_FILE" ]; then
  url=$(grep '^DATABASE_PUBLIC_URL=' "$ENV_FILE" | cut -d= -f2- || true)
fi
if [ -z "$url" ]; then
  echo "backup: no DATABASE_PUBLIC_URL configured - set it in deploy/.env (the Railway" >&2
  echo "        Postgres service's public connection string)" >&2
  exit 1
fi

PG_DUMP="pg_dump"
if ! command -v pg_dump > /dev/null 2>&1; then
  PG_DUMP="$(brew --prefix 2> /dev/null)/opt/postgresql@17/bin/pg_dump"
  if [ ! -x "$PG_DUMP" ]; then
    echo "backup: pg_dump not found - brew install postgresql@17" >&2
    exit 1
  fi
fi

mkdir -p "$BACKUP_DIR"

"$PG_DUMP" --format plain --no-owner "$url" | gzip > "$target.partial"

# Rename only after a clean dump, so a truncated file is never mistaken for a backup.
mv "$target.partial" "$target"

size="$(du -h "$target" | cut -f1)"
echo "backup: wrote $target ($size)"

# Prune by age, and only files this script writes.
deleted="$(find "$BACKUP_DIR" -name 'researchscout-*.sql.gz' -type f -mtime +"$KEEP_DAYS" -print -delete | wc -l | tr -d ' ')"
if [ "$deleted" != "0" ]; then
  echo "backup: pruned $deleted older than $KEEP_DAYS days"
fi

# A dump that gzip cannot read back is not a backup.
if ! gzip -t "$target"; then
  echo "backup: the dump did not verify" >&2
  exit 1
fi
echo "backup: verified"
