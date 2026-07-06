# Deploying ResearchScout

Local dev uses compose profiles; `deploy/k3d` runs the chart on a disposable local cluster.
This runbook covers the real thing: a single Oracle Always Free ARM VM (2 OCPU / 12GB since
2026-06-15) running k3s, deployed by GitHub Actions.

## One-time setup

1. **Provision the VM** — needs an OCI account with an API signing key:

   ```bash
   cd infra/terraform
   cp terraform.tfvars.example terraform.tfvars   # fill in; never commit
   terraform init
   ./apply-retry.sh                               # retries through A1 capacity errors
   ```

   Rotate `-var availability_domain_index=1` (or 2) if one AD stays out of capacity for hours.
   Cloud-init installs k3s, helm, sops, helm-secrets, and a 4GB swapfile.

2. **DNS** — point `scout.<your-domain>` and `auth.<your-domain>` A records at the VM's
   public IP (a cheap registrar domain beats sslip.io: shared Let's Encrypt rate limits, and
   Keycloak needs a stable issuer host).

3. **TLS** — on the VM: `ACME_EMAIL=you@example.com bash ~/researchscout/deploy/scripts/bootstrap.sh`
   (installs cert-manager and the `letsencrypt` ClusterIssuer).

4. **Secrets** — generate an age key (`age-keygen`), put the public key in `.sops.yaml`, then:

   ```bash
   cp deploy/secrets/secrets.prod.yaml.example deploy/secrets/secrets.prod.yaml
   # fill in real values, then encrypt in place and commit the encrypted file
   sops encrypt -i deploy/secrets/secrets.prod.yaml
   ```

   Put the age *private* key on the VM at `~/.config/sops/age/keys.txt`.

5. **Domain values** — replace `example.com` in `deploy/charts/researchscout/values-prod.yaml`.

6. **GitHub** — repo secrets `VM_HOST` (public IP or hostname) and `VM_SSH_KEY` (private key
   for the `ubuntu` user), then set the repository variable `DEPLOY_ENABLED=true`.

## Every deploy after that

Push to main. `ci.yml` gates; `deploy.yml` builds the three arm64 images natively on
`ubuntu-24.04-arm` runners, pushes them to GHCR tagged with the commit SHA, rsyncs the chart
to the VM, and runs `helm secrets upgrade` with that tag. Rollback is
`helm rollback rs <revision>` on the VM.

## Memory budget on 12GB

k3s ~1GB, app plane ~4.5GB, Kafka+Keycloak ~1.5GB, obs plane ~2.5GB: ~9.5GB, plus the 4GB
swapfile as headroom. If memory gets tight, trim in this order: Tempo, Prometheus retention,
then the embed worker (index via the ingest CronJob's follow-up `scout index` instead).
