"""Request/response bodies for the HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

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
    venue: str | None
    published_at: datetime
    source: str
    url: str | None
    pdf_url: str | None
    score: float | None = None

    @classmethod
    def from_paper(cls, paper: Paper, *, score: float | None = None) -> PaperSummary:
        return cls(
            id=paper.id,
            title=paper.title,
            abstract=paper.abstract,
            authors=paper.authors,
            categories=paper.categories,
            venue=paper.venue,
            published_at=paper.published_at,
            source=paper.source,
            url=paper.url,
            pdf_url=paper.pdf_url,
            score=score,
        )


class PaperList(BaseModel):
    items: list[PaperSummary]


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=8, ge=1, le=50)
    days: int | None = Field(default=None, ge=1, le=365)


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


class TopicDetail(TopicSummary):
    papers: list[TopicPaper]


class TopicList(BaseModel):
    items: list[TopicDetail]


class InterestList(BaseModel):
    interests: list[str]


class InterestUpdate(BaseModel):
    interests: list[Annotated[str, Field(min_length=1, max_length=40)]] = Field(max_length=20)
