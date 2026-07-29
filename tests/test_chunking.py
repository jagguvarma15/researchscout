from researchscout.chunking import chunk_text


def _para(word: str, count: int) -> str:
    return " ".join(f"{word}{i}" for i in range(count))


def test_chunks_never_cross_sections() -> None:
    text = "\n\n".join(
        [
            "## Introduction",
            _para("intro", 50),
            "## Methods",
            _para("method", 50),
        ]
    )
    chunks = chunk_text(text, target_words=300)
    assert [chunk.section for chunk in chunks] == ["Introduction", "Methods"]
    assert "method0" not in chunks[0].text
    assert "intro0" not in chunks[1].text


def test_long_sections_split_with_overlap() -> None:
    text = "\n\n".join(["## Methods", _para("a", 200), _para("b", 200), _para("c", 200)])
    chunks = chunk_text(text, target_words=250, overlap_words=20)
    assert len(chunks) == 3
    assert all(chunk.section == "Methods" for chunk in chunks)
    # The second chunk starts with the tail of the first (the carried overlap).
    first_tail = chunks[0].text.split()[-20:]
    assert chunks[1].text.split()[:20] == first_tail
    assert [chunk.index for chunk in chunks] == [0, 1, 2]


def test_tiny_tails_merge_into_the_previous_chunk() -> None:
    text = "\n\n".join(["## Results", _para("x", 290), _para("y", 10)])
    chunks = chunk_text(text, target_words=300)
    # 290 + 10 fits one chunk outright; force a split with a lower target instead.
    assert len(chunks) == 1
    chunks = chunk_text(text, target_words=280)
    assert len(chunks) == 1  # the 10-word tail merged back rather than standing alone
    assert "y9" in chunks[0].text


def test_preamble_before_any_heading_gets_no_section() -> None:
    chunks = chunk_text(_para("lead", 30) + "\n\n## Intro\n\n" + _para("body", 30))
    assert chunks[0].section is None
    assert chunks[1].section == "Intro"


def test_empty_and_capped_input() -> None:
    assert chunk_text("") == []
    many_sections = "\n\n".join(f"## S{i}\n\n{_para('w', 60)}" for i in range(200))
    assert len(chunk_text(many_sections, max_chunks=50)) == 50
