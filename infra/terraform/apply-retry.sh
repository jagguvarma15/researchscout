#!/usr/bin/env bash
# A1 "Out of host capacity" errors are routine on the free tier: retry until placed.
# Rotate -var availability_domain_index=0/1/2 if one AD stays dry for hours.
set -uo pipefail
cd "$(dirname "$0")"

attempt=1
while true; do
  echo "apply attempt ${attempt}..."
  if terraform apply -auto-approve "$@"; then
    terraform output
    exit 0
  fi
  attempt=$((attempt + 1))
  sleep 60
done
