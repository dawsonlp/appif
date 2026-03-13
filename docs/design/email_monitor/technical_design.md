# Technical Design: Email Monitor

**Author**: Senior Engineer
**Date**: 2026-02-22
**Status**: Ready for implementation
**Depends on**: appif Connector protocol, mimir-client, Kafka 4.2+

---

## 1. Context

The email evaluator script (`scripts/email_evaluator.py`) currently pulls messages from Gmail, classifies them with Claude, and prints results to stdout. This design replaces that script with a structured monitoring service that:

- Evaluates inbound messages from any appif connector or a Kafka topic
- Writes useful messages with their complete metadata envelope to a JSONL log file
- Writes a compact summary of non-useful messages to a separate JSONL log file
- Persists useful messages as artifacts in mimir

The source is pluggable: today it reads directly from email connectors; later a dumb collection agent writes raw messages to a Kafka topic and this service consumes from the topic instead. The classification and persistence logic is identical regardless of source.

---

## 2. Components

| Component | Responsibility | Dependencies |
|-----------|---------------|--------------|
| **MessageSource** | Protocol: yields `MessageEvent` objects from any origin | appif domain models |
| **ConnectorSource** | Adapts an appif `Connector` to `MessageSource` | appif Connector protocol |
| **KafkaSource** | Consumes `MessageEvent` JSON from a Kafka topic (future) | confluent-kafka |
| **Evaluator** | Classifies a message as useful/not-useful via LLM | Anthropic SDK |
| **JsonlLogger** | Writes structured JSONL to two files: useful and filtered | stdlib |
| **MimirSink** | Persists useful messages as artifacts in mimir | mimir-client |
| **EmailMonitor** | Orchestrates: source -> evaluate -> route to sinks | all above |

---

## 3. Data Flow

```
MessageSource
    |
    v
EmailMonitor.run()
    |
    +-- Evaluator.evaluate(event) --> EvaluationResult
    |
    +-- if useful:
    |       JsonlLogger.log_useful(event, result)      --> useful_messages.jsonl
    |       MimirSink.persist(event, result)            --> mimir artifact
    |
    +-- if not useful:
            JsonlLogger.log_filtered(event, result)    --> filtered_messages.jsonl
```

---

## 4. Source Abstraction

### MessageSource Protocol

Defines the contract for any message origin. Implementations must:

- Provide a `start()` method for initialization (connect, subscribe)
- Provide a `stop()` method for teardown
- Provide a blocking `messages()` iterator that yields `MessageEvent` objects
- Be interchangeable without affecting downstream logic

### ConnectorSource

Wraps any appif `Connector` (Gmail, Outlook, or future connectors). Internally registers as a `MessageListener`, queues events, and yields them from `messages()`. The specific connector is selected by configuration.

### KafkaSource (future)

Consumes from a Kafka topic where a collection agent has written serialized `MessageEvent` JSON. Deserializes each record back to a `MessageEvent` and yields it. Uses Kafka consumer group semantics for offset management.

The serialization format on Kafka is the `MessageEvent` dataclass serialized with `dataclasses.asdict()` plus ISO 8601 timestamps. `KafkaSource` reconstructs the full `MessageEvent` including nested `Identity`, `MessageContent`, `ConversationRef`, and `Attachment` objects.

### Switching Sources

The source is selected by configuration. When transitioning from connector to Kafka:

1. Deploy the collection agent that writes `MessageEvent` JSON to the Kafka topic
2. Change one environment variable to switch the monitor's source
3. No changes to evaluation, logging, or persistence logic

---

## 5. Evaluation

### EvaluationResult

A frozen dataclass returned by the evaluator for each message:

| Field | Type | Description |
|-------|------|-------------|
| `useful` | `bool` | Whether the message warrants attention |
| `category` | `str` | Classification label (e.g. `action_required`, `informational`, `newsletter`, `spam`) |
| `confidence` | `float` | 0.0 to 1.0 |
| `reasoning` | `str` | One-line explanation of the classification |
| `suggested_action` | `str` | Recommended next step (e.g. `reply`, `archive`, `flag`, `ignore`) |

### Evaluator Contract

- Input: `MessageEvent` + classification prompt (loaded from file, same mechanism as existing email_evaluator.py)
- Output: `EvaluationResult`
- Side effects: one LLM API call per message
- The prompt file location follows the existing convention (env var override, then config directory, then fallback)

---

## 6. Logging

### Two Log Files

| File | Content | Purpose |
|------|---------|---------|
| `useful_messages.jsonl` | Full message envelope + evaluation result | Complete audit trail for useful messages |
| `filtered_messages.jsonl` | Compact summary of non-useful messages | Lightweight record of what was filtered and why |

Log directory: configurable via environment variable, with a sensible default under `~/.local/share/appif/monitor/logs/`.

### Useful Message Log Schema

Each line is a JSON object containing:

| Field | Source | Description |
|-------|--------|-------------|
| `logged_at` | system clock | ISO 8601 timestamp of when the record was written |
| `message_id` | `MessageEvent.message_id` | Platform-assigned unique ID |
| `connector` | `MessageEvent.connector` | Source platform name |
| `account_id` | `MessageEvent.account_id` | Which account received the message |
| `author` | `MessageEvent.author` | Full `Identity` object (id, display_name, connector) |
| `timestamp` | `MessageEvent.timestamp` | When the message was sent (ISO 8601) |
| `conversation_ref` | `MessageEvent.conversation_ref` | Full `ConversationRef` (connector, account_id, type, opaque_id) |
| `content` | `MessageEvent.content` | Full `MessageContent` (text + attachment metadata) |
| `metadata` | `MessageEvent.metadata` | Platform-specific extras (subject, labels, etc.) |
| `evaluation` | `EvaluationResult` | All evaluation fields |

This is the complete `MessageEvent` envelope -- every field preserved for downstream consumption. Attachment `data` bytes are excluded (only metadata: filename, content_type, size_bytes, content_ref).

### Filtered Message Log Schema

Compact summary per line:

| Field | Source | Description |
|-------|--------|-------------|
| `logged_at` | system clock | ISO 8601 |
| `message_id` | `MessageEvent.message_id` | Platform message ID |
| `connector` | `MessageEvent.connector` | Platform name |
| `author_name` | `MessageEvent.author.display_name` | Who sent it |
| `subject` | `MessageEvent.metadata["subject"]` | Email subject (if available) |
| `timestamp` | `MessageEvent.timestamp` | ISO 8601 |
| `evaluation` | `EvaluationResult` | All evaluation fields |

No message body, no attachments, no opaque routing data.

---

## 7. Mimir Integration

### Tenant

- **Shortname**: `lpd_context_dev_v1`
- **Type**: `environment`
- Registered on first run using `ensure_tenant()` from mimir-client

### Artifact Type

- **Name**: `email_message`
- **Description**: Evaluated email message with classification metadata
- Registered on first run using `ensure_artifact_type()`

### Artifact Mapping

Each useful message becomes one mimir artifact:

| Artifact Field | Source |
|---------------|--------|
| `artifact_type` | `"email_message"` |
| `title` | `"{author_name}: {subject}"` |
| `content` | `MessageEvent.content.text` |
| `source` | `"appif-email-monitor"` |
| `source_system` | `MessageEvent.connector` |
| `external_id` | `MessageEvent.message_id` |
| `metadata` | Structured dict containing: author identity, account_id, timestamp, subject, labels, conversation type, evaluation result, attachment summaries |

The `external_id` field enables idempotent writes -- reprocessing the same message does not create duplicates.

### MimirSink Contract

- On start: ensure tenant and artifact type exist
- On persist: create artifact with the mapping above
- On stop: close the HTTP client
- Mimir persistence is optional and can be disabled via configuration

---

## 8. File Layout

```
src/appif/
    monitor/
        __init__.py
        _source.py          # MessageSource protocol + ConnectorSource
        _evaluator.py       # Evaluator + EvaluationResult
        _logger.py          # JsonlLogger (two-file writer)
        _mimir_sink.py      # MimirSink (artifact persistence)
        monitor.py          # EmailMonitor (orchestrator)
        config.py           # Configuration loading from env vars
scripts/
    email_monitor.py        # CLI entry point
```

---

## 9. Configuration

All configuration is via environment variables with sensible defaults:

### Source Selection

| Variable | Values | Default |
|----------|--------|---------|
| `APPIF_MONITOR_SOURCE` | `connector`, `kafka` | `connector` |
| `APPIF_MONITOR_CONNECTOR` | `gmail`, `outlook` | `gmail` |

### Kafka (future, only when source = kafka)

| Variable | Default | Description |
|----------|---------|-------------|
| `APPIF_MONITOR_KAFKA_SERVERS` | `localhost:9092` | Bootstrap servers |
| `APPIF_MONITOR_KAFKA_TOPIC` | `appif.messages.raw` | Topic to consume from |
| `APPIF_MONITOR_KAFKA_GROUP` | `email-monitor` | Consumer group ID |

### Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `APPIF_MONITOR_LOG_DIR` | `~/.local/share/appif/monitor/logs/` | JSONL output directory |

### Mimir

| Variable | Default | Description |
|----------|---------|-------------|
| `APPIF_MONITOR_MIMIR_URL` | `http://localhost:38000` | Mimir API base URL |
| `APPIF_MONITOR_MIMIR_TENANT` | `lpd_context_dev_v1` | Mimir tenant shortname |
| `APPIF_MONITOR_MIMIR_ENABLED` | `true` | Set `false` to disable mimir persistence |

### Evaluation

| Variable | Default | Description |
|----------|---------|-------------|
| `APPIF_EMAIL_EVAL_PROMPT` | (none) | Path to classification prompt file |
| `ANTHROPIC_API_KEY` | (required) | API key for Claude |

Connector-specific variables (Gmail credentials, Outlook client ID, etc.) are inherited from the existing appif configuration documented in `docs/usage.md`.

---

## 10. Error Handling

| Failure | Behavior |
|---------|----------|
| LLM API call fails | Log error, skip message, continue processing |
| Mimir API call fails | Log error, continue (message already logged to JSONL) |
| Source disconnects | Attempt reconnect with exponential backoff |
| Malformed Kafka message (future) | Log to filtered file with `category: "parse_error"`, continue |

The JSONL log files are the durable record. Mimir persistence is best-effort and can be replayed from the useful messages log if needed.

---

## 11. Testing Strategy

| Level | What | How |
|-------|------|-----|
| Unit | Evaluator prompt formatting and result parsing | Mock LLM responses |
| Unit | JsonlLogger output format | Write to temp files, assert JSON structure |
| Unit | MimirSink artifact mapping | Mock mimir-client |
| Unit | ConnectorSource queue mechanics | Mock Connector |
| Integration | Full pipeline with real LLM | Real Anthropic call, temp log dir |
| Integration | MimirSink against running mimir | Testcontainers (PostgreSQL) |

---

## 12. Trade-offs and Rationale

| Decision | Rationale |
|----------|-----------|
| JSONL over database for logs | Simple, portable, appendable, grep-friendly. Mimir handles structured persistence. |
| Two log files over one with a filter field | Simpler downstream consumption. Useful messages are typically piped to other tools. |
| Source protocol with iterator over callback | Simpler orchestrator loop. Backpressure is natural (iterator blocks). |
| `dataclasses.asdict()` for Kafka serialization | Standard library, no schema registry needed for phase 1. Schema registry can be added later if needed. |
| Mimir persistence is optional | JSONL is the primary record. Mimir adds semantic search but must not block the pipeline. |
| `external_id` for idempotency | Kafka at-least-once semantics require deduplication. Mimir rejects duplicate external_id per artifact type. |