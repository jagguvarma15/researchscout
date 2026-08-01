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
# Restore is in the runbook: deploy/README.md.

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups/researchscout}"
KEEP_DAYS="${KEEP_DAYS:-7}"
stamp="$(date +%Y%m%d-%H%M%S)"
target="$BACKUP_DIR/researchscout-$stamp.sql.gz"

mkdir -p "$BACKUP_DIR"

if ! docker compose -f "$here/docker-compose.yml" ps postgres --status running --quiet >/dev/null 2>&1; then
  echo "backup: postgres is not running, nothing dumped" >&2
  exit 1
fi

# -T because there is no terminal in a cron run; the dump streams straight into gzip.
docker compose -f "$here/docker-compose.yml" exec -T postgres \
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
