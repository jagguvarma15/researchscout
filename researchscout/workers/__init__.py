"""Long-running Kafka consumers. Each worker is one small loop over one topic; message handling
lives in plain functions so tests exercise them without a broker."""
