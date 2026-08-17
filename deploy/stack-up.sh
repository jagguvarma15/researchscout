#!/usr/bin/env bash
# Bring the deployed stack back after a reboot: Docker first, then the containers, then the
# funnel. Repo-free on purpose, like backup.sh - launchd cannot read ~/Desktop, so this
# addresses containers by name and leans on their restart policy rather than on compose files.
# Installed by `make stack-schedule` (a copy; rerun the target after editing this).
set -uo pipefail

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

if ! docker system info > /dev/null 2>&1; then
  log "docker daemon not running; launching Docker"
  open -g -a Docker
  for _ in $(seq 1 60); do
    sleep 2
    if docker system info > /dev/null 2>&1; then break; fi
  done
fi
if ! docker system info > /dev/null 2>&1; then
  log "docker daemon did not come up within 120s"
  exit 1
fi

# `restart: unless-stopped` revives these with the daemon; `docker start` covers the ones a
# clean shutdown left stopped. Missing containers (a torn-down stack) are only a log line.
for name in researchscout-postgres-1 researchscout-api-1 researchscout-scheduler-1; do
  docker start "$name" > /dev/null 2>&1 && log "started $name" || log "$name already running or absent"
done

# The funnel config survives restarts, but the public DNS record has vanished across reboots
# while the client still said "Funnel on" - re-asserting is idempotent and rebuilds the
# registration.
if command -v tailscale > /dev/null 2>&1; then
  tailscale funnel --bg 8001 > /dev/null 2>&1 && log "funnel re-asserted" || log "funnel re-assert failed"
fi

log "stack-up done"
