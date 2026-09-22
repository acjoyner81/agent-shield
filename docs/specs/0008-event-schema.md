# 0008. Open Telemetry Standard & Event Schema

**Date**: 2026-09-22
**Status**: Proposed

## Summary

Defines a standardized CloudEvents-compatible JSON event schema across all AgentShield services (gateway-python, gateway-java, mcp-server, and tripwire). Every log entry, security violation, and token usage event follows a single envelope, ensuring uniform parsing by the Redis-to-Splunk Aggregator (spec 0002) and reliable consumption by the Usage Metering Engine (spec 0009).

## Context

AgentShield operates as a multi-tenant AI gateway where requests flow through multiple services. Currently, telemetry data uses a simplified JSON structure defined in spec 0001 and implemented in `gateway/telemetry.py`. This structure lacks event-type discipline, no `specversion`, no `event_id`, and does not carry token usage or security context in a structured way. The existing format is a flat log entry, not an event envelope.

This creates three problems:

1. The Usage Metering Engine (spec 0009) needs structured `data.tokens.total` per tenant to bill customers through Stripe integration (spec 0007), but the current telemetry format buries token counts under an opaque `context` field with no standard shape.
2. Security events (RBAC failures from spec 0005, rate limit exhaustion from spec 0006) need typed event classification to be reliably filtered in Splunk, but the current `category` field is a free-text string with no enforced vocabulary.
3. Without a `specversion` and `event_id`, tracing a specific event across service boundaries is unreliable, and deduplication in the aggregator becomes impossible.

## Requirements

**User stories**:
- As a gateway operator, I want every telemetry event to follow a standardized envelope so that the Redis-to-Splunk Aggregator can reliably parse and route it.
- As a billing engineer, I want token usage events to carry a structured `data.tokens` object so that the Usage Metering Engine can aggregate `data.tokens.total` per tenant_id for Stripe metered billing.
- As a security analyst, I want security events to have a typed `type` field so that Splunk queries can filter `agentshield.security.*` events in real time.

**Acceptance criteria**:
- **AC-1**: Every event follows the standardized JSON envelope with `specversion`, `event_id`, `timestamp`, `source`, `type`, `tenant_id`, `user_id`, `trace_id`, `span_id`, and `data` fields.
- **AC-2**: Event types follow the namespaced convention `agentshield.{domain}.{action}` where domain is one of `telemetry`, `token`, `security`, `billing`.
- **AC-3**: Token usage events carry a structured `data.tokens` object with `input`, `output`, `total`, and `model` fields.
- **AC-4**: Security events carry a structured `data.security` object with `rbac_passed`, `rate_limit_remaining`, and `tripwire_flagged` fields.
- **AC-5**: HTTP request events carry a structured `data.http` object with `method`, `path`, `status_code`, and `latency_ms` fields.
- **AC-6**: All services (gateway-python, gateway-java, mcp-server, tripwire) emit events conforming to this schema.
- **AC-7**: The existing `gateway/telemetry.py` implementation is updated to produce this event schema.

## Options considered

### Option 1: CloudEvents JSON envelope (Recommended)

Adopt the CloudEvents specification pattern (specversion, id, type, source, time, etc.) with a `data` payload carrying domain-specific information.

**Pros**:
- Industry-standard envelope enables interoperability with any CloudEvents-compliant system.
- The `data` abstraction cleanly separates the envelope from domain-specific content.
- Namespaced event types (`agentshield.*`) prevent collision across services.
- Already partially aligned with the existing spec 0001 structure.

**Cons**:
- Requires updating `gateway/telemetry.py` and all service emitters.
- Slightly larger JSON payload than the current flat structure.

### Option 2: Flat log format with added fields

Extend the current spec 0001 schema by adding `event_id`, `specversion`, and `data` sub-objects.

**Pros**:
- Minimal change to the existing structure.
- No need to restructure the envelope.

**Cons**:
- Mixes envelope metadata with domain data, reducing clarity.
- Not compatible with CloudEvents-based tooling.
- `event_id` and `specversion` would be peers of `tenant_id` rather than part of a standard envelope.

### Option 3: Custom protocol buffer schema

Define a protobuf schema for all events.

**Pros**:
- Compact binary format, fast serialization.
- Strong typing via protobuf definitions.

**Cons**:
- Adds a protobuf compilation step across all services.
- Not human-readable in logs or Splunk.
- Overkill for an event schema that needs to be inspectable.

## Decision

**Chosen option**: Option 1: CloudEvents JSON envelope

All AgentShield services adopt a CloudEvents-compatible JSON envelope with a `data` payload carrying domain-specific event information. Event types follow the `agentshield.{domain}.{action}` naming convention.

**Implementation skills**: `fastapi` (`tiangolo/fastapi`, `.claude/skills/architect/`) · `redis` (`redis/redis-py`, `.claude/skills/architect/`) · `telemetry` (`gateway/telemetry.py`)

## Rationale

Option 1 is the right choice because it aligns with industry standards while remaining lightweight enough for a multi-tenant gateway. The CloudEvents envelope provides the `specversion`, `event_id`, and `source` fields that the aggregator and metering engine need to function correctly. The `agentshield.*` namespace ensures no collisions between services and makes Splunk filtering trivial. The existing spec 0001 chose W3C traceparent for tracing, and this spec complements that by standardizing the event payload that carries the trace context.

## Feature design

**Data model sketch**:
Every event follows the CloudEvents-compatible envelope:

```json
{
  "specversion": "1.0",
  "event_id": "evt_9a8b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d",
  "timestamp": "2026-09-22T11:49:56.123Z",
  "source": "agentshield-gateway-python",
  "type": "agentshield.telemetry.request.completed",
  "tenant_id": "ten_prod_enterprise_01",
  "user_id": "usr_992184",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "span_id": "00f067aa0ba902b7",
  "data": {}
}
```

**Key invariant**: The `data` field is always an object containing domain-specific information. Its shape depends on the event type but always includes a `tenant_id` at the envelope level for filtering.

**API surface**:
This is a standard definition, not an API surface. All services emit events conforming to this schema when logging to Redis (key `telemetry:queue`) or stdout (NDJSON fallback).

**Value sourcing**:
| Action | Value produced / displayed | Source |
|---|---|---|
| Event type | `agentshield.{domain}.{action}` | Spec-defined namespace |
| Timestamp | ISO 8601 UTC | `datetime.now(timezone.utc).isoformat()` |
| Trace ID | W3C traceparent | `request.state.trace_id` or generated |
| Token total | `data.tokens.total` | LLM response metadata |
| RBAC status | `data.security.rbac_passed` | `require_permission` result |
| Rate limit remaining | `data.security.rate_limit_remaining` | Token bucket state |

**Key invariants**:
- Every event MUST have `specversion`, `event_id`, `timestamp`, `source`, `type`, `tenant_id`, and `data` fields.
- `source` MUST follow the format `agentshield-{service-name}` (e.g., `agentshield-gateway-python`, `agentshield-mcp-server`, `agentshield-tripwire`, `agentshield-gateway-java`).
- `type` MUST match the regex `agentshield\.(telemetry|token|security|billing)\.\w+`.
- `event_id` MUST be a UUID v4 prefixed with `evt_`.

**Security model**:
No sensitive data (JWTs, API keys, emails) appears in the event payload. PII scrubbing rules from spec 0001 apply before serialization. The `tenant_id` and `user_id` fields are internal identifiers, not PII.

**Configuration required**:
- No new environment variables required. The existing `config/settings.py` fields are sufficient.
- The `SPLUNK_ENABLED`, `SPLUNK_PASSWORD`, and Redis configuration from existing specs drive transport.

**Critical test scenarios**:
- Happy path: A `/v1/chat/completions` request produces an event with type `agentshield.telemetry.request.completed`, data.http and data.tokens populated, verifies **AC-1**, **AC-2**, **AC-3**, **AC-5**
- Security event: A permission-denied request produces an event with type `agentshield.security.authz_failure`, data.security populated, verifies **AC-4**
- Token usage: An LLM response produces an event with type `agentshield.token.usage`, data.tokens.total populated, verifies **AC-3**
- Rate limit: A rate-limited request produces an event with type `agentshield.security.rate_limit_exceeded`, verifies **AC-4**
- Cross-service: All four services (gateway-python, gateway-java, mcp-server, tripwire) emit conforming events, verifies **AC-6**

## Standard definition

**Canonical pattern**:

```python
# Python (FastAPI) - Event emission via gateway/telemetry.py
import uuid
from datetime import datetime, timezone

def emit_event(tenant_id, user_id, trace_id, span_id, event_type, data, source="agentshield-gateway-python"):
    event = {
        "specversion": "1.0",
        "event_id": f"evt_{uuid.uuid4().hex}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "type": event_type,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "trace_id": trace_id,
        "span_id": span_id,
        "data": data
    }
    return json.dumps(event)
```

```java
// Java (Spring Boot) - Event emission
// Use ObjectMapper to serialize the event envelope
// source = "agentshield-gateway-java"
// type follows agentshield.{domain}.{action} pattern
```

**Event type registry**:
- `agentshield.telemetry.request.completed` -- Standard API gateway request complete (tracks HTTP latency, path, status).
- `agentshield.token.usage` -- LLM token usage metadata for consumption-based billing.
- `agentshield.security.authz_failure` -- RBAC permission check denied (spec 0005).
- `agentshield.security.rate_limit_exceeded` -- Tenant exceeded token bucket limits (spec 0006).
- `agentshield.billing.subscription_changed` -- Subscription tier or status updated via Stripe webhooks (spec 0007).

**Replaces**:
- The flat telemetry format from spec 0001 (`{"timestamp": ..., "trace_id": ..., "tenant_id": ..., "service_name": ..., "log_level": ..., "category": ..., "message": ..., "context": {}}`).
- The `category` field as a free-text string for event classification.

**Enforcement**:
- The `gateway/telemetry.py` module provides a single `emit_event` helper that all services use.
- Type validation can be added via Pydantic model or runtime schema check in the aggregator.
- PR reviews should verify events conform to the schema.

**Rollout**:
- New code immediately emits events conforming to this schema.
- Existing `gateway/telemetry.py` updated to produce this format.
- Existing logs tracked as debt and migrated during the Redis-to-Splunk Aggregator (spec 0002) rollout.

**Exceptions**:
- None. All services and event types conform to this schema.

## Consequences

**Positive**:
- Unified event format enables reliable parsing by the Redis-to-Splunk Aggregator (spec 0002).
- Structured `data.tokens.total` enables the Usage Metering Engine (spec 0009) to reliably aggregate usage for Stripe billing.
- Typed event types (`agentshield.security.*`) make Splunk filtering trivial for security auditing.
- `event_id` enables deduplication and tracing across service boundaries.

**Negative / tradeoffs**:
- Requires updating `gateway/telemetry.py` and all service emitters to produce the new format.
- Existing telemetry data in Redis (under the old format) will need migration or dual-format support during transition.
- Slightly larger JSON payload than the current flat format.

**Neutral**:
- The `data` abstraction means future event types can be added without changing the envelope.
- `specversion` allows the schema to evolve without breaking existing consumers.

## Follow-up

- [ ] Update `gateway/telemetry.py` to produce the new event schema, satisfies **AC-7**
- [ ] Add Pydantic model for event validation in `gateway/telemetry.py`, satisfies **AC-1**
- [ ] Update `gateway/tests/test_telemetry.py` if it exists, or add new tests for the event schema, satisfies **AC-1**, **AC-2**
- [ ] Coordinate with the Redis-to-Splunk Aggregator (spec 0002) team to handle both old and new format during migration, satisfies **AC-6**

## References

**Project sources**:
- Spec 0001 (W3C Trace Context + Custom Tenant Header)
- Spec 0002 (Redis-to-Splunk Aggregator)
- Spec 0005 (RBAC Enforcement)
- Spec 0006 (Tenant Token Bucket Rate Limiting)
- Spec 0007 (Hybrid Stripe Integration)
- `gateway/telemetry.py` (current implementation)
- `gateway/tests/test_telemetry.py` (existing tests, if any)

**Practices & standards**:
- CloudEvents specification (event envelope pattern)
- W3C Trace Context (traceparent, trace_id, span_id)
- NDJSON for transport (from spec 0001)
