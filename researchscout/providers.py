"""Which organisations get a row in the provider comparison, and what it compares them on.

A leaderboard answers "what is the best score on this benchmark". The question people actually
arrive with is the other one: *which lab is ahead right now*, across the tests the field agrees
matter. That needs two judgements a query cannot make -- which organisations are worth a row,
and which benchmarks are worth a column -- so both live in ``config/providers.yaml`` where they
can be argued with and edited, rather than being buried in SQL.

Aliases are the load-bearing part. The same lab arrives under different names depending on who
is describing it: Epoch AI writes "Google DeepMind" and "Google" for the same organisation, and
a Hugging Face repository owner is a handle, so Meta turns up as "meta-llama". Matching is
case-insensitive and against the whole field, never a substring -- "AI21" must not swallow
"AI21 Labs Israel" by accident, and more to the point "Mistral" must not match "Mistral
community".

The benchmark list is treated as preferences rather than requirements: a benchmark named here
that the catalogue does not hold is skipped rather than rendering an empty column, because the
upstream decides what it publishes and the page should degrade to what exists.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

import yaml

from researchscout.config import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Provider:
    """One organisation worth comparing, and every name the upstreams file it under."""

    name: str
    country: str | None
    #: Casefolded, and always including ``name`` itself so a config need not repeat it.
    aliases: frozenset[str]

    def matches(self, organization: str | None) -> bool:
        """Whether an ``ai_models.organization`` value belongs to this provider."""
        if not organization:
            return False
        return organization.strip().casefold() in self.aliases


@dataclass(frozen=True)
class ProviderConfig:
    """The whole comparison: who is in it, and which benchmarks it prefers as columns."""

    providers: tuple[Provider, ...]
    benchmarks: tuple[str, ...]

    def for_organization(self, organization: str | None) -> Provider | None:
        """The provider an organisation name belongs to, or None when it is not one we list."""
        return next((p for p in self.providers if p.matches(organization)), None)


_EMPTY = ProviderConfig(providers=(), benchmarks=())


def _provider(entry: object) -> Provider | None:
    if not isinstance(entry, dict):
        return None
    name = str(entry.get("name") or "").strip()
    if not name:
        return None
    raw = entry.get("aliases")
    aliases = (
        {str(alias).strip().casefold() for alias in raw if str(alias).strip()}
        if (isinstance(raw, list))
        else set()
    )
    aliases.add(name.casefold())
    country = entry.get("country")
    return Provider(
        name=name,
        country=str(country) if country else None,
        aliases=frozenset(aliases),
    )


def parse_providers(document: object) -> ProviderConfig:
    """Build the configuration from parsed YAML; a malformed file is an empty comparison.

    Empty rather than an exception on purpose: this feeds one table on one page, and a page
    that renders without it is a better outcome than a benchmarks route that 500s because
    somebody left a stray dash in a list.
    """
    if not isinstance(document, dict):
        return _EMPTY
    raw_providers = document.get("providers")
    providers = (
        tuple(
            provider
            for provider in (_provider(entry) for entry in raw_providers)
            if provider is not None
        )
        if isinstance(raw_providers, list)
        else ()
    )
    raw_benchmarks = document.get("benchmarks")
    benchmarks = (
        tuple(str(item).strip() for item in raw_benchmarks if str(item).strip())
        if isinstance(raw_benchmarks, list)
        else ()
    )
    return ProviderConfig(providers=providers, benchmarks=benchmarks)


@lru_cache(maxsize=1)
def load_providers() -> ProviderConfig:
    """Read ``config/providers.yaml``; a missing or unreadable file is an empty comparison."""
    path = get_settings().providers_config_path
    try:
        document = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("could not read %s: %s", path, exc)
        return _EMPTY
    return parse_providers(document)
