import httpx
import pytest

from researchscout.fulltext import extract_text, fetch_full_text

FIXTURE = """
<html><head><script>tracking()</script><style>.x{}</style></head>
<body>
<article>
  <h1>Certified Sinkhorn</h1>
  <div class="ltx_authors"><p>Ada Lovelace</p></div>
  <h2>1 Introduction</h2>
  <p>Optimal transport is <em>everywhere</em>.</p>
  <p>We prove <math><mi>x</mi></math> bounds.</p>
  <ul><li><p>A list point.</p></li></ul>
  <h2>2 Method</h2>
  <p>The parallel scheme works.</p>
  <figure><figcaption>Figure 1: the pipeline.</figcaption></figure>
</article>
<footer><p>arXiv admin footer</p></footer>
</body></html>
"""


class _Resp:
    def __init__(self, status: int, text: str) -> None:
        self.status_code = status
        self.text = text


def test_extract_marks_sections_and_keeps_body_prose() -> None:
    text = extract_text(FIXTURE)
    lines = text.split("\n\n")
    assert "## Certified Sinkhorn" in lines
    assert "## 1 Introduction" in lines
    assert "## 2 Method" in lines
    assert "Optimal transport is everywhere." in lines
    assert "A list point." in lines  # once, via its paragraph
    assert lines.count("A list point.") == 1
    assert "Figure 1: the pipeline." in lines
    assert "tracking()" not in text  # scripts dropped
    assert "arXiv admin footer" not in text  # footer dropped
    assert "x" not in [line for line in lines if len(line) == 1]  # math dropped


def test_extract_empty_document() -> None:
    assert extract_text("<html><body></body></html>") == ""


def _article(chars: int) -> str:
    body = f"<p>{'word ' * (chars // 5)}</p>"
    return f"<html><body><article><h2>S</h2>{body}</article></body></html>"


def test_fetch_falls_back_to_ar5iv(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_get(url: str, **kwargs: object) -> _Resp:
        calls.append(url)
        if "ar5iv" in url:
            return _Resp(200, _article(2000))
        return _Resp(404, "")

    monkeypatch.setattr(httpx, "get", fake_get)
    text = fetch_full_text("2401.00001")
    assert text is not None and "word" in text
    assert calls[0].startswith("https://arxiv.org/html/")
    assert "ar5iv" in calls[1]


def test_fetch_rejects_error_shells(monkeypatch: pytest.MonkeyPatch) -> None:
    # 200 responses whose article is tiny are stubs, not papers.
    monkeypatch.setattr(httpx, "get", lambda url, **k: _Resp(200, _article(80)))
    assert fetch_full_text("2401.00001") is None


def test_fetch_none_when_both_hosts_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(url: str, **kwargs: object) -> _Resp:
        raise httpx.HTTPError("down")

    monkeypatch.setattr(httpx, "get", boom)
    assert fetch_full_text("2401.00001") is None
