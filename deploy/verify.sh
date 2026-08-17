#!/usr/bin/env bash
# Is the deployed backend current, migrated, and fetching? One command instead of an
# afternoon of docker inspect - this is the check that would have caught a stack running
# two-day-old code with a frozen environment on day one.
#
# Run from the repo root (make deploy-verify does). Reads deploy/.env only for the service
# token; prints nothing secret. Exits nonzero when the deployment is unreachable, stale, or
# missing the catalogue routes.
set -euo pipefail

BASE="http://127.0.0.1:8001"
ENV_FILE="deploy/.env"

token=""
if [ -f "$ENV_FILE" ]; then
  token=$(grep '^RS_SERVICE_TOKEN=' "$ENV_FILE" | cut -d= -f2- || true)
fi
auth=()
if [ -n "$token" ]; then
  auth=(-H "x-rs-service-token: $token")
fi

status=$(curl -sf -m 10 "${auth[@]}" "$BASE/v1/system/status") || {
  echo "FAIL: $BASE/v1/system/status is unreachable or unknown."
  echo "      If the stack is up, the image predates the status endpoint:"
  echo "      run make deploy-build && make deploy-up."
  exit 1
}

# The API being up on 127.0.0.1 says nothing about the world reaching it: this machine
# resolves the funnel hostname through the tailnet, so a local curl of the public URL lies.
# Ask a public resolver instead - the record only exists while the control plane holds a
# live funnel registration, and a reboot can drop it while the client still says Funnel on.
if command -v tailscale > /dev/null 2>&1 && command -v dig > /dev/null 2>&1 \
  && tailscale serve status --json 2> /dev/null | grep -q '"AllowFunnel"'; then
  host=$(tailscale status --json 2> /dev/null | python3 -c '
import json, sys
print(((json.load(sys.stdin).get("Self") or {}).get("DNSName") or "").rstrip("."))
')
  if [ -n "$host" ]; then
    ip=$(dig +short +time=3 +tries=1 @8.8.8.8 "$host" A 2> /dev/null | head -1)
    if [ -z "$ip" ]; then
      ip=$(dig +short +time=3 +tries=1 @1.1.1.1 "$host" A 2> /dev/null | head -1)
    fi
    if [ -z "$ip" ]; then
      echo "FAIL: $host has no public DNS record - the funnel is not published, so the"
      echo "      site's proxy cannot reach the API even though local checks pass."
      echo "      Re-register with: tailscale funnel reset && tailscale funnel --bg 8001"
      echo "      and check https://status.tailscale.com before deeper surgery."
      exit 1
    fi
    public=$(curl -s -o /dev/null -w '%{http_code}' -m 10 \
      --resolve "$host:443:$ip" "https://$host/healthz") || public="unreachable"
    if [ "$public" != "200" ]; then
      echo "FAIL: https://$host/healthz through the funnel edge ($ip) returned $public."
      exit 1
    fi
    echo "public:    $host resolves ($ip); /healthz answers 200 through the funnel edge"
  fi
else
  echo "public:    no funnel configured on this host - skipping the public-path check"
fi

local_sha=$(git rev-parse --short HEAD)

printf '%s' "$status" | LOCAL_SHA="$local_sha" python3 -c '
import json
import os
import sys
from datetime import UTC, datetime, timedelta

s = json.load(sys.stdin)
problems = []

print("version:   " + s["version"])
sha = s.get("build_sha") or "unstamped"
local = os.environ["LOCAL_SHA"]
print("built at:  " + sha + "  (local HEAD: " + local + ")")
if sha == "unstamped":
    problems.append("the image carries no build SHA - built before stamping, so almost certainly stale")
elif sha[:7] != local[:7]:
    problems.append("the deployed SHA does not match local HEAD - rebuild and redeploy, or check out what is deployed")
print("migration: " + str(s.get("migration")))

# Freshness is judged on arrival time (created_at) - published_at is submission time and
# runs a day or more behind the announcement. Older images only report published_at; fall
# back so this script still speaks to them.
newest = s.get("newest_paper_created_at") or s.get("newest_paper_at")
if newest:
    then = datetime.fromisoformat(newest)
    hours = (datetime.now(UTC) - then).total_seconds() / 3600
    which = "arrived" if s.get("newest_paper_created_at") else "published"
    print("papers:    %s, newest %s %.1f hours old" % (s["papers"], which, hours))
    if hours > 96:
        problems.append("the newest paper is over four days old - the pipeline is not landing anything")
else:
    print("papers:    %s, none stored yet" % s["papers"])

started = s.get("scheduler_started_at")
if started:
    print("scheduler: started " + started)
else:
    print("scheduler: no start-up recorded (image predates the ledger row, or it never ran)")

runs = s.get("runs") or []
if runs:
    print("recent runs:")
    for r in runs[:8]:
        note = (" - " + r["note"]) if r.get("note") else ""
        if r.get("finished_at"):
            mark = "ok " if r["ok"] else "FAIL"
            print("  %s %-9s finished %s%s" % (mark, r["task"], r["finished_at"], note))
        else:
            print("  ..  %-9s running since %s" % (r["task"], r["started_at"]))
else:
    print("recent runs: none recorded yet (the ledger fills as scheduled tasks run)")

health = s.get("health") or []
if health:
    print("health:")
    for c in health:
        print("  %-4s %-16s %s" % (c["status"], c["name"], c["detail"]))
        if c["status"] == "fail":
            problems.append("health check %s failed: %s" % (c["name"], c["detail"]))
last_health = s.get("last_health_run")
if last_health and not last_health.get("ok"):
    problems.append("the last health task run failed: " + (last_health.get("note") or ""))

# A slot that passed after the newest scheduler start-up must have left a run behind. This
# is the check that tells a stalled loop (the host slept through its deadlines) apart from a
# young ledger; a start-up after the slot is a plain restart and raises nothing.
due = s.get("pipeline_due_at")
if due and started:
    due_at = datetime.fromisoformat(due)
    if datetime.fromisoformat(started) <= due_at:
        slack = due_at - timedelta(seconds=120)
        ran_since = any(
            r["task"] != "scheduler" and datetime.fromisoformat(r["started_at"]) >= slack
            for r in runs
        )
        if not ran_since:
            problems.append(
                "the pipeline slot due %s passed with the scheduler up but no run is "
                "recorded - the loop is stalled or dead; check the host slept and "
                "make deploy-logs" % due_at.strftime("%Y-%m-%d %H:%M")
            )

if problems:
    print()
    for p in problems:
        print("PROBLEM: " + p)
    sys.exit(1)
'

models=$(curl -s -o /dev/null -w '%{http_code}' -m 10 "${auth[@]}" "$BASE/v1/models?limit=1")
if [ "$models" != "200" ]; then
  echo "FAIL: /v1/models returned $models - the catalogue routes or migrations are missing."
  exit 1
fi
echo "catalog:   /v1/models answers 200"
echo "verified."
