"""Request/response bodies for the HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

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
    terms_required: str
    terms_accepted_version: str | None = None
    terms_accepted: bool


class MeUpdate(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)


class TermsAcceptance(BaseModel):
    version: str


class AccountDeleted(BaseModel):
    deleted: bool


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
