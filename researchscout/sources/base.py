"""The source connector contract and the config-driven registry.

A Source hides one upstream behind a common interface: an incremental ``fetch``, a ``normalize``
that maps the source's native shape into the canonical schema, and a ``health`` probe. Connectors
register themselves with ``@register``; the registry is filtered by ``config/sources.yaml``, so
enabling or disabling a source is config, not code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, ClassVar, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from researchscout.config import get_settings
from researchscout.schema import Paper, Signal

SourceKind = Literal["content", "signal"]
HealthStatus = Literal["ok", "rate_limited", "error"]


class RawItem(BaseModel):
    """A single item as fetched from a source, before normalization."""

    source: str
    fetched_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class SourceAttribution(BaseModel):
    """Where a source's data comes from and what its terms allow.

    Declared per source in ``config/sources.yaml`` next to ``enabled``, so a connector
    cannot be added without saying who it credits; the /about page reads it back.
    """

    name: str
    homepage: str
    terms: str
    data_license: str
    provides: str


class SourceDescription(BaseModel):
    """A registered source with its config state and attribution (None when undeclared)."""

    name: str
    kind: SourceKind
    enabled: bool
    attribution: SourceAttribution | None = None


class Source(ABC):
    """The contract every content/signal source implements."""

    name: ClassVar[str]
    kind: ClassVar[SourceKind]

    @abstractmethod
    def fetch(self, since: datetime, cursor: str | None) -> tuple[list[RawItem], str | None]:
        """Fetch items submitted on/after ``since``, resuming from ``cursor``.

        Returns the page of items plus the next cursor (``None`` when exhausted). Must be
        incremental and idempotent so re-runs over the same window do not duplicate.
        """

    @abstractmethod
    def normalize(self, raw: RawItem) -> Paper | Signal:
        """Map a fetched item into the canonical schema (PR 01)."""

    def health(self) -> HealthStatus:
        """Report availability. Default assumes healthy; override for a real probe."""
        return "ok"


_REGISTRY: dict[str, type[Source]] = {}


def register(cls: type[Source]) -> type[Source]:
    """Class decorator: register a Source under its ``name``."""
    _REGISTRY[cls.name] = cls
    return cls


def registered_sources() -> list[type[Source]]:
    """All registered source classes, sorted by name."""
    return [_REGISTRY[name] for name in sorted(_REGISTRY)]


def _load_config() -> dict[str, Any]:
    path = get_settings().sources_config_path
    if not path.exists():
        return {}
    data: Any = yaml.safe_load(path.read_text()) or {}
    sources = data.get("sources", {}) if isinstance(data, dict) else {}
    return sources if isinstance(sources, dict) else {}


def source_config(name: str) -> dict[str, Any]:
    """Return the config block for one source (empty dict if absent)."""
    block = _load_config().get(name, {})
    return block if isinstance(block, dict) else {}


def get_source(name: str) -> Source:
    """Instantiate a registered source by name."""
    if name not in _REGISTRY:
        raise KeyError(f"unknown source {name!r}; registered: {sorted(_REGISTRY)}")
    return _REGISTRY[name]()


def describe_sources() -> list[SourceDescription]:
    """Every registered source with its enabled state and attribution, sorted by name.

    Reads config directly rather than instantiating connectors: this feeds an HTTP route
    and must not touch the network or a database. Malformed attribution reads as missing
    so the page shows the gap instead of failing the whole listing.
    """
    config = _load_config()
    out: list[SourceDescription] = []
    for cls in registered_sources():
        cfg = config.get(cls.name, {})
        cfg = cfg if isinstance(cfg, dict) else {}
        block = cfg.get("attribution")
        attribution = None
        if isinstance(block, dict):
            try:
                attribution = SourceAttribution.model_validate(block)
            except ValidationError:
                attribution = None
        out.append(
            SourceDescription(
                name=cls.name,
                kind=cls.kind,
                enabled=bool(cfg.get("enabled", False)),
                attribution=attribution,
            )
        )
    return out


def enabled_sources(kind: SourceKind | None = None) -> list[Source]:
    """Instantiate every registered source marked ``enabled: true``, optionally filtered by kind."""
    config = _load_config()
    out: list[Source] = []
    for cls in registered_sources():
        cfg = config.get(cls.name, {})
        if not (isinstance(cfg, dict) and cfg.get("enabled", False)):
            continue
        if kind is not None and cls.kind != kind:
            continue
        out.append(cls())
    return out
