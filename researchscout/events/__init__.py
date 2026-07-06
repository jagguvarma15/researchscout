"""The event plane: topic schemas, the EventSink seam, and Kafka plumbing.

Only :mod:`researchscout.events.kafka` (and ``KafkaEventSink``) touch confluent-kafka, and they
import it lazily — the rest of the package stays importable without the ``kafka`` extra.
"""
