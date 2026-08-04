"""What belongs in this corpus, and the two axes it is filtered by.

The properties worth pinning are the ones a wrong answer would quietly break: that cross-lists
count towards scope (otherwise the intersection work this radar exists for never arrives), that
every subject is reachable and every core archive is covered by one, and that the RL phrase
query is a phrase query rather than a bag of words.
"""

from researchscout.taxonomy import (
    RL_PHRASES,
    SCOPE_ARCHIVES,
    all_subjects,
    all_topics,
    archive_of,
    archives_of,
    group_for,
    in_scope,
    phrase_query,
    subject_for,
    topic_for,
)


def test_archive_of_splits_on_the_first_dot() -> None:
    assert archive_of("cs.LG") == "cs"
    assert archive_of("astro-ph.CO") == "astro-ph"
    assert archive_of("gr-qc") == "gr-qc"
    assert archive_of("physics.optics") == "physics"


def test_archives_of_collapses_a_category_list() -> None:
    assert archives_of(["cs.LG", "cs.AI", "stat.ML"]) == frozenset({"cs", "stat"})
    assert archives_of([]) == frozenset()
    # An empty string is not an archive called "".
    assert archives_of(["", "cs.CL"]) == frozenset({"cs"})


def test_a_core_paper_is_in_scope() -> None:
    assert in_scope(["cs.LG"])
    assert in_scope(["stat.ME"])
    assert in_scope(["math.OC"])
    assert in_scope(["eess.SP"])
    assert in_scope(["math-ph"])


def test_a_cross_list_carries_a_paper_into_scope() -> None:
    # The whole point: a biology or physics paper reaching into machine learning belongs here,
    # and the same paper without that reach does not.
    assert in_scope(["q-bio.NC", "cs.LG"])
    assert in_scope(["quant-ph", "cs.LG"])
    assert not in_scope(["q-bio.NC"])
    assert not in_scope(["astro-ph.CO", "gr-qc"])


def test_nothing_at_all_is_out_of_scope() -> None:
    assert not in_scope([])


def test_scope_archives_are_the_computing_and_mathematics_ones() -> None:
    assert SCOPE_ARCHIVES == frozenset({"cs", "stat", "eess", "math", "math-ph"})


def test_subject_keys_and_labels_are_unique() -> None:
    subjects = all_subjects()
    assert len({subject.key for subject in subjects}) == len(subjects)
    assert len({subject.label for subject in subjects}) == len(subjects)


def test_every_subject_selects_something() -> None:
    for subject in all_subjects():
        assert subject.archives or subject.categories, subject.key


def test_every_subject_is_reachable_by_key() -> None:
    for subject in all_subjects():
        assert subject_for(subject.key) is subject
    assert subject_for("notreal") is None


def test_the_core_subjects_are_the_four_named_ones() -> None:
    assert [subject.key for subject in all_subjects() if subject.core] == [
        "ai",
        "stats",
        "data",
        "math",
    ]


def test_every_scope_archive_is_covered_by_some_subject() -> None:
    # Otherwise a paper could be stored and then be invisible behind every filter.
    reached = set()
    for subject in all_subjects():
        reached |= subject.archives
        reached |= {archive_of(code) for code in subject.categories}
    assert SCOPE_ARCHIVES <= reached


def test_subject_categories_are_real_arxiv_codes() -> None:
    for subject in all_subjects():
        for code in subject.categories:
            assert "." in code, code
            assert group_for(code) is not None, code


def test_topics_are_the_three_techniques() -> None:
    assert [topic.key for topic in all_topics()] == ["nlp", "cv", "rl"]
    assert topic_for("notreal") is None


def test_nlp_and_cv_are_categories_and_rl_is_phrases() -> None:
    nlp = topic_for("nlp")
    cv = topic_for("cv")
    rl = topic_for("rl")
    assert nlp is not None and nlp.categories == frozenset({"cs.CL"}) and not nlp.phrases
    assert cv is not None and cv.categories == frozenset({"cs.CV", "eess.IV"}) and not cv.phrases
    # arXiv has no reinforcement learning category, so this one has to read the text.
    assert rl is not None and not rl.categories and rl.phrases


def test_phrase_query_quotes_each_phrase() -> None:
    # Unquoted, "reinforcement learning" would match a paper mentioning the two words
    # paragraphs apart; quoted, it is a phrase.
    joined = phrase_query(["reinforcement learning", "bandit"])
    assert joined == '"reinforcement learning" OR "bandit"'
    assert phrase_query([]) == ""


def test_rl_phrases_carry_the_obvious_ones() -> None:
    assert "reinforcement learning" in RL_PHRASES
    assert "policy gradient" in RL_PHRASES
    assert "RLHF" in RL_PHRASES


def test_group_for_still_maps_the_arxiv_filing_system() -> None:
    # report.py and the stream's categorize stage group by this; subjects did not replace it.
    cs = group_for("cs.LG")
    assert cs is not None and cs.key == "cs"
    physics = group_for("hep-th")
    assert physics is not None and physics.key == "physics"
    assert group_for(None) is None
    assert group_for("notreal.XX") is None
