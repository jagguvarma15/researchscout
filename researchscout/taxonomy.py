"""What this radar covers: the papers that belong in it, and the two axes it filters them by.

Three ideas live here, in order of how much they decide.

**Scope** is the rule for what is stored at all. A paper belongs if it touches computing,
statistics or mathematics -- ``cs``, ``stat``, ``eess``, ``math`` or ``math-ph`` anywhere in its
category list, cross-lists included. That single predicate is also the arXiv query the ingest
runs (``cat:cs.*`` and friends match the whole category list, not just the primary), so nothing
is fetched that would then be discarded, and a q-bio or physics paper is here exactly when it
reaches into this field.

**Subjects** are what a reader picks between: the field a paper is in. They deliberately overlap
-- ``stat.ML`` is machine learning and statistics both -- because they are lenses, not a
partition. The five non-core subjects select the intersection work, which is all that survives
the scope rule anyway.

**Topics** are the technique a paper uses, which is a different question from its field. Two of
the three are plain categories; ``rl`` is a phrase match, because arXiv has no reinforcement
learning category and the nearest approximation (``cs.LG`` plus ``cs.AI`` plus ``cs.MA`` plus
``cs.RO``) matches 1,137 papers a week to find the 138 that are actually about it. The asymmetry
is real and worth stating rather than papering over.

``ArchiveGroup`` and ``group_for`` remain for the two callers that group by arXiv's own filing
system (the daily report's sections and the stream's categorize stage); subjects replace them
everywhere a reader can see. The web app keeps a display-only mirror in
``apps/web/src/lib/taxonomy.ts``; this module is authoritative for filtering.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class ArchiveGroup:
    key: str
    label: str
    tech: bool


_GROUPS: tuple[ArchiveGroup, ...] = (
    ArchiveGroup("cs", "Computer Science", True),
    ArchiveGroup("stat", "Statistics", True),
    ArchiveGroup("eess", "Electrical Engineering and Systems", True),
    ArchiveGroup("math", "Mathematics", False),
    ArchiveGroup("physics", "Physics", False),
    ArchiveGroup("q-bio", "Quantitative Biology", False),
    ArchiveGroup("q-fin", "Quantitative Finance", False),
    ArchiveGroup("econ", "Economics", False),
)

PHYSICS_ARCHIVES = frozenset(
    {
        "astro-ph",
        "cond-mat",
        "gr-qc",
        "hep-ex",
        "hep-lat",
        "hep-ph",
        "hep-th",
        "math-ph",
        "nlin",
        "nucl-ex",
        "nucl-th",
        "physics",
        "quant-ph",
    }
)

_GROUP_BY_KEY = {group.key: group for group in _GROUPS}
_ARCHIVE_TO_GROUP: dict[str, ArchiveGroup] = {
    **{group.key: group for group in _GROUPS if group.key != "physics"},
    **{archive: _GROUP_BY_KEY["physics"] for archive in PHYSICS_ARCHIVES},
}


def archive_of(category: str) -> str:
    """Return the archive prefix of a category code: ``cs.LG`` -> ``cs``; ``gr-qc`` -> ``gr-qc``."""
    return category.split(".", 1)[0]


def group_for(category: str | None) -> ArchiveGroup | None:
    """Map a category code to its arXiv group, or None for unknown/missing categories."""
    if not category:
        return None
    return _ARCHIVE_TO_GROUP.get(archive_of(category))


# --- Scope -------------------------------------------------------------------------------

SCOPE_ARCHIVES = frozenset({"cs", "stat", "eess", "math", "math-ph"})


def archives_of(categories: Iterable[str]) -> frozenset[str]:
    """The distinct archive prefixes across a paper's whole category list.

    The database computes the same set through the ``paper_archives`` function (migration
    0020), which is what the scope and subject filters index against.
    """
    return frozenset(archive_of(category) for category in categories if category)


def in_scope(categories: Iterable[str]) -> bool:
    """Whether a paper belongs in this corpus at all.

    Cross-lists count, which is the whole point: a quantitative biology paper that also files
    under ``cs.LG`` is the kind of intersection work this radar exists to surface, while one
    that does not is somebody else's feed.
    """
    return bool(archives_of(categories) & SCOPE_ARCHIVES)


# --- Subjects ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Subject:
    """One field of study, as a set of whole archives plus a set of individual codes.

    Splitting it that way keeps the table short and keeps both halves indexed: archives match
    through ``paper_archives(categories) && ...`` and codes through ``categories ?| ...``,
    which are the two GIN paths the store already has.
    """

    key: str
    label: str
    archives: frozenset[str]
    categories: frozenset[str]
    #: Core subjects are what this radar is about; the rest are where it meets other fields.
    core: bool


SUBJECTS: tuple[Subject, ...] = (
    Subject(
        "ai",
        "AI and machine learning",
        frozenset(),
        frozenset(
            {
                "cs.AI",
                "cs.CL",
                "cs.CV",
                "cs.IR",
                "cs.LG",
                "cs.MA",
                "cs.NE",
                "cs.RO",
                "eess.AS",
                "eess.IV",
                "stat.ML",
            }
        ),
        True,
    ),
    Subject("stats", "Statistics", frozenset({"stat"}), frozenset(), True),
    Subject(
        "data",
        "Data science",
        frozenset(),
        frozenset({"cs.DB", "cs.DL", "cs.DM", "cs.DS", "cs.IR", "stat.AP", "stat.CO"}),
        True,
    ),
    Subject(
        "math",
        "Mathematics",
        frozenset({"math", "math-ph"}),
        frozenset({"cs.CC", "cs.GT", "cs.LO", "cs.NA", "cs.SC"}),
        True,
    ),
    Subject("bio", "Biology and health", frozenset({"q-bio"}), frozenset(), False),
    Subject("physical", "Physical sciences", PHYSICS_ARCHIVES, frozenset(), False),
    Subject("security", "Security and privacy", frozenset(), frozenset({"cs.CR"}), False),
    Subject(
        "society",
        "Society and economics",
        frozenset({"econ", "q-fin"}),
        frozenset({"cs.CY", "cs.HC", "cs.SI"}),
        False,
    ),
    Subject(
        "systems",
        "Systems and software",
        frozenset(),
        frozenset(
            {"cs.AR", "cs.DC", "cs.NI", "cs.OS", "cs.PF", "cs.PL", "cs.SE", "cs.SY", "eess.SY"}
        ),
        False,
    ),
)

_SUBJECT_BY_KEY = {subject.key: subject for subject in SUBJECTS}


def subject_for(key: str) -> Subject | None:
    """One subject by key, or None for an unknown one."""
    return _SUBJECT_BY_KEY.get(key)


def all_subjects() -> tuple[Subject, ...]:
    """Every subject in display order: the four core ones first, then the intersections."""
    return SUBJECTS


# --- Topics ------------------------------------------------------------------------------

#: Phrases that mean reinforcement learning and very little else. Fed to
#: ``websearch_to_tsquery``, so each is matched as a stemmed phrase against title and abstract.
#: "bandit" is the loosest of them and will occasionally catch a pure statistics paper; the
#: filter is labelled a technique match rather than a category for exactly that reason.
RL_PHRASES: tuple[str, ...] = (
    "reinforcement learning",
    "policy gradient",
    "policy optimization",
    "actor critic",
    "q learning",
    "temporal difference learning",
    "markov decision process",
    "reward model",
    "reward shaping",
    "RLHF",
    "RLAIF",
    "bandit",
)


@dataclass(frozen=True)
class Topic:
    """One technique. Matched by category where arXiv files it, by phrase where it does not."""

    key: str
    label: str
    categories: frozenset[str]
    phrases: tuple[str, ...]


TOPICS: tuple[Topic, ...] = (
    Topic("nlp", "NLP", frozenset({"cs.CL"}), ()),
    Topic("cv", "Computer vision", frozenset({"cs.CV", "eess.IV"}), ()),
    Topic("rl", "Reinforcement learning", frozenset(), RL_PHRASES),
)

_TOPIC_BY_KEY = {topic.key: topic for topic in TOPICS}


def topic_for(key: str) -> Topic | None:
    """One topic by key, or None for an unknown one."""
    return _TOPIC_BY_KEY.get(key)


def all_topics() -> tuple[Topic, ...]:
    """Every topic in display order."""
    return TOPICS


def phrase_query(phrases: Iterable[str]) -> str:
    """Join phrases into one ``websearch_to_tsquery`` input matching any of them.

    Quoting each phrase is what makes it a phrase rather than a bag of words: unquoted,
    "reinforcement learning" would match a paper mentioning reinforcement and learning
    paragraphs apart.
    """
    return " OR ".join(f'"{phrase}"' for phrase in phrases)
