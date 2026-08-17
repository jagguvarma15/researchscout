#!/usr/bin/env bash
# Every ten minutes: is the stack up, is the API answering, does the world still resolve the
# funnel hostname - and once a day, does the server's own health block agree? Anything that
# needed fixing or could not be fixed lands as a macOS notification, so a broken pipeline is
# a banner on the screen instead of a discovery next week.
#
# Repo-free like backup.sh (launchd cannot read ~/Desktop). The service token is rendered
# into watchdog.env beside this script by `make watchdog-schedule` (chmod 600).
set -uo pipefail

AGENT_DIR="$(cd "$(dirname "$0")" && pwd)"
[ -f "$AGENT_DIR/watchdog.env" ] && . "$AGENT_DIR/watchdog.env"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
notify() {
  log "NOTIFY: $1"
  osascript -e "display notification \"$1\" with title \"ResearchScout\"" > /dev/null 2>&1 || true
}

# 1. The Docker daemon. If it is down, start it and let the next tick continue - the daemon
# needs longer to come up than this run should wait.
if ! docker system info > /dev/null 2>&1; then
  log "docker daemon down; launching Docker"
  open -g -a Docker
  notify "Docker was down - relaunching, next check in 10 minutes"
  exit 0
fi

# 2. Stopped stack containers.
for name in researchscout-postgres-1 researchscout-api-1 researchscout-scheduler-1; do
  state=$(docker inspect -f '{{.State.Status}}' "$name" 2> /dev/null || echo absent)
  if [ "$state" = "exited" ] || [ "$state" = "created" ]; then
    docker start "$name" > /dev/null 2>&1 && notify "$name was stopped - started it"
  fi
done

# 3. The API itself, from the host.
if ! curl -sf -m 10 http://127.0.0.1:8001/healthz > /dev/null; then
  notify "the API on 127.0.0.1:8001 is not answering healthz"
fi

# 4. The funnel's public DNS record. On-tailnet resolution lies (MagicDNS answers), so ask a
# public resolver; an empty answer means the world cannot reach the site even though every
# local probe passes. Re-asserting is idempotent and rebuilds the registration.
host=$(tailscale status --json 2> /dev/null | /usr/bin/python3 -c \
  'import json,sys; print(((json.load(sys.stdin).get("Self") or {}).get("DNSName") or "").rstrip("."))' \
  2> /dev/null || true)
if [ -n "$host" ]; then
  record=$(dig +short +time=3 +tries=1 @8.8.8.8 "$host" A 2> /dev/null | head -1)
  if [ -z "$record" ]; then
    tailscale funnel --bg 8001 > /dev/null 2>&1
    notify "funnel DNS record for $host was missing - re-asserted the funnel"
  fi
fi

# 5. Once a day, the server's own verdict: the status endpoint carries the health checks the
# scheduler ran (including funnel DNS from inside). Reusing the server-side logic keeps this
# script free of a repo checkout, which deploy/verify.sh needs and launchd cannot reach.
stamp="$AGENT_DIR/.watchdog-status-day"
today=$(date '+%Y-%m-%d')
if [ "$(cat "$stamp" 2> /dev/null)" != "$today" ]; then
  auth=()
  [ -n "${RS_SERVICE_TOKEN:-}" ] && auth=(-H "x-rs-service-token: $RS_SERVICE_TOKEN")
  status=$(curl -sf -m 10 "${auth[@]}" http://127.0.0.1:8001/v1/system/status || true)
  if [ -z "$status" ]; then
    notify "daily check: /v1/system/status did not answer"
  else
    problems=$(printf '%s' "$status" | /usr/bin/python3 -c '
import json, sys
s = json.load(sys.stdin)
bad = [c["name"] for c in s.get("health") or [] if c.get("status") == "fail"]
run = s.get("last_health_run")
if run is not None and not run.get("ok"):
    bad.append("health task: " + (run.get("note") or "failed"))
print("; ".join(bad))
' 2> /dev/null || echo "unparseable status payload")
    if [ -n "$problems" ]; then
      notify "daily check: $problems"
    else
      log "daily check ok"
    fi
    printf '%s' "$today" > "$stamp"
  fi
fi

log "watchdog pass done"
