# researchscout
ResearchScout ingests new AI/ML papers and their surrounding signals (citations, trending attention, code adoption), then scores, clusters, and summarizes them to separate breakthrough from noise - so you know what's worth reading right now.

**Scope.** The corpus is computing, statistics and mathematics: a paper belongs if it carries a
`cs`, `stat`, `eess`, `math` or `math-ph` category anywhere in its list. Cross-lists count, which
is the point - a quantitative biology or physics paper that also files under `cs.LG` is exactly
the intersection work this radar exists to surface, and one that does not is somebody else's
feed. The arXiv query is that same rule, so nothing is fetched only to be discarded.

## Quickstart

Everything runs as plain host processes — no Docker. Needs Homebrew Postgres with pgvector
plus Kafka (`brew install postgresql@17 pgvector kafka`), [uv](https://docs.astral.sh/uv/), and
pnpm. Chat needs an LLM: the defaults target local [Ollama](https://ollama.com) (`brew install
ollama`, run `ollama serve`, then `ollama pull qwen2.5:3b-instruct` — one-time ~2.3GB), or point
`RS_LLM_*` in `.env` at any OpenAI-compatible provider.

```bash
make setup    # deps, .env, a repo-local Postgres cluster and Kafka storage in .local/
make start    # postgres, kafka, the streaming pipeline, api, web — prints the URLs when ready
make seed     # ~25 real arXiv papers, embedded and searchable
```

Then open http://localhost:4321: browse and search the feed — no sign-in, the app runs as a
built-in local user — and ask Scout about the papers in the same field; answers stream with
citations. The feed opens on the last seven days, which the filters can widen.

Two axes filter it. **Field** is what a paper is about: AI and machine learning, statistics,
data science and mathematics, plus the five places this radar meets other disciplines (biology,
the physical sciences, security, society and economics, systems and software). **Technique** is
what it uses: NLP, computer vision, reinforcement learning. Values within an axis widen, the
axes narrow each other, so `?subject=ai&topic=rl` means machine learning papers about
reinforcement learning. The Filters button adds date, popularity, authors and venues, and every
result is a plain URL you can share or bookmark.

Read on a card or paper page opens the PDF in-app; math in titles and abstracts renders
properly. Star papers (`/saved`), run `make digest` and visit `/digests` for the weekly summary.
`/topics` clusters recent papers into emerging themes ranked by momentum, `/for-you`
personalizes the feed from your interests, and `/models` and `/benchmarks` carry the AI
landscape beside the papers - which models exist, how large they are, what they score, and which
of them came out of a paper in this corpus. Dismiss takes a paper out of the feed and keeps it
out on the next visit, with an Undo in the confirmation; it stays searchable and its own page
still opens, because a dismissal says "not among what is new" rather than "hide this". `make
stop` shuts everything down; `make clean` also wipes the data; plain `make` lists every target.

## Streaming pipeline

Ingestion runs as a streaming pipeline over a local single-node Kafka broker (KRaft mode,
brew-installed, lifecycle-managed by make with data under `.local/`). `scout stream serve`
(started by `make start`) runs polling producers — arXiv hourly, signal sources on their
cadence, full text in politely paced batches — publishing raw packets to `rs.raw.v1`, and a
Bytewax worker consuming them through three stages: parse (deterministic normalization and
cleanup), categorize (taxonomy group, topic-centroid match, statistical keywords with a
small-LLM fallback, optional custom labels), and inject (idempotent upserts, embeddings,
chunks, signals). Every packet carries per-stage lineage into Postgres: `GET /v1/stream/stats`
serves hourly rollups over the `pipeline_rollups_hourly` view, and `scout stream tail` watches
packets live on the `rs.parsed.v1` /
`rs.enriched.v1` taps. Delivery is at-least-once with natural-key upserts everywhere, so
replays converge. `make scheduler` still drives the derived products (weekly digest, topics,
the daily report with its must-read five). The batch commands below remain the manual fallback
whenever the broker is down.

### Scheduling

Every scheduled task runs on an interval by default, which is what a local checkout wants: a
fresh process does its work at once and then every N seconds. A deployment tracking a
publisher's day wants a clock instead, so two settings move the named tasks onto one - and the
deployment stack sets them, along with `RS_SCHEDULER_BATCH_PIPELINE`, as its compose defaults
rather than relying on `deploy/.env` carrying them:

```bash
RS_SCHEDULER_PIPELINE_AT=05:00,10:00,14:00,17:00,20:30   # ingest, index, full text, signals
RS_SCHEDULER_DAILY_AT=17:00                              # catalogue, digest, topics
RS_SCHEDULER_REPORT_AT=21:00                             # daily report (empty: with the daily set)
RS_SCHEDULER_TIMEZONE=America/New_York                   # a named zone, not a fixed offset
```

The 20:30 slot is the one that keeps evenings honest: arXiv announces the day's papers at
20:00 ET, so a schedule whose last fetch is 17:00 always shows yesterday until the next
morning. The daily report runs after that fetch and windows on when papers **arrived** in the
corpus, not on arXiv's published_at (submission time, a day or more behind the announcement) -
windowed on published_at it was empty on almost every real day. Weekend reports still skip
publishing: arXiv announces Sunday through Thursday evenings, so an empty Saturday is truth,
not a fault.

A named zone rather than `UTC-5` because the runs should stay where they are on the local clock
when daylight saving moves; the two days a year that differ are covered by tests. A time the
clock skips over on the spring-forward day runs at the first moment that exists rather than
being silently dropped. There is deliberately no catch-up for a slot missed while the process
was down - the ingest window is several days wide, so the next run covers it, and firing on
start-up instead would mean a restart loop hammering arXiv. `scout serve scheduler --once`
ignores the clock entirely and runs everything, which is what a host cron entry wants.

Wall-clock deadlines are compared against the wall clock, not stored as monotonic offsets.
The distinction earns its sentence on a Mac: while the host sleeps, the container's monotonic
clock stops with it, so an offset deadline slips by however long the machine was closed - this
deployment once held its 05:00 run until late evening that way. Judged by the wall clock, a
slot slept over fires once on wake and covers the backlog. Runs only happen at their exact
times if the machine is awake to see them, though: a Mac hosting the deployment should not
sleep on AC power (`sudo pmset -c sleep 0`) - while it sleeps the API is unreachable anyway.

Ingest runs are built to survive the upstream: each page commits on its own and a rate limit
mid-walk keeps everything already stored (arXiv pages newest-first, so the papers that matter
land first); 429s are retried briefly, honoring `Retry-After`. Set
`RS_SCHEDULER_INGEST_EARLY_STOP_PAGES=2` (the deployment default) and a run also stops after
two consecutive pages of nothing new instead of re-walking the whole window - the difference
between three requests and thirty, four times a day. Citation signals go through Semantic
Scholar's batch endpoint, 500 papers per call; without an `S2_API_KEY` the shared pool still
throttles sometimes, and a key (free, from their site) is the real fix.

Every completed task lands in the `scheduler_runs` ledger - including a `scheduler` row each
time the loop comes up - and `GET /v1/system/status` serves it along with the app version, the
build SHA, the migration stamp, the newest paper's age, and the pipeline slot most recently
due. That last pair is what lets `make deploy-verify` flag a stalled loop by name: a slot that
passed after the newest scheduler start with no run recorded is a problem, not a young ledger.
One warning worth repeating: never run two fetchers against arXiv from one address.
`make scheduler` says so out loud if the deployed scheduler container is already running.

## Monitoring

Six dashboards live in `config/grafana/dashboards/` as JSON, hosted on Grafana Cloud rather
than by a local Grafana - the free tier costs nothing, and the machine at home has better uses
for the memory. **Ingest health** answers whether anything is still arriving and how enriched it
is once it does; **Corpus** how much is here; **Answers** what people ask Scout and how long it
takes; **Engagement** what readers open and dismiss; **Signals and sources** where the momentum
numbers come from and whether each upstream is still replying; **Catalogue** the models and
benchmarks beside the papers.

They read the tables in Postgres directly - no exporters and no metrics agent, and the numbers
accrue whether anything is watching or not. Grafana Cloud reaches that database through Private
Data Source Connect: an agent in the deployment stack opens an outbound tunnel, so the database
needs no inbound rule and no public address. Each dashboard declares a `DS_POSTGRES` input
rather than naming a data source, so importing prompts for one and rewires every panel.

`config/grafana/alerting/corpus-stale.yaml` is the one alert worth having: no new paper for
thirty hours. Nothing else notices that ingestion has stopped, because the API stays up and
every panel keeps showing yesterday's number.

Setting all of it up, including the read-only login the dashboards use, is section 4 of
`deploy/PUBLISHING.md`.

To read them against a development database instead, run a Grafana yourself and point it at
`.local/pgdata`; the files are portable.

## Deep backfill

`config/sources.yaml` registers the four archives the scope rule covers. The seed pulls one small
slice; to fill the radar, backfill per archive with the resumable cursor (arXiv caps paging depth
per query, so keep runs archive-sized), then embed and pull citation signals:

```bash
uv run scout ingest --since 2026-01-01 --category 'cs.*' --resume    # repeat per archive; rerun on interruption
uv run scout index                                                   # embeddings; hours on CPU, run overnight
uv run scout ingest --source semantic_scholar                        # citations -> citation counts
```

One-time step for rows ingested before the metadata capture landed: re-ingest their original
window once so venue, comment, and the primary category populate (same-id re-ingest refreshes
in place). Ingest paces itself with `RS_ARXIV_PAGE_DELAY_SEC` (default 3 seconds between pages).

A corpus gathered before the scope narrowed will hold papers that no longer belong.
`scout db prune-scope --dry-run` reports them with a sample; without the flag it deletes them,
along with their embeddings, chunks and signals. That is the one irreversible command here, so
take a backup first.

## The AI landscape

`/models` and `/benchmarks` are refreshed once a day, or on demand:

```bash
uv run scout catalog    # Epoch AI + Hugging Face, keyless, fails soft
```

Two upstreams, both declared in `config/sources.yaml` with the attribution their licences
require. [Epoch AI](https://epoch.ai) (CC BY) supplies about a thousand notable models with
organisation, parameters, training compute and weight availability, plus a few thousand benchmark
scores; the [Hugging Face Hub](https://huggingface.co) supplies open-weight download counts and
the `arxiv:` tags on model cards. The two are merged on a slug of the model name, so one row
carries both, and a model whose paper is in this corpus links straight to it - which is the whole
reason the catalogue lives here rather than being a link to somebody's leaderboard.

Where a model card carries several arXiv tags, only the newest is treated as its own paper. The
others are what it stands on, and taking the first instead once filed half of Hugging Face under
"Attention Is All You Need".

Each upstream is the authority for the fields it actually knows: Epoch AI for what a model is
(organisation, date, size, compute, licence, the work it came from) and the Hub for how much it
is used. Neither can overwrite the other's half, so the order a refresh runs in decides nothing.

`/models` sorts by release date, parameters, training compute, downloads, organisation or name,
and searches by name; every model has its own page carrying its scores and its paper. Above the
leaderboard on `/benchmarks` is a comparison of the major labs - each one's most recent measured
model across the benchmarks the field leans on. Which labs appear and which benchmarks are the
columns is editorial, so it lives in `config/providers.yaml`, aliases included: the same lab
arrives as "Google DeepMind" from one upstream and "Google" from the other.

## Development

Requires [uv](https://docs.astral.sh/uv/). The project pins Python 3.12 via `.python-version`
(uv will fetch it automatically).

```bash
uv sync                 # create the venv and install runtime + dev dependencies
uv sync --extra stream  # plus bytewax and the kafka client, which `make setup` installs
uv run scout --help     # see the CLI surface

# checks
uv run ruff check researchscout tests
uv run ruff format --check researchscout tests
uv run mypy researchscout
uv run pytest -q -m "not integration"      # unit tests — no Docker needed
```

Configuration is via environment variables (prefix `RS_`) or a local `.env` — copy `.env.example`
to `.env` to start. The canonical data model lives in `researchscout/schema.py`; the source
registry stub is `config/sources.yaml`.

## Deploying

The public site is the frontend on Vercel plus this backend in Docker, published through
Tailscale Funnel - no inbound port, no domain to buy. `deploy/README.md` is the runbook for the
container stack (including moving the development database into it and the nightly backup);
`deploy/PUBLISHING.md` is the account-by-account setup for Funnel, Auth0, Vercel and Grafana
Cloud.

```bash
cp deploy/.env.example deploy/.env   # fill in, then
make deploy-build && make deploy-up  # postgres, migrations, api, scheduler on :8001
make deploy-verify                   # current SHA, migrations, run ledger, catalogue answering
make backup                          # nightly dump, keeps a week, verifies the file
```

`deploy-build` stamps the image with the commit it was built from and `/v1/system/status`
serves it back, so `deploy-verify` can tell a stale deployment from a broken one - the failure
mode that once ran for two days looking healthy. Rebuild and re-up after every merge you want
live; a restart alone keeps the old image and the old environment.

The development stack is unchanged and independent: `make start` still runs everything as host
processes against the repo-local Postgres, and with no identity provider configured the API and
the site behave exactly as they always have, as a single built-in user.

## Data sources and attribution

Every connector in `config/sources.yaml` carries an `attribution` block naming the upstream, its
terms, the license its data arrives under, and what this app takes from it. `GET /v1/sources`
serves those blocks (never the `api_key`/`token`/`mailto` beside them) and the web `/about` page
renders them alongside the copyright, API-usage and privacy notices. Adding a source means
adding its attribution: `tests/test_sources.py` fails otherwise.

arXiv metadata is CC0; papers stay under their authors' licenses and are never redistributed
here. Semantic Scholar's API license requires the visible credit that `/about` carries.

## HTTP API

The `api` extra adds a FastAPI service over the same core the CLI uses (the dev group already
includes it, so `uv sync` is enough for development):

```bash
uv run scout serve api    # http://127.0.0.1:8000, OpenAPI docs at /docs
```

Endpoints: `GET /healthz`, `GET /v1/papers` (recency feed; `?q=` switches to semantic ranking),
`GET /v1/papers/{id}`, `GET /v1/topics` (emerging topics), `GET /v1/keywords` (corpus keyword
dictionary with paper counts), `GET /v1/sources` (the registry with its attribution),
`GET /v1/me/feed` (personalized), `GET /v1/models` and `GET /v1/models/{id}`,
`GET /v1/benchmarks` and `GET /v1/providers` (the AI landscape; `?paper_id=` lists what came out
of one paper), and `POST /v1/ask` (grounded, cited answer).
Signed-in callers also get `/v1/me/history`, `/v1/me/recent`, `/v1/me/dismissals` and
`/v1/me/filters` - a per-account cache of site state, kept in unlogged tables. With `RS_OIDC_ISSUER` unset (the default) the API
runs in local no-auth mode as a built-in user; set an issuer to require OIDC Bearer tokens.
The LLM defaults to local Ollama; point `RS_LLM_BASE_URL` / `RS_LLM_MODEL` / `RS_LLM_API_KEY`
at any OpenAI-compatible provider to swap it.

`GET /v1/papers` filter and sort parameters (all combinable; they also apply under `?q=`):

| Param | Meaning |
|---|---|
| `days` | window in days (mutually exclusive with `year`) |
| `year`, `month` | calendar window; `month` requires `year` |
| `category` | arXiv category, repeatable (`category=cs.LG&category=math.CO`) |
| `subject` | field, repeatable: `ai`, `stats`, `data`, `math` (core), `bio`, `physical`, `security`, `society`, `systems` (intersections). Matched against the whole category list, so cross-lists count. An unknown key is a 422 naming it, not an empty result |
| `topic` | technique, repeatable: `nlp` (cs.CL), `cv` (cs.CV, eess.IV), `rl` (a phrase match on title and abstract, because arXiv has no RL category) |
| `author`, `venue` | case-insensitive contains match |
| `min_citations` | latest citation count at least N |
| `sort` | `newest` (default), `citations`, or `activity` |
| `limit`, `offset` | pagination; the response carries `total` (null under `q`) |

```bash
curl 'http://127.0.0.1:8000/v1/papers?subject=ai&topic=rl&days=7&limit=5'
```
