from researchscout.stream.broker import InMemoryBroker, StreamTopics


def test_topic_names_follow_the_prefix() -> None:
    topics = StreamTopics.for_prefix("rs")
    assert topics.raw == "rs.raw.v1"
    assert topics.parsed == "rs.parsed.v1"
    assert topics.enriched == "rs.enriched.v1"


def test_topic_configs_size_raw_for_fulltext() -> None:
    configs = StreamTopics.for_prefix("rs").configs()
    assert configs["rs.raw.v1"]["max.message.bytes"] == "5242880"
    assert configs["rs.raw.v1"]["retention.ms"] == str(168 * 3_600_000)
    assert configs["rs.parsed.v1"]["retention.ms"] == str(72 * 3_600_000)
    assert "max.message.bytes" not in configs["rs.enriched.v1"]


def test_in_memory_broker_captures_messages() -> None:
    broker = InMemoryBroker()
    broker.publish("rs.raw.v1", "arxiv:2607.1", b"one")
    broker.publish("rs.raw.v1", "arxiv:2607.2", b"two")
    broker.flush()
    broker.flush(timeout=0.5)  # the bounded form is part of the protocol
    assert [key for key, _ in broker.messages["rs.raw.v1"]] == ["arxiv:2607.1", "arxiv:2607.2"]


def test_kafka_broker_flush_accepts_a_timeout() -> None:
    from researchscout.stream.broker import KafkaBroker

    broker = KafkaBroker("localhost:1")  # construction is offline; nothing connects yet
    broker.flush(0.05)  # bounded drain returns promptly even with no broker
