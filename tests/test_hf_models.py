"""Reading the Hugging Face model listing.

The load-bearing test here is ``primary_arxiv_id``. A model card cites its ancestors as well as
itself, and taking the wrong tag is not a harmless miss: on a first pass it put a row of NVIDIA
speech models under "Attention Is All You Need", because that was the only tag the corpus
happened to hold. The rule that fixed it - newest tag wins - is pinned below with real payloads.
"""

from researchscout.sources.hf_models import HubModel, arxiv_ids_in, parse_models

PAYLOAD = [
    {
        "id": "openai/whisper-large-v3",
        "pipeline_tag": "automatic-speech-recognition",
        "downloads": 4_000_000,
        "likes": 3_000,
        "createdAt": "2023-11-07T15:00:00.000Z",
        "tags": ["transformers", "arxiv:2212.04356", "license:apache-2.0"],
    },
    {
        "id": "handy-computer/parakeet-unified-en-0.6b-gguf",
        "pipeline_tag": "automatic-speech-recognition",
        "downloads": 12_000,
        "likes": 40,
        "createdAt": "2026-05-01T00:00:00.000Z",
        "tags": ["arxiv:2305.05084", "arxiv:2604.19079", "arxiv:2304.09325"],
    },
    {"id": "no-tags/model", "pipeline_tag": "text-generation", "downloads": 5, "likes": 0},
    {"modelId": "legacy-key/model", "downloads": None, "likes": None},
    {"pipeline_tag": "text-generation"},
    "not a dict",
]


def test_parses_the_fields_a_landscape_needs() -> None:
    whisper = parse_models(PAYLOAD)[0]
    assert whisper.repo == "openai/whisper-large-v3"
    assert whisper.name == "whisper-large-v3"
    assert whisper.owner == "openai"
    assert whisper.pipeline == "automatic-speech-recognition"
    assert whisper.downloads == 4_000_000
    assert whisper.likes == 3_000
    assert whisper.created_at is not None and whisper.created_at.year == 2023


def test_entries_without_an_id_are_skipped() -> None:
    assert [model.repo for model in parse_models(PAYLOAD)] == [
        "openai/whisper-large-v3",
        "handy-computer/parakeet-unified-en-0.6b-gguf",
        "no-tags/model",
        "legacy-key/model",
    ]


def test_missing_counts_read_as_zero_not_as_an_error() -> None:
    legacy = parse_models(PAYLOAD)[3]
    assert (legacy.downloads, legacy.likes) == (0, 0)
    assert legacy.created_at is None


def test_a_payload_that_is_not_a_list_yields_nothing() -> None:
    assert parse_models({"error": "rate limited"}) == []
    assert parse_models(None) == []


def test_arxiv_tags_are_read_in_order_without_duplicates() -> None:
    tags = ["arxiv:2212.04356", "license:mit", "arxiv:2212.04356v2", "arxiv:2305.05084"]
    assert arxiv_ids_in(tags) == ["2212.04356", "2305.05084"]
    assert arxiv_ids_in(["not-arxiv", "arxiv:nonsense"]) == []
    assert arxiv_ids_in([]) == []


def test_one_tag_is_unambiguously_the_paper() -> None:
    assert parse_models(PAYLOAD)[0].primary_arxiv_id == "2212.04356"


def test_several_tags_resolve_to_the_newest() -> None:
    """The one that is about this model; the rest are what it stands on.

    This is the rule that stops half of Hugging Face resolving to a 2017 paper.
    """
    assert parse_models(PAYLOAD)[1].primary_arxiv_id == "2604.19079"


def test_no_tags_means_no_paper() -> None:
    assert parse_models(PAYLOAD)[2].primary_arxiv_id is None


def test_a_repo_without_an_owner_still_reads() -> None:
    bare = HubModel(repo="gpt2", pipeline=None, downloads=1, likes=0, created_at=None, arxiv_ids=[])
    assert bare.name == "gpt2"
    assert bare.owner is None
