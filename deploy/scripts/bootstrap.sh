#!/usr/bin/env bash
# One-time cluster bootstrap on the VM: cert-manager + the Let's Encrypt issuer.
# Run after k3s is up (cloud-init installs it) and DNS points at the VM.
set -euo pipefail
cd "$(dirname "$0")"

: "${ACME_EMAIL:?set ACME_EMAIL to the address for Let's Encrypt notices}"

helm repo add jetstack https://charts.jetstack.io --force-update
helm upgrade --install cert-manager jetstack/cert-manager \
  --namespace cert-manager --create-namespace \
  --set crds.enabled=true

kubectl wait --for=condition=available deployment --all -n cert-manager --timeout=300s
sed "s/ACME_EMAIL/${ACME_EMAIL}/" cluster-issuer.yaml | kubectl apply -f -
echo "bootstrap complete"
