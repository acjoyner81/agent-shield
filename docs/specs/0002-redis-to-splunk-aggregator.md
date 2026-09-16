# 0002. Implement Redis to Splunk Telemetry Aggregator

**Date**: 2026-09-14
**Status**: Proposed

## Summary

This feature builds the telemetry "shuttle" that moves logs from the Redis buffer to the Splunk HTTP Event Collector (HEC). It ensures that logs are shipped efficiently in batches, handled reliably with retries, and dumped to stdout if the network fails. This completes the telemetry loop from the gateway to the observability platform.

## Context

The AgentShield gateways write telemetry logs to Redis to avoid blocking the primary request path. However, these logs are only useful if they reach Splunk for analysis. We need a dedicated aggregator service that can handle the throughput of the gateways while ensuring zero log loss during transient outages.

The system must operate under the constraints of the `0001-telemetry-standard` spec, ensuring all shipped logs maintain their trace and tenant identity.

## Requirements

**User stories**:
- As an operator, I want logs to move from Redis to Splunk automatically so that I have real time visibility into system health.
- As a developer, I want logs to be buffered and retried during Splunk outages so that I don't lose critical debug data.
- As a platform engineer, I want the aggregator to be lightweight and non-blocking so it doesn't compete for resources with the gateways.

**Acceptance criteria**:
- **AC-1**: The aggregator reads logs from Redis and ships them to the Splunk HEC endpoint.
- **AC-2**: Logs are flushed every 5 seconds OR when the buffer reaches 100 entries (whichever comes first).
- **AC-3**: Failed shipments trigger exponential backoff; after 5 failed attempts, the batch is moved to a Redis DLQ (`telemetry:dlq`).
- **AC-4**: The aggregator uses a single threaded async loop in Python to prevent race conditions and locking complexity.
- **AC-5**: If the transport layer fails completely, the aggregator dumps the current batch to `stdout` in NDJSON format without crashing.

## Options considered

### Option 1: Simple synchronous loop
A script that pops one log at a time and sends it to Splunk.

**Pros**:
- Simplest possible implementation.

**Cons**:
- Extremely inefficient; makes one HTTP call per log entry, which would crash the Splunk HEC at MVP volumes.

### Option 2: Distributed worker pool
Multiple concurrent workers pulling from Redis and shipping in parallel.

**Pros**:
- Maximum throughput for extreme scale.

**Cons**:
- Introduces complex locking requirements on Redis lists and potential log ordering issues.

### Option 3: Single threaded async loop with hybrid batching
A single asynchronous process that buffers logs and ships them based on time or size triggers.

**Pros**:
- High efficiency via batching.
- Zero locking complexity.
- Sufficient for MVP throughput (~1,000 logs/sec).

**Cons**:
- Single point of failure (though easily restarted by Docker).

## Decision

**Chosen option**: Option 3: Single threaded async loop with hybrid batching

We will implement a Python based async worker that buffers telemetry from Redis and ships to Splunk HEC using a hybrid trigger (5s / 100 items).

## Rationale

This approach is the "boring" and correct choice for MVP. It eliminates the overhead of distributed locking while providing the efficiency of batching. The use of an async loop allows the worker to handle I/O wait times without blocking, easily meeting the throughput requirements without the complexity of a worker pool.

## Feature design

**Data model sketch**:
- **Primary Queue**: `telemetry:queue` (Redis List) - Incoming logs from gateways.
- **Dead Letter Queue**: `telemetry:dlq` (Redis List) - Logs that failed after 5 retries.
- **Local Buffer**: In-memory list in the aggregator for batching.

**API surface**:
No external API. This is a background worker process.

**Value sourcing**:
| Action | Value produced / displayed | Source |
|---|---|---|
| Ship Batch | Splunk HEC Payload | Derived from logs popped from `telemetry:queue` |
| Retry Logic | Backoff Interval | Calculated from attempt count (exponential) |
| DLQ Move | DLQ Entry | Original log payload from failed batch |

**Key invariants**:
- No log is deleted from Redis until the HEC returns a 200 OK or the log is moved to the DLQ.
- The aggregator must not block the gateways' ability to write to Redis.

**Security model**:
The aggregator requires the Splunk HEC Token, which must be provided via a secret environment variable (`SPLUNK_HEC_TOKEN`).

**Configuration required**:
- `SPLUNK_HEC_URL`: The endpoint for the Splunk HEC.
- `SPLUNK_HEC_TOKEN`: The authentication token for HEC.
- `REDIS_URL`: Connection string for the telemetry buffer.
- `BATCH_SIZE`: Default 100.
- `BATCH_TIMEOUT_SEC`: Default 5.

**Critical test scenarios**:
- Happy path: logs are written to Redis and appear in Splunk within 5 seconds, verifies **AC-1**, **AC-2**.
- Failure case: Splunk HEC returns 503; worker retries 5 times then moves batch to `telemetry:dlq`, verifies **AC-3**.
- Transport failure: Network is cut; worker dumps batch to `stdout` in NDJSON, verifies **AC-5**.
- Volume test: 1,000 logs/sec written to Redis; aggregator keeps up without memory growth, verifies **AC-4**.

## Build plan

1. Scaffold the async worker process in Python using `asyncio` and `redis-py`, satisfies **AC-4**
2. Implement the hybrid batching loop (Time + Size triggers), satisfies **AC-2**
3. Implement the Splunk HEC client with batch POST capabilities, satisfies **AC-1**
4. Implement the exponential backoff and DLQ routing logic, satisfies **AC-3**
5. Add the `stdout` NDJSON fallback for total transport failure, satisfies **AC-5**

## Consequences

**Positive**:
- Efficient use of Splunk HEC resources.
- Zero log loss via the DLQ.
- Low operational complexity.

**Negative / tradeoffs**:
- Logs may be delayed by up to 5 seconds (tradeoff for batch efficiency).

**Neutral**:
- Requires a dedicated container/process in the Docker Compose stack.
