"""Request/response bodies for the HTTP API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from researchscout.answer import FastEntry
from researchscout.retrieve.search import ScoredPaper
from researchscout.schema import Author, Paper, PaperLabel


class PaperSummary(BaseModel):
    """A paper as returned by the list/detail endpoints (never the full text)."""

    id: str
    title: str
    abstract: str
    authors: list[Author]
    categories: list[str]
    primary_category: str | None
    venue: str | None
    comment: str | None
    citation_count: int = 0
    published_at: datetime
    source: str
    url: str | None
    pdf_url: str | None
    # Stream enrichment (None until the categorize stage has seen the paper). Sections
    # stay internal: they only feed LLM answer context and no UI needs them.
    keywords: list[str] | None = None
    labels: list[PaperLabel] | None = None
    score: float | None = None
    # Why the personalized feed picked this paper (None everywhere else).
    reason: str | None = None

    @classmethod
    def from_paper(
        cls, paper: Paper, *, score: float | None = None, reason: str | None = None
    ) -> PaperSummary:
        return cls(
            id=paper.id,
            title=paper.title,
            abstract=paper.abstract,
            authors=paper.authors,
            categories=paper.categories,
            primary_category=paper.primary_category,
            venue=paper.venue,
            comment=paper.comment,
            citation_count=paper.citation_count,
            published_at=paper.published_at,
            source=paper.source,
            url=paper.url,
            pdf_url=paper.pdf_url,
            keywords=paper.keywords,
            labels=paper.labels,
            score=score,
            reason=reason,
        )


class PaperList(BaseModel):
    items: list[PaperSummary]
    # total is the unpaginated facet count; None under q, where search returns at most k.
    total: int | None = None
    limit: int = 20
    offset: int = 0


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=8, ge=1, le=50)
    days: int | None = Field(default=None, ge=1, le=365)
    agentic: bool = False
    # "fast" answers extractively with no LLM call and skips the chat guardrail. The
    # schema default stays "llm" so existing clients and the CLI are byte-identical; the
    # chat drawer opts into fast explicitly.
    mode: Literal["fast", "llm"] = "llm"


class UsedPaper(BaseModel):
    id: str
    title: str
    score: float

    @classmethod
    def from_scored(cls, item: ScoredPaper) -> UsedPaper:
        return cls(id=item.paper.id, title=item.paper.title, score=item.score)


class AskResponse(BaseModel):
    text: str
    cited: list[str]
    hallucinated: list[str]
    used: list[UsedPaper]
    # False only for fast-mode answers under the relevance floor (nothing matched).
    found: bool = True


class FastResultItem(BaseModel):
    """One structured fast-answer hit, streamed to the drawer in the ``results`` event."""

    id: str
    title: str
    published_at: datetime
    venue: str | None
    matches: list[str]
    keywords: list[str]
    excerpt: str | None
    relevance: float | None

    @classmethod
    def from_entry(cls, entry: FastEntry) -> FastResultItem:
        return cls(
            id=entry.id,
            title=entry.title,
            published_at=entry.published_at,
            venue=entry.venue,
            matches=entry.matches,
            keywords=entry.keywords,
            excerpt=entry.excerpt,
            relevance=entry.relevance,
        )


class WebSearchHit(BaseModel):
    provider: Literal["arxiv", "s2"]
    title: str
    authors: list[str]
    year: int | None = None
    snippet: str
    arxiv_id: str | None = None
    url: str | None = None
    already_known: bool = False
    paper_id: str | None = None


class WebSearchResponse(BaseModel):
    query: str
    hits: list[WebSearchHit]
    providers_failed: list[str]


class ImportRequest(BaseModel):
    arxiv_id: str = Field(min_length=1, max_length=64)


class ImportResponse(BaseModel):
    id: str
    title: str
    already_known: bool
    enrichment_queued: bool


class KeywordCount(BaseModel):
    keyword: str
    papers: int


class KeywordList(BaseModel):
    # items are ranked papers desc then keyword asc; total counts distinct keywords
    # before the limit cut.
    items: list[KeywordCount]
    total: int


class MeResponse(BaseModel):
    """The signed-in account, and whether it still owes a terms acceptance."""

    sub: str
    username: str
    email: str | None = None
    display_name: str | None = None
    avatar: str | None = None
    terms_required: str
    terms_accepted_version: str | None = None
    terms_accepted: bool


class MeUpdate(BaseModel):
    """A partial update: only the fields present are applied.

    The avatar is a slug naming one of the web app's drawn set; the server checks the shape
    only, because the set lives in the web app and unknown values fall back to initials
    there. An empty string clears it.
    """

    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    avatar: str | None = Field(default=None, pattern=r"^[a-z0-9-]{0,32}$")


class TermsAcceptance(BaseModel):
    version: str


class AccountDeleted(BaseModel):
    deleted: bool


# --- Per-account site state (a cache; see researchscout/store/account.py) ---


class SearchRecord(BaseModel):
    query: str = Field(min_length=1, max_length=200)


class SearchHistory(BaseModel):
    items: list[str]


class ViewRecord(BaseModel):
    paper_id: str = Field(min_length=1, max_length=200)


class RecentPaperList(BaseModel):
    """Papers this account opened, newest first - hydrated, because a bare id shows nothing."""

    items: list[PaperSummary]


class DismissRequest(BaseModel):
    paper_id: str = Field(min_length=1, max_length=200)


class DismissalList(BaseModel):
    items: list[str]


class FilterState(BaseModel):
    """The feed's query string verbatim, which is already the whole filter state."""

    query_string: str | None = Field(default=None, max_length=2000)


# --- The AI landscape (see researchscout/catalog.py) ---


class BenchmarkResultSummary(BaseModel):
    """One model's score on one benchmark. ``model_id`` is null when it is not in the catalogue."""

    benchmark: str
    model: str
    model_id: str | None = None
    score: float
    measured_on: date | None = None
    origin: str | None = None
    #: The benchmark's scale, carried alongside the score because it belongs to the benchmark:
    #: without it a page has a number and no way to know whether it is a percentage.
    scale: str = "fraction"


class ModelSummary(BaseModel):
    """One model in the landscape, merged from every source that describes it."""

    id: str
    name: str
    organization: str | None
    publication_date: date | None
    domains: list[str]
    task: str | None
    parameters: float | None
    training_compute_flop: float | None
    accessibility: str | None
    open_weights: bool | None
    link: str | None
    #: The paper this model came from, when this corpus holds it. The join the pages turn on.
    paper_id: str | None
    hf_repo: str | None
    hf_downloads: int | None
    hf_likes: int | None
    sources: list[str]
    scores: list[BenchmarkResultSummary] = Field(default_factory=list)

    @classmethod
    def from_row(cls, row: Any) -> ModelSummary:
        return cls(
            id=row.id,
            name=row.name,
            organization=row.organization,
            publication_date=row.publication_date,
            domains=[part for part in (row.domains or "").split(",") if part],
            task=row.task,
            parameters=row.parameters,
            training_compute_flop=row.training_compute_flop,
            accessibility=row.accessibility,
            open_weights=row.open_weights,
            link=row.link,
            paper_id=row.paper_id,
            hf_repo=row.hf_repo,
            hf_downloads=row.hf_downloads,
            hf_likes=row.hf_likes,
            sources=[part for part in (row.sources or "").split(",") if part],
        )


class ModelList(BaseModel):
    items: list[ModelSummary]
    total: int = 0
    limit: int = 50
    offset: int = 0


class BenchmarkSummary(BaseModel):
    id: str
    name: str
    released_on: date | None
    result_count: int
    #: "fraction" when the scores read as percentages, "raw" when they are a ratio, an Elo or
    #: an amount of money. Eleven of the hub's benchmarks are the latter.
    score_scale: str = "fraction"

    @classmethod
    def from_row(cls, row: Any) -> BenchmarkSummary:
        return cls(
            id=row.id,
            name=row.name,
            released_on=row.released_on,
            result_count=row.result_count,
            score_scale=getattr(row, "score_scale", "fraction"),
        )


class BenchmarkList(BaseModel):
    items: list[BenchmarkSummary]


class BenchmarkDetail(BenchmarkSummary):
    results: list[BenchmarkResultSummary] = Field(default_factory=list)


class BenchmarkColumn(BaseModel):
    """One column of the provider comparison: the benchmark id, how to head it, and its scale.

    ``scale`` is "fraction" when the numbers read as percentages and "raw" when they do not -
    a ratio, an Elo, an amount of money. Sent because only the server knows it, and a page that
    guesses from the handful of values it can see would format the same benchmark two ways.
    """

    id: str
    name: str
    scale: str = "fraction"


class ProviderSummary(BaseModel):
    """One provider's current flagship model and what it scores.

    ``scores`` is keyed by benchmark id rather than being a list, because the table draws a
    fixed set of columns and a missing score is a blank cell rather than a shorter row.
    """

    provider: str
    country: str | None = None
    model_id: str
    model_name: str
    published_on: date | None = None
    paper_id: str | None = None
    open_weights: bool | None = None
    scores: dict[str, float] = Field(default_factory=dict)


class ProviderList(BaseModel):
    """The provider comparison, and the benchmark columns that actually have data."""

    columns: list[BenchmarkColumn] = Field(default_factory=list)
    items: list[ProviderSummary] = Field(default_factory=list)


class SourceInfo(BaseModel):
    """One registered source as the /about page shows it.

    Flat by design: the attribution fields are null together when a source has not declared
    them, and the page renders that gap rather than hiding the source.
    """

    name: str
    kind: str
    enabled: bool
    display_name: str | None = None
    homepage: str | None = None
    terms_url: str | None = None
    data_license: str | None = None
    provides: str | None = None


class SourceList(BaseModel):
    items: list[SourceInfo]


class DigestItem(BaseModel):
    paper_id: str
    title: str
    score: float
    citations: float


class DigestSummary(BaseModel):
    slug: str
    title: str
    period_start: datetime
    period_end: datetime


class DigestDetail(DigestSummary):
    body: str
    items: list[DigestItem]


class DigestList(BaseModel):
    items: list[DigestSummary]


class TopicPaper(BaseModel):
    paper_id: str
    title: str
    score: float


class TopicSummary(BaseModel):
    id: int
    label: str
    summary: str | None
    score: float
    size: int
    # new | rising | steady | fading (None before the first continuity-aware build).
    trend: str | None = None


class TopicDetail(TopicSummary):
    papers: list[TopicPaper]


class TopicList(BaseModel):
    items: list[TopicDetail]


class EventIn(BaseModel):
    event: Literal["impression", "click", "dwell", "dismiss", "open_pdf"]
    paper_id: str = Field(min_length=1, max_length=200)
    rank: int | None = Field(default=None, ge=0, le=10000)
    value: float | None = Field(default=None, ge=0)
    surface: str | None = Field(default=None, max_length=40)


class EventBatch(BaseModel):
    events: list[EventIn] = Field(min_length=1, max_length=200)


class EventAck(BaseModel):
    stored: int


class InterestList(BaseModel):
    interests: list[str]


class InterestUpdate(BaseModel):
    interests: list[Annotated[str, Field(min_length=1, max_length=40)]] = Field(max_length=20)


class StreamStatBucket(BaseModel):
    bucket: datetime
    stage: str
    kind: str
    source: str
    outcome: str
    category: str | None
    packets: int
    avg_seconds: float | None


class StreamStats(BaseModel):
    hours: int
    buckets: list[StreamStatBucket]


class SchedulerRun(BaseModel):
    """One scheduled task run from the ledger; still running while finished_at is null."""

    task: str
    started_at: datetime
    finished_at: datetime | None
    ok: bool
    note: str


class HealthCheckInfo(BaseModel):
    """One self-check verdict: ok, warn, fail, or skipped, with the reason."""

    name: str
    status: str
    detail: str


class ScheduleGroup(BaseModel):
    """One task group's wall-clock schedule and its next occurrence."""

    group: str
    at: list[str]
    timezone: str
    next_run: datetime | None


class SystemStatus(BaseModel):
    """What is deployed and whether it is fetching - the deploy-verify payload."""

    version: str
    build_sha: str | None
    migration: str | None
    papers: int
    # Freshness has two truths: published_at is the paper's submission time (what the feed
    # shows) and created_at is when it landed here (what "is the pipeline running" means).
    newest_paper_at: datetime | None
    newest_paper_created_at: datetime | None = None
    runs: list[SchedulerRun]
    # The pipeline slot most recently due (None on an interval schedule) and the newest
    # scheduler start-up. Together they let a verifier tell "the ledger is young" apart from
    # "a slot passed with the scheduler up and nothing ran".
    pipeline_due_at: datetime | None = None
    scheduler_started_at: datetime | None = None
    # Database-only self-checks, plus the last scheduler health run's ledger row.
    health: list[HealthCheckInfo] = []
    last_health_run: SchedulerRun | None = None
    schedule: list[ScheduleGroup] = []


class NotableModelInfo(BaseModel):
    """One row of the recent-models strip on /models."""

    id: str
    name: str
    provider: str
    country: str | None
    published_on: date | None
    parameters: float | None
    open_weights: bool | None


class NotableModelList(BaseModel):
    items: list[NotableModelInfo]


class HeadlineBenchmarkInfo(BaseModel):
    """One curated benchmark with the best curated-lab score and its holder."""

    id: str
    name: str
    scale: str
    result_count: int
    best_score: float
    model_id: str
    model_name: str
    provider: str


class HeadlineBenchmarkList(BaseModel):
    items: list[HeadlineBenchmarkInfo]
