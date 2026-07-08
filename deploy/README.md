# Deploying ResearchScout

Local dev uses compose profiles; `deploy/k3d` runs the chart on a disposable local cluster
(`LOCAL=1 bash deploy/scripts/bootstrap.sh` adds the same observability stack to it, minus
cert-manager/TLS). This runbook covers the real thing: a single Oracle Always Free ARM VM
(2 OCPU / 12GB since 2026-06-15) running k3s, deployed by GitHub Actions.

## One-time setup

1. **Provision the VM** — needs an OCI account with an API signing key:

   ```bash
   cd infra/terraform
   cp terraform.tfvars.example terraform.tfvars   # fill in; never commit
   terraform init
   ./apply-retry.sh                               # retries through A1 capacity errors
   ```

   Rotate `-var availability_domain_index=1` (or 2) if one AD stays out of capacity for hours.
   Cloud-init installs k3s, helm, sops, helm-secrets, a 4GB swapfile, and Ollama (see
   "Ollama on the VM" below).

2. **DNS** — point `scout.<your-domain>`, `auth.<your-domain>`, and `grafana.<your-domain>`
   A records at the VM's public IP (a cheap registrar domain beats sslip.io: shared Let's
   Encrypt rate limits, and Keycloak needs a stable issuer host).

3. **Secrets** — generate an age key (`age-keygen`), put the public key in `.sops.yaml`, then:

   ```bash
   cp deploy/secrets/secrets.prod.yaml.example deploy/secrets/secrets.prod.yaml
   # fill in real values, then encrypt in place and commit the encrypted file
   sops encrypt -i deploy/secrets/secrets.prod.yaml
   ```

   Put the age *private* key on the VM at `~/.config/sops/age/keys.txt`.

4. **Bootstrap the cluster** — on the VM, from a clone of this repo (needs the secrets from
   step 3 in place: it reads Grafana's admin password out of them):

   ```bash
   ACME_EMAIL=you@example.com GRAFANA_HOST=grafana.<your-domain> \
     bash ~/researchscout/deploy/scripts/bootstrap.sh
   ```

   Installs cert-manager with the `letsencrypt` ClusterIssuer, then the observability
   plane: kube-prometheus-stack (Grafana served at `https://grafana.<your-domain>`), Loki,
   and the OTel collector fed by `config/otel/collector-k8s.yaml`.

5. **Domain values** — replace `example.com` in `deploy/charts/researchscout/values-prod.yaml`,
   and set `llm.external.nodeIP` there to the VM's private IP (`hostname -I | awk '{print $1}'`
   on the VM).

6. **GitHub** — repo secrets `VM_HOST` (public IP or hostname) and `VM_SSH_KEY` (private key
   for the `ubuntu` user), then set the repository variable `DEPLOY_ENABLED=true`.

## Every deploy after that

Push to main. `ci.yml` gates; `deploy.yml` builds the three arm64 images natively on
`ubuntu-24.04-arm` runners, pushes them to GHCR tagged with the commit SHA, rsyncs the chart
to the VM, and runs `helm secrets upgrade` with that tag. Rollback is
`helm rollback rs <revision>` on the VM.

## Ollama on the VM

Cloud-init installs Ollama as a **host systemd service** (not a pod), so the ~2GB of model
weights stay out of image pulls and the Kubernetes memory ledger. A systemd override pins
`OLLAMA_HOST=0.0.0.0:11434` (pods reach it via the node's private IP; the OCI security
list still only admits 22/80/443 from outside) and `OLLAMA_KEEP_ALIVE=30m` (how long the
model stays resident after the last request). `qwen2.5:3b-instruct` is pulled at first boot.

In-cluster, the chart's `ollama` Service + Endpoints (`templates/ollama-endpoint.yaml`,
gated on `llm.external.enabled`) forward `http://ollama:11434/v1` to that host service —
which is what `llm.baseUrl` points at in values-prod. Health checks on the VM:

```bash
systemctl status ollama
curl -s localhost:11434/api/tags        # the pulled models
kubectl get endpoints ollama            # must show the node's private IP
```

To swap models: `ollama pull <model>` on the VM, set `llm.model` in values-prod, redeploy.

## Hardening

- **Backups**: create an Object Storage bucket (`researchscout-backups`) and a customer secret
  key in OCI, put the S3 endpoint + keys in the encrypted secrets values, and set
  `backup.enabled=true` in values-prod. Nightly `pg_dump` lands in the bucket; restore with
  `pg_restore -d researchscout <file>`.
- **Status page**: set `uptimeKuma.enabled=true` and point a `status.` DNS record at the VM;
  configure monitors for the site, auth, and a dead-man check on the digest CronJob.
- **Renovate**: install the Renovate GitHub App on the repo — `renovate.json` groups
  OpenTelemetry and Astro packages and batches minor/patch bumps weekly.
- **Trivy**: CI fails on CRITICAL/HIGH filesystem findings and leaked secrets; deploys scan
  each pushed image for CRITICALs before the helm upgrade runs.

## Memory budget on 12GB

| Component | Budget |
| --- | --- |
| k3s + system | ~1.0GB |
| App plane (API, web, workers, Postgres, Redis) | ~4.5GB |
| Kafka + Keycloak | ~1.5GB |
| Observability (Prometheus, Loki, Grafana, collector — no Tempo in prod) | ~1.6GB |
| Ollama with qwen2.5:3b-instruct resident | ~2.6GB |
| **Total** | **~11.2GB**, plus the 4GB swapfile as headroom |

If memory gets tight, trim in this order:

1. **Prometheus retention** — drop `retention: 3d` to `1d` in `deploy/scripts/bootstrap.sh`
   and re-run it.
2. **Embed worker** — set `workers.embed.enabled=false`; index via `scout index` after
   ingests instead.
3. **Model unload** — lower `OLLAMA_KEEP_ALIVE` (e.g. `5m`) in the systemd override
   (`systemctl edit ollama`), so the model leaves RAM between chats and digests.
