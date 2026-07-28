import pytest

from researchscout.config import Settings, get_settings


def test_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.embedding_model == "BAAI/bge-small-en-v1.5"
    assert s.llm_base_url == "http://localhost:11434/v1"
    assert s.llm_model == "qwen2.5:3b-instruct"
    assert s.freshness_days == 30
    assert s.chat_guardrail is True


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RS_FRESHNESS_DAYS", "7")
    monkeypatch.setenv("RS_LLM_MODEL", "llama3.2:3b")
    monkeypatch.setenv("RS_CHAT_GUARDRAIL", "false")
    s = get_settings()
    assert s.freshness_days == 7
    assert s.llm_model == "llama3.2:3b"
    assert s.chat_guardrail is False
