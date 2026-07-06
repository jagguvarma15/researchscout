from datetime import UTC, datetime

from researchscout.ingest.watchlist import jobs_from_rows

SINCE = datetime(2026, 7, 5, tzinfo=UTC)


def _row(**fields: object) -> dict[str, object]:
    return {"id": "rec1", "fields": fields}


def test_disabled_rows_are_skipped() -> None:
    rows = [
        _row(Source="arxiv", Category="cs.LG", Enabled=True),
        _row(Source="arxiv", Category="cs.CV", Enabled=False),
        _row(Source="arxiv", Category="cs.CL"),  # no Enabled checkbox at all
    ]
    jobs = jobs_from_rows(rows, since=SINCE)
    assert len(jobs) == 1
    assert jobs[0].categories == ["cs.LG"]


def test_row_fields_map_to_job() -> None:
    rows = [_row(Source="semantic_scholar", Max=50, Enabled=True)]
    (job,) = jobs_from_rows(rows, since=SINCE)
    assert job.source == "semantic_scholar"
    assert job.max_items == 50
    assert job.categories is None
    assert job.since == SINCE


def test_source_defaults_to_arxiv() -> None:
    (job,) = jobs_from_rows([_row(Enabled=True)], since=SINCE)
    assert job.source == "arxiv"
