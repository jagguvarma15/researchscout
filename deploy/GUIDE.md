# The stack, tool by tool

What each moving part is, why it earns its RAM on a 12GB VM, where this repo configures it,
how data flows through it, how to check it is healthy, and what to tweak later. Commands
assume a shell on the VM (or `kubectl` pointed at the k3d rehearsal cluster); everything
runs in the `default` namespace unless said otherwise.

The one-paragraph map: browsers hit **Traefik** (k3s's built-in ingress), which routes to
the Astro **web** BFF or **Keycloak**. The BFF holds sessions in **Redis** and proxies API
calls with a Bearer token to the FastAPI **api** pod, which reads **Postgres+pgvector** and
streams chat tokens from **Ollama** (a host service outside the cluster). Ingest work rides
**Kafka**: CronJobs emit jobs, workers consume them and write papers/embeddings back to
Postgres. Every service exports telemetry to the **OTel collector**, which fans out to
**Prometheus** (metrics) and **Loki** (logs), both graphed in **Grafana**.

## k3s + Helm

**What/why.** k3s is Kubernetes as one ~70MB binary — API server, scheduler, kubelet,
containerd, plus batteries (Traefik ingress, local-path storage, CoreDNS) in a single
systemd service. On a lone ARM VM it gives us the Kubernetes API and Helm ecosystem for
about 1GB of overhead, with none of the multi-node control-plane machinery. Helm is the
package manager on top: our app is one chart, and third-party charts (cert-manager,
kube-prometheus-stack, Loki, OTel collector) install with one command each.

**Configured in:** `infra/terraform/cloud-init.yaml` (installs k3s, helm, helm-secrets);
`deploy/charts/researchscout/` — hand-rolled templates for every app component:
`api.yaml`, `web.yaml`, `workers.yaml`, `postgres.yaml`, `kafka.yaml`, `keycloak.yaml`,
`redis.yaml`, `ingress.yaml`, `cronjobs.yaml`, `ollama-endpoint.yaml`, plus
`migrate-job.yaml` (a `post-install,post-upgrade` hook at weight -5 that runs
`scout db upgrade` before app pods roll). Values layer up: `values.yaml` (defaults) →
`values-prod.yaml` (domain, prod shape) → `deploy/secrets/secrets.prod.yaml` (SOPS,
secret material). The release is named `rs` (see `.github/workflows/deploy.yml`).

**Data flow.** Traefik terminates TLS (certificates from cert-manager, issuer installed by
`deploy/scripts/bootstrap.sh`) and routes by Host header per `templates/ingress.yaml`:
`scout.<domain>` to `web:4321`, `auth.<domain>` to `keycloak:8080`, `grafana.<domain>` to
Grafana (that ingress is created by the kube-prometheus-stack values in bootstrap.sh).

**Health.**

```bash
kubectl get pods                      # everything Running/Completed
helm ls                               # rs, kps, loki, otel-collector deployed
kubectl top nodes                     # memory against the budget in deploy/README.md
```

**Tweak later.** Replicas and resources live in the values files. `helm rollback rs <rev>`
undoes a bad deploy. `kubectl get events --sort-by=.lastTimestamp` is the first stop when
a pod will not start.

## Ollama

**What/why.** Ollama runs open-weight LLMs behind an OpenAI-compatible HTTP API. It is the
answer engine for chat and digests without per-token cloud costs. It runs as a **host
systemd service on the VM, not a pod**: the ~2GB of model weights stay out of container
images and the Kubernetes memory ledger, and it survives cluster rebuilds.

**Why a 3B model.** The whole stack shares 12GB. `qwen2.5:3b-instruct` at Q4 quantization
holds ~2.6GB resident, leaves the budget intact (table in `deploy/README.md`), and is
strong enough for grounded, citation-checked answers over abstracts — the app's prompts
retrieve the evidence; the model only has to synthesize it. Bigger models would swap, and
swapping an LLM is worse than a smaller model.

**Configured in:** `infra/terraform/cloud-init.yaml` — install script, model pull, and a
systemd override setting `OLLAMA_HOST=0.0.0.0:11434` (reachable from pods via the node's
private IP; the OCI security list still blocks it from the internet) and
`OLLAMA_KEEP_ALIVE=30m` (how long weights stay in RAM after the last request).
`deploy/charts/researchscout/templates/ollama-endpoint.yaml` — gated on
`llm.external.enabled`, it creates a Service named `ollama`: with an IP in
`llm.external.nodeIP` (prod) a selectorless Service + manual Endpoints; with a hostname
(k3d: `host.k3d.internal`) an ExternalName Service. `values-prod.yaml` sets
`llm.baseUrl: http://ollama:11434/v1` and the nodeIP.

**Data flow.** `researchscout/llm/openai_compat.py` speaks the OpenAI `/v1` protocol to
whatever `RS_LLM_BASE_URL` names — pods get `http://ollama:11434/v1`, DNS resolves the
`ollama` Service, traffic leaves the cluster to the host process. Swapping to a cloud LLM
or an in-cluster Ollama is a values change; app code never knows.

**Health.**

```bash
systemctl status ollama && curl -s localhost:11434/api/tags   # on the VM
kubectl get endpoints ollama                                  # shows the node private IP
kubectl exec deploy/api -- python -c "import urllib.request; print(urllib.request.urlopen('http://ollama:11434/api/version').read())"
```

**Tweak later.** New model: `ollama pull <name>` on the VM, set `llm.model` in
values-prod, redeploy. RAM pressure: lower `OLLAMA_KEEP_ALIVE` via `systemctl edit ollama`.
Cloud fallback: point `llm.baseUrl`/`llm.model` at any OpenAI-compatible endpoint and put
the key in the SOPS secrets (`llm.apiKey`).

## Kafka — the event streaming setup

**What/why.** Kafka is a durable, replayable log of events, split into named topics.
Here it decouples "something should happen" from "something did it": the API and CronJobs
emit small events and return immediately; workers consume at their own pace, survive
restarts, and can be replayed. Single node, KRaft mode (no ZooKeeper), 256MB heap.

**Configured in:** `deploy/charts/researchscout/templates/kafka.yaml` (StatefulSet + 5Gi
PVC in prod); topics and event schemas in `researchscout/events/schemas.py`; producer/
consumer helpers in `researchscout/events/kafka.py`; the `EventSink` seam in
`researchscout/events/sink.py`; workers in `researchscout/workers/`; schedules in
`templates/cronjobs.yaml` driven by the `cron.*` values (both enabled in values-prod).

**Data flow.** Four topics:

| Topic | Producer | Consumer, and what it does |
| --- | --- | --- |
| `ingest.jobs` | `cron-ingest` CronJob (`scout jobs emit-ingest`), `scout jobs emit-watchlist` | ingest worker: pulls from arXiv/Semantic Scholar, upserts papers |
| `papers.new` | ingest worker (via `KafkaEventSink`) | embed worker: writes a pgvector embedding per paper |
| `papers.saved` | API, when a user saves a paper | airtable worker (opt-in): mirrors reading lists |
| `digests.published` | `cron-digest` CronJob (`scout digest`) | airtable worker (opt-in): archives digests |

**Delivery semantics.** At-least-once, made safe by idempotency — there is deliberately no
exactly-once plumbing. Consumers use manual commits (`enable.auto.commit: false` in
`events/kafka.py`): the offset advances only after the database transaction commits, so a
crash mid-batch means redelivery, and redelivery means an idempotent upsert (`ON CONFLICT`
in the store) rather than a duplicate. CronJobs use `concurrencyPolicy: Forbid` so a slow
run never overlaps the next one.

**Health.**

```bash
kubectl exec kafka-0 -- /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list
kubectl exec kafka-0 -- /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server localhost:9092 --describe --all-groups   # LAG column near 0
kubectl create job --from=cronjob/cron-ingest ingest-now && kubectl logs -f job/ingest-now  # force a run
```

**Tweak later.** Schedules and batch sizes are `cron.*` values. New event type: add a
schema + topic constant to `events/schemas.py`, produce via the sink, add a worker entry
to `workers.*` values (the `workers.yaml` template ranges over the map).

## OpenTelemetry → Prometheus / Loki → Grafana

**What/why.** OpenTelemetry (OTel) is the vendor-neutral instrumentation standard: apps
emit metrics, logs, and traces via one SDK to one **collector**, and only the collector
knows where telemetry lands. Prometheus stores metrics (numbers over time, pull model);
Loki stores logs (like Prometheus but for log lines, labels not full-text indexing);
Grafana is the shared UI over both. Tempo (traces) runs **only in local compose** — it was
cut from the VM to fit the memory budget; span context still exists in code, and LangSmith
(`LANGSMITH_TRACING` env) covers LLM-call tracing independently.

**Configured in:** app side — `researchscout/obs/otel.py` (everything gated on
`RS_OTEL_ENABLED`; OTLP/gRPC to `RS_OTLP_ENDPOINT`, FastAPI/httpx/SQLAlchemy/Kafka
auto-instrumentation) and `researchscout/obs/trace.py` (`trace_span`, the one-line span +
structured-log helper). Collector side — `config/otel/collector.yaml` for compose (full
pipelines incl. Tempo), `config/otel/collector-k8s.yaml` for the cluster (traces terminate
in the `nop` exporter so SDK exports still succeed). Install side —
`deploy/scripts/bootstrap.sh` helm-installs kube-prometheus-stack (release `kps`,
Alertmanager off, 3d retention), Loki (single binary, filesystem storage, 7d retention),
and the collector (release `otel-collector`), and provisions Grafana's Prometheus + Loki
datasources plus its ingress at `grafana.<domain>`. The chart's `otel.enabled` /
`otel.endpoint` values (`values-prod.yaml`, `values-local.yaml`) feed the app env vars.

**Data flow.** App pods export OTLP/gRPC to `otel-collector:4317`. Metrics: collector
re-exposes them on `:8889` in Prometheus format; Prometheus scrapes that target (wired via
`additionalScrapeConfigs` in bootstrap.sh) plus node/kubelet/kube-state-metrics from the
kps defaults. Logs: collector pushes OTLP to `loki:3100`; every root-logger line from the
app arrives labeled with its `service_name` (`researchscout-api`,
`researchscout-ingest-worker`, `researchscout-embed-worker`). Grafana queries both.

**Health.**

```bash
kubectl logs deploy/otel-collector --tail 20        # no exporter errors
curl -su admin:$PW https://grafana.<domain>/api/datasources | python3 -m json.tool | grep '"name"'
# after some traffic, via port-forward svc/kps-prometheus 9090:
curl -s 'localhost:9090/api/v1/query?query=http_server_duration_milliseconds_count' | head -c 300
```

**Query starters.** PromQL: request rate
`sum(rate(http_server_duration_milliseconds_count[5m]))`; p95 latency
`histogram_quantile(0.95, sum by (le) (rate(http_server_duration_milliseconds_bucket[5m])))`;
pod memory `container_memory_working_set_bytes{namespace="default"}`. LogQL: everything
from the API `{service_name="researchscout-api"}`; errors only
`{service_name=~"researchscout-.*"} |= "ERROR"`.

**Tweak later.** Retention: Prometheus in bootstrap.sh (`retention: 3d`), Loki
(`retention_period: 7d`). New destination (e.g. a Dynatrace trial): add an exporter to the
collector config and re-run bootstrap — no app changes. Prometheus loses history on pod
restarts (no PVC by design); add `storageSpec` in bootstrap.sh if that starts to matter.

## Redis

**What/why.** In-memory key-value store; three jobs here, all small: (1) the web BFF's
**session store** — browsers hold only an opaque `rs_session` cookie, the OIDC tokens
backing it live server-side in Redis; (2) the **token-refresh coordination** on top of
those sessions — refresh tokens rotate, so `apps/web/src/lib/auth.ts` single-flights
refreshes per session to stop parallel requests from racing Keycloak; (3) **chat rate
limiting** — `researchscout/api/ratelimit.py` counts requests per user in a window
(defaults: 20 per 600s, `RS_CHAT_RATE_LIMIT`/`RS_CHAT_RATE_WINDOW_SECONDS`), and *fails
open* if Redis is down — degraded chat beats dead chat.

**Configured in:** `deploy/charts/researchscout/templates/redis.yaml` (no persistence —
losing it logs everyone out, nothing more); consumers wired by env:
`SESSION_REDIS_URL` (web, `templates/web.yaml`), `RS_REDIS_URL` (api, `templates/api.yaml`).

**Health.**

```bash
kubectl exec deploy/redis -- redis-cli ping                 # PONG
kubectl exec deploy/redis -- redis-cli --scan --count 100   # rs_session/rate keys exist
```

**Tweak later.** Rate limits via the `RS_CHAT_RATE_*` env vars; add persistence
(`appendonly yes` + a PVC) only if session loss on redeploys starts to annoy.

## Keycloak

**What/why.** The identity provider: an OIDC server owning login, users, and tokens, so
the app never stores passwords. The web app is a **BFF** (backend-for-frontend): the Astro
server does the OIDC authorization-code + PKCE dance (`apps/web/src/pages/auth/*.ts`),
keeps tokens in Redis, and forwards API calls with a Bearer token
(`apps/web/src/pages/api/[...path].ts`, which also rejects cross-site mutations by Origin).
The API validates tokens against Keycloak's JWKS (`researchscout/api/auth.py`).

**Configured in:** `deploy/charts/researchscout/templates/keycloak.yaml` — dev-mode start
with realm import, `KC_HOSTNAME` pinned to `authUrl` so issuer strings match what browsers
see (backchannel stays dynamic for in-cluster calls); the realm lives in
`templates/keycloak-realm.yaml` (clients `web-bff` + `api`, redirect URIs derived from
`siteUrl`, demo/demo user gated on `oidc.demoUserEnabled` — off in prod). Compose uses the
sibling `config/keycloak/realm.json`.

**Health.**

```bash
curl -s https://auth.<domain>/realms/researchscout/.well-known/openid-configuration | head -c 200
kubectl logs deploy/keycloak --tail 20      # "Imported realm" on first boot, no errors
```

**Tweak later.** Real users: Keycloak admin console at `auth.<domain>` (credentials in the
SOPS secrets). Realm changes: edit the template, not the console — the Deployment
re-imports on the next rollout (a checksum annotation forces it). Production-grade
Keycloak later means `start` (not `start-dev`) plus an external DB.

## Postgres + pgvector

**What/why.** The system of record — papers, signals, saved lists, digests, users'
interests — plus **pgvector**, the extension that stores embeddings and does approximate
nearest-neighbor search inside SQL. One database serves both relational and vector
workloads, which on a small VM beats running a separate vector store.

**Configured in:** `deploy/charts/researchscout/templates/postgres.yaml` (StatefulSet,
`pgvector/pgvector:pg16` image, PVC); schema owned by Alembic migrations in
`alembic/versions/` (0001–0007: papers, embeddings, signals, saved, digests, interests,
lexical tsvector), applied automatically by the chart's migrate hook. Vector code:
`researchscout/store/models.py` (`Vector(384)` — matched to the `BAAI/bge-small-en-v1.5`
embedder) and `researchscout/store/vectors.py` (cosine ANN); retrieval fuses vector and
lexical scores in `researchscout/retrieve/`.

**Health.**

```bash
kubectl exec postgres-0 -- psql -U researchscout -c "select count(*) from papers"
kubectl exec postgres-0 -- psql -U researchscout -c "select count(*) from paper_embeddings"
kubectl exec postgres-0 -- psql -U researchscout -c "\dx"   # vector extension listed
```

**Tweak later.** A new embedding model with a different dimension needs a migration (the
column is fixed at 384) and a re-index (`scout index`). Backups: `backup.enabled=true` in
values-prod ships nightly `pg_dump` to OCI Object Storage (`templates/backup.yaml`).

## Terraform / Oracle Cloud

**What/why.** Terraform (the OpenTofu-compatible OCI provider) declares the cloud
resources in code, so the VM is reproducible and disposable. Oracle because its Always
Free tier includes an ARM A1.Flex shape with 2 OCPUs / 12GB — the only free tier that fits
this stack.

**Configured in:** `infra/terraform/` — `network.tf` (VCN 10.0.0.0/16, public subnet
10.0.1.0/24, security list admitting only 22/80/443), `compute.tf` (A1.Flex instance,
Ubuntu ARM image, boot volume, cloud-init handoff), `variables.tf` +
`terraform.tfvars.example` (tenancy/user/key OCIDs — never commit the filled file),
`outputs.tf` (public IP), `cloud-init.yaml` (everything installed at first boot: k3s,
helm, sops, helm-secrets, swap, iptables openings, Ollama + model), and `apply-retry.sh` —
A1 capacity is scarce, so it loops `terraform apply` until Oracle finds room; rotate
`availability_domain_index` if one AD stays full for hours.

**Health.**

```bash
cd infra/terraform && terraform plan   # "No changes" means reality matches code
ssh ubuntu@$(terraform output -raw public_ip) 'cloud-init status --long && free -h'
```

**Tweak later.** Anything about the VM (shape, volume, opened ports) belongs in these
files, then `terraform apply` — not in the console, or the next apply reverts it.

## SOPS (secrets)

**What/why.** SOPS encrypts secret *values* inside YAML while keys stay readable, so the
encrypted file lives in git with meaningful diffs. Encryption uses **age** (a minimal
modern PGP replacement): one keypair, public key encrypts locally, private key decrypts on
the VM. The **helm-secrets** plugin decrypts transparently during deploys.

**Configured in:** `.sops.yaml` (which paths encrypt to which age public key);
`deploy/secrets/secrets.prod.yaml.example` (the full key inventory: postgres, keycloak,
grafana admin password, oidc client secret, llm apiKey, airtable, backup S3 keys);
consumers: `deploy.yml` (`helm secrets upgrade ... -f secrets.prod.yaml`) and
`deploy/scripts/bootstrap.sh` (extracts `grafana.adminPassword`). The private key sits on
the VM at `~/.config/sops/age/keys.txt`; cloud-init installs the tooling.

**Health.**

```bash
sops decrypt deploy/secrets/secrets.prod.yaml | head -3   # decrypts where the key exists
git show HEAD:deploy/secrets/secrets.prod.yaml | head -5  # committed file shows ENC[AES256_GCM...
```

**Tweak later.** Rotate a secret: edit with `sops deploy/secrets/secrets.prod.yaml`,
commit, redeploy. New machine or teammate: add their age public key to `.sops.yaml` and
run `sops updatekeys`.

## CI/CD

**What/why.** GitHub Actions, two workflows. `ci.yml` is the gate on every PR and push:
Python lint/format/types/tests (`ruff`, `mypy`, `pytest -m "not integration"`), the web
build (`astro check` + build), `helm lint` + a `helm template` render against values-prod,
and a Trivy scan of the repo for vulnerable dependencies and leaked secrets. `deploy.yml`
is the delivery pipe, gated on the `DEPLOY_ENABLED` repository variable so it stays green
before a VM exists.

**Configured in:** `.github/workflows/ci.yml` and `.github/workflows/deploy.yml`; secrets
`VM_HOST` / `VM_SSH_KEY` and the variable `DEPLOY_ENABLED` in the repo settings.

**Data flow.** Push to main → build the three images (api/web/worker) natively on
`ubuntu-24.04-arm` runners → push to GHCR tagged with the commit SHA and `latest` → Trivy
scans the pushed images for CRITICALs → rsync `deploy/` to the VM → `helm secrets upgrade
--install rs ... --set image.*.tag=<sha> --wait`. The SHA tag makes every deploy traceable
to a commit and `helm rollback rs <revision>` exact.

**Health.**

```bash
gh run list --limit 5                       # green ci + deploy runs
ssh ubuntu@<vm> 'helm history rs | tail -3' # revisions match recent commits
```

**Tweak later.** The images-then-scan-then-deploy order is deliberate: nothing unscanned
reaches the cluster. Add steps to `ci.yml` freely — `deploy.yml` only runs after it.
