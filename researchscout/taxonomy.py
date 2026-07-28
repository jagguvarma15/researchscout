"""The arXiv taxonomy: archives grouped for faceting, partitioned into tech and non-tech.

Pure and dependency-free. A category code like ``cs.LG`` belongs to an archive (``cs``), every
archive belongs to exactly one group, and every group is either tech or non-tech. The physics
archives (``hep-th``, ``quant-ph``, ...) share one umbrella group so the facet list stays short.
``AI_CATEGORIES`` is the fixed category set behind the AI quick filter, matched against a
paper's full category list (cross-lists included). The web app keeps a display-only mirror of
the group list; this module is authoritative for filtering.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


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

_PHYSICS_ARCHIVES = frozenset(
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

AI_CATEGORIES = frozenset({"cs.AI", "cs.LG", "cs.CL", "cs.CV", "cs.NE", "stat.ML"})

_GROUP_BY_KEY = {group.key: group for group in _GROUPS}
_ARCHIVE_TO_GROUP: dict[str, ArchiveGroup] = {
    **{group.key: group for group in _GROUPS if group.key != "physics"},
    **{archive: _GROUP_BY_KEY["physics"] for archive in _PHYSICS_ARCHIVES},
}


def archive_of(category: str) -> str:
    """Return the archive prefix of a category code: ``cs.LG`` -> ``cs``; ``gr-qc`` -> ``gr-qc``."""
    return category.split(".", 1)[0]


def group_for(category: str | None) -> ArchiveGroup | None:
    """Map a category code to its group, or None for unknown/missing categories."""
    if not category:
        return None
    return _ARCHIVE_TO_GROUP.get(archive_of(category))


def archives_for(kind: Literal["tech", "non_tech"]) -> frozenset[str]:
    """All archive prefixes belonging to tech (or non-tech) groups."""
    tech = kind == "tech"
    return frozenset(archive for archive, group in _ARCHIVE_TO_GROUP.items() if group.tech is tech)


def archives_for_group(key: str) -> frozenset[str]:
    """Archive prefixes behind one group key; empty for unknown keys."""
    if key == "physics":
        return _PHYSICS_ARCHIVES
    return frozenset({key}) if key in _GROUP_BY_KEY else frozenset()


def all_groups() -> tuple[ArchiveGroup, ...]:
    """Every group in display order."""
    return _GROUPS
