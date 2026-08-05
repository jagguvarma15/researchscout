"""Reading the provider comparison's configuration.

Pure parsing, no database: the interesting behaviour is alias matching, which is what decides
whether Alibaba's models arrive as one row or three.
"""

from pathlib import Path

import pytest
import yaml

from researchscout.providers import Provider, load_providers, parse_providers

_DOCUMENT = {
    "benchmarks": ["gpqa-diamond", " swe-bench-verified ", ""],
    "providers": [
        {"name": "Alibaba", "country": "China", "aliases": ["Alibaba Cloud", "Qwen"]},
        {"name": "OpenAI", "country": "United States"},
        {"name": "", "aliases": ["nameless"]},
    ],
}


def test_a_provider_answers_to_every_name_it_is_filed_under() -> None:
    config = parse_providers(_DOCUMENT)
    alibaba = config.providers[0]
    assert alibaba.name == "Alibaba"
    assert alibaba.matches("Qwen")
    assert alibaba.matches("alibaba cloud")  # case-insensitive
    assert alibaba.matches("  Alibaba  ")  # and whitespace-insensitive
    # The display name is always an alias for itself, without having to be repeated.
    assert config.providers[1].matches("openai")


def test_matching_is_the_whole_field_rather_than_a_substring() -> None:
    """ "Mistral" must not swallow "Mistral community", which is a different organisation."""
    provider = Provider(name="Mistral AI", country="France", aliases=frozenset({"mistral ai"}))
    assert provider.matches("Mistral AI")
    assert not provider.matches("Mistral community")
    assert not provider.matches("Mistral AI Research")


def test_a_missing_organization_matches_nothing() -> None:
    provider = Provider(name="OpenAI", country=None, aliases=frozenset({"openai"}))
    assert not provider.matches(None)
    assert not provider.matches("")


def test_unusable_entries_are_dropped_rather_than_breaking_the_file() -> None:
    config = parse_providers(_DOCUMENT)
    assert [p.name for p in config.providers] == ["Alibaba", "OpenAI"]  # the nameless one goes
    assert config.benchmarks == ("gpqa-diamond", "swe-bench-verified")  # trimmed, blanks dropped


@pytest.mark.parametrize("document", [None, [], "nonsense", {"providers": "not a list"}])
def test_a_malformed_file_is_an_empty_comparison(document: object) -> None:
    """One table on one page is not worth 500ing a route over."""
    config = parse_providers(document)
    assert config.providers == () and config.benchmarks == ()


def test_an_unreadable_file_is_an_empty_comparison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RS_PROVIDERS_CONFIG_PATH", str(tmp_path / "absent.yaml"))
    load_providers.cache_clear()
    assert load_providers().providers == ()
    load_providers.cache_clear()


def test_the_shipped_configuration_parses_and_names_real_providers() -> None:
    """The file is editorial, but it still has to load and to reach the labs it claims to."""
    load_providers.cache_clear()
    config = parse_providers(yaml.safe_load(Path("config/providers.yaml").read_text()))
    names = [provider.name for provider in config.providers]
    assert {"OpenAI", "Anthropic", "Google DeepMind", "Meta"} <= set(names)
    assert config.benchmarks  # at least one column is asked for

    # The aliases are the point: these are the spellings the upstreams actually use.
    assert config.for_organization("Google") is not None
    assert config.for_organization("meta-llama") is not None
    assert config.for_organization("Qwen") is not None
    assert config.for_organization("Some Unlisted Lab") is None
