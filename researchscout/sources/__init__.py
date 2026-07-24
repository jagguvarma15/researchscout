"""Source connectors and the registry. Importing this package registers all connectors."""

from researchscout.sources import (  # noqa: F401  (import registers connectors)
    arxiv,
    code_adoption,
    hf_trending,
    s2_signal,
)
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
