"""Request/response bodies for the HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from researchscout.retrieve.search import ScoredPaper
from researchscout.schema import Author, Paper


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
