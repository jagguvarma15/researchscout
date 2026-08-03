#!/usr/bin/env bash
# A backup that exists.
#
# Once other people have accounts here, the database holds things they cannot get back: their
# reading lists, their interests, the terms they accepted. This dumps it, keeps a week, and
# says plainly whether it worked - a backup script that fails quietly is worse than none,
# because it also removes the worry that would have made you check.
#
#   deploy/backup.sh              write today's dump and prune old ones
#   BACKUP_DIR=/path deploy/backup.sh
#
# Deliberately depends on nothing in the repository: it addresses the database container by
# name rather than through the compose file. `make backup-schedule` installs a copy outside
# the home folders macOS protects, because a launchd job cannot read ~/Desktop or ~/Documents
# and fails with "Operation not permitted" long before it reaches Postgres.
#
# Restore is in the runbook: deploy/README.md.

set -euo pipefail

CONTAINER="${CONTAINER:-researchscout-postgres-1}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups/researchscout}"
KEEP_DAYS="${KEEP_DAYS:-7}"
stamp="$(date +%Y%m%d-%H%M%S)"
target="$BACKUP_DIR/researchscout-$stamp.sql.gz"

mkdir -p "$BACKUP_DIR"

if ! docker inspect --format '{{.State.Running}}' "$CONTAINER" 2>/dev/null | grep -q true; then
  echo "backup: $CONTAINER is not running, nothing dumped" >&2
  exit 1
fi

# Local connections inside the container are trusted, so no password is needed here and none
# is stored in the schedule.
docker exec "$CONTAINER" \
  pg_dump --username researchscout --format plain --no-owner researchscout |
  gzip >"$target.partial"

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
