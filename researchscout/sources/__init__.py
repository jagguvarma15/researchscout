"""Source connectors and the registry. Importing this package registers all connectors."""

from researchscout.sources import arxiv, s2_signal  # noqa: F401  (import registers connectors)
from researchscout.sources.base import (
    RawItem,
    Source,
    enabled_sources,
    get_source,
    register,
    registered_sources,
    source_config,
)

__all__ = [
    "RawItem",
    "Source",
    "enabled_sources",
    "get_source",
    "register",
    "registered_sources",
    "source_config",
]
