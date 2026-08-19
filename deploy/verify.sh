#!/usr/bin/env bash
# Is the deployed backend current, migrated, and fetching? One command instead of an
# afternoon in the Railway dashboard - this is the check that would have caught a stack
# running two-day-old code on day one.
#
# Run from the repo root (make deploy-verify does). Reads deploy/.env for the API URL and
# the service token; prints nothing secret. Exits nonzero when the deployment is
# unreachable, stale, or missing the catalogue routes. The URL here is the same public one
# the site's proxy uses, so a pass proves the whole public path, not just the process.
set -euo pipefail

ENV_FILE="deploy/.env"

BASE="${RAILWAY_API_URL:-}"
token="${RS_SERVICE_TOKEN:-}"
if [ -f "$ENV_FILE" ]; then
  [ -n "$BASE" ] || BASE=$(grep '^RAILWAY_API_URL=' "$ENV_FILE" | cut -d= -f2- || true)
  [ -n "$token" ] || token=$(grep '^RS_SERVICE_TOKEN=' "$ENV_FILE" | cut -d= -f2- || true)
fi
if [ -z "$BASE" ]; then
  echo "FAIL: no RAILWAY_API_URL configured - set it in deploy/.env (the Railway service's"
  echo "      public https URL, no trailing slash)."
  exit 1
fi
BASE="${BASE%/}"

auth=()
if [ -n "$token" ]; then
  auth=(-H "x-rs-service-token: $token")
fi

healthz=$(curl -s -o /dev/null -w '%{http_code}' -m 10 "$BASE/healthz") || healthz="unreachable"
if [ "$healthz" != "200" ]; then
  echo "FAIL: $BASE/healthz returned $healthz - the service is down or still deploying;"
  echo "      check the Railway dashboard for the build and deploy logs."
  exit 1
fi
echo "public:    $BASE/healthz answers 200"

status=$(curl -sf -m 10 "${auth[@]}" "$BASE/v1/system/status") || {
  echo "FAIL: $BASE/v1/system/status is unreachable or unknown."
  echo "      If /healthz answers, the service token is wrong or the image predates the"
  echo "      status endpoint - compare RS_SERVICE_TOKEN and the deployed commit."
  exit 1
}

# Railway builds whatever main points at, so origin/main is the SHA the deployment should
# carry. Fetch first or this compares against a stale local ref.
git fetch -q origin main 2> /dev/null || true
local_sha=$(git rev-parse origin/main)

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
print("built at:  " + sha[:12] + "  (origin/main: " + local[:12] + ")")
if sha == "unstamped":
    problems.append("the image carries no build SHA - RS_BUILD_SHA is not set on the service")
elif sha[:7] != local[:7]:
    problems.append("the deployed SHA does not match origin/main - a deploy is in flight, failed, or auto-deploy is off")
print("migration: " + str(s.get("migration")))

# Freshness is judged on arrival time (created_at) - published_at is submission time and
# runs a day or more behind the announcement.
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
# tells a stalled loop apart from a young ledger; a start-up after the slot is a plain
# restart (every redeploy is one) and raises nothing.
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
                "recorded - the loop is stalled or dead; check the Railway service logs"
                % due_at.strftime("%Y-%m-%d %H:%M")
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
