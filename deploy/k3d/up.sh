#!/usr/bin/env bash
# Bring the full stack up on a disposable k3d cluster; tear down with down.sh.
set -euo pipefail
cd "$(dirname "$0")/../.."

k3d cluster create --config deploy/k3d/k3d.yaml

docker build -f deploy/docker/api.Dockerfile -t researchscout/api:local .
docker build -f deploy/docker/web.Dockerfile -t researchscout/web:local apps/web
docker build -f deploy/docker/worker.Dockerfile -t researchscout/worker:local .
k3d image import -c rs \
  researchscout/api:local researchscout/web:local researchscout/worker:local

helm upgrade --install rs deploy/charts/researchscout \
  -f deploy/charts/researchscout/values-local.yaml

kubectl wait --for=condition=available deployment --all --timeout=600s
echo
echo "up: http://scout.localtest.me:8080  (keycloak: http://auth.localtest.me:8080, demo/demo)"
