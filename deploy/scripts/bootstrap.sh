#!/usr/bin/env bash
# One-time cluster bootstrap: cert-manager + the Let's Encrypt issuer, then the
# observability plane (kube-prometheus-stack, Loki, OTel collector) trimmed for the 12GB
# VM. Run from a full repo clone after k3s is up (cloud-init installs it) and DNS points
# at the VM:
#
#   ACME_EMAIL=you@example.com GRAFANA_HOST=grafana.example.com bash bootstrap.sh
#
# LOCAL=1 skips cert-manager/TLS and the Grafana ingress for k3d rehearsals (Grafana then
# via: kubectl port-forward svc/kps-grafana 3000:80, admin/admin):
#
#   LOCAL=1 bash deploy/scripts/bootstrap.sh
set -euo pipefail
cd "$(dirname "$0")"

LOCAL="${LOCAL:-0}"
COLLECTOR_CONFIG=../../config/otel/collector-k8s.yaml
[ -f "$COLLECTOR_CONFIG" ] || { echo "missing $COLLECTOR_CONFIG — run from a full repo clone"; exit 1; }

if [ "$LOCAL" = "1" ]; then
  GRAFANA_ADMIN_PASSWORD="${GRAFANA_ADMIN_PASSWORD:-admin}"
else
  : "${ACME_EMAIL:?set ACME_EMAIL to the address for Let's Encrypt notices}"
  : "${GRAFANA_HOST:?set GRAFANA_HOST to Grafana's hostname, e.g. grafana.example.com}"
  # Grafana's admin password lives with the other secret material (see
  # deploy/secrets/secrets.prod.yaml.example, key grafana.adminPassword).
  GRAFANA_ADMIN_PASSWORD="$(sops decrypt --extract '["grafana"]["adminPassword"]' ../secrets/secrets.prod.yaml)"

  helm repo add jetstack https://charts.jetstack.io --force-update
  helm upgrade --install cert-manager jetstack/cert-manager \
    --namespace cert-manager --create-namespace \
    --set crds.enabled=true

  kubectl wait --for=condition=available deployment --all -n cert-manager --timeout=300s
  sed "s/ACME_EMAIL/${ACME_EMAIL}/" cluster-issuer.yaml | kubectl apply -f -
fi

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update
helm repo add grafana https://grafana.github.io/helm-charts --force-update
helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts --force-update

# The observability plane installs into the app's (default) namespace so every service URL
# matches the compose profile: the app exports to otel-collector:4317, the collector and
# Grafana reach Loki at loki:3100, Prometheus scrapes otel-collector:8889.

# kube-prometheus-stack, trimmed: no Alertmanager, 3 days of retention, small resource
# caps, and no monitors for control-plane parts k3s embeds (nothing listens on their
# ports). The first install pulls several images — expect a few quiet minutes.
{
  cat <<EOF
fullnameOverride: kps
alertmanager:
  enabled: false
kubeControllerManager:
  enabled: false
kubeScheduler:
  enabled: false
kubeProxy:
  enabled: false
kubeEtcd:
  enabled: false
prometheus:
  prometheusSpec:
    retention: 3d
    resources:
      requests: {cpu: 100m, memory: 400Mi}
      limits: {memory: 600Mi}
    additionalScrapeConfigs:
      - job_name: otel-collector
        static_configs:
          - targets: ["otel-collector:8889"]
grafana:
  adminPassword: "${GRAFANA_ADMIN_PASSWORD}"
  resources:
    requests: {cpu: 50m, memory: 128Mi}
    limits: {memory: 256Mi}
  additionalDataSources:
    - name: Loki
      type: loki
      access: proxy
      url: http://loki:3100
EOF
  if [ "$LOCAL" != "1" ]; then
    cat <<EOF
  ingress:
    enabled: true
    ingressClassName: traefik
    annotations:
      cert-manager.io/cluster-issuer: letsencrypt
    hosts: ["${GRAFANA_HOST}"]
    tls:
      - secretName: grafana-tls
        hosts: ["${GRAFANA_HOST}"]
EOF
  fi
} | helm upgrade --install kps prometheus-community/kube-prometheus-stack -f -

# Loki: one binary, filesystem storage, 7 days of retention, caches and canary off.
helm upgrade --install loki grafana/loki -f - <<EOF
deploymentMode: SingleBinary
loki:
  auth_enabled: false
  commonConfig:
    replication_factor: 1
  storage:
    type: filesystem
  schemaConfig:
    configs:
      - from: "2024-04-01"
        store: tsdb
        object_store: filesystem
        schema: v13
        index:
          prefix: index_
          period: 24h
  limits_config:
    retention_period: 7d
  compactor:
    retention_enabled: true
    delete_request_store: filesystem
singleBinary:
  replicas: 1
  persistence:
    size: 5Gi
  resources:
    requests: {cpu: 100m, memory: 256Mi}
    limits: {memory: 512Mi}
read:
  replicas: 0
write:
  replicas: 0
backend:
  replicas: 0
gateway:
  enabled: false
chunksCache:
  enabled: false
resultsCache:
  enabled: false
lokiCanary:
  enabled: false
test:
  enabled: false
EOF

# OTel collector: the config comes verbatim from config/otel/collector-k8s.yaml, indented
# under alternateConfig (which replaces the chart's default config instead of merging).
{
  cat <<EOF
mode: deployment
fullnameOverride: otel-collector
image:
  repository: otel/opentelemetry-collector-contrib
  tag: "0.155.0"
resources:
  requests: {cpu: 50m, memory: 128Mi}
  limits: {memory: 256Mi}
ports:
  jaeger-compact:
    enabled: false
  jaeger-thrift:
    enabled: false
  jaeger-grpc:
    enabled: false
  zipkin:
    enabled: false
  prom-exporter:
    enabled: true
    containerPort: 8889
    servicePort: 8889
    protocol: TCP
alternateConfig:
EOF
  sed 's/^/  /' "$COLLECTOR_CONFIG"
} | helm upgrade --install otel-collector open-telemetry/opentelemetry-collector -f -

kubectl rollout status deployment/otel-collector --timeout=300s
kubectl rollout status deployment/kps-grafana --timeout=600s
echo "bootstrap complete"
