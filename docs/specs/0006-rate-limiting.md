# 0006. Tenant Token Bucket Rate Limiting & Isolation

**Date**: 2026-09-15
**Status**: Accepted

## Summary

Implement tenant level token bucket rate limiting at the API Gateway perimeter using Redis. Every request to protected API routes evaluates tenant capacity against configurable rates, returning an HTTP 429 status code with retry guidance and logging telemetry events when capacity is exhausted.

## Context

As AgentShield handles multi-tenant LLM traffic and MCP tool execution, individual tenants can inadvertently or maliciously flood the gateway with requests. Without rate limiting, a single noisy tenant could consume system resources, exhaust downstream LLM quotas, or cause service degradation for other tenants.

We need a flexible, high-performance perimeter rate limiter that enforces per-tenant limits smoothly without introducing latency overhead.

## Requirements

**User stories**:
- As a tenant administrator, I want my API requests to be processed predictably while being protected against noisy neighbor service degradation.
- As a gateway operator, I want requests exceeding tenant limits to fail fast with standard HTTP headers and telemetry logging before hitting downstream services.

**Acceptance criteria**:
- **AC-1**: Every incoming request to protected `/v1/*` endpoints evaluates the tenant's token bucket in Redis keyed by `rate_limit:{tenant_id}`.
- **AC-2**: If tokens remain in the bucket, decrement the token count, attach rate limit headers (`X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`), and allow the request to proceed.
- **AC-3**: If capacity is exhausted, short-circuit the request returning `HTTP 429 Too Many Requests` with a `Retry-After` header and payload `{"detail": "Tenant rate limit exceeded"}`.
- **AC-4**: Exhaustion events emit a structured telemetry warning (`event="rate_limit_exceeded"`, `tenant_id`, `user_id`, `endpoint`).
- **AC-5**: Public/unauthenticated endpoints (`/health`, `/metrics`, `/docs`) bypass rate limiting evaluation entirely.

## Options considered

### Option 1: Token Bucket Algorithm in Redis (Recommended)

Store capacity and refill timestamps per tenant in Redis using atomic Lua scripts or sliding window counters.

**Pros**:
- Handles traffic bursts smoothly while enforcing strict requests-per-minute (RPM) limits.
- Sub-millisecond evaluation overhead using Redis key lookups.
- Avoids boundary spike issues common with fixed window counters.

**Cons**:
- Slightly higher Redis memory footprint compared to simple fixed-window key counters.

### Option 2: Fixed Window Counter

Increment a key per tenant per minute (`rate:{tenant_id}:{minute}`).

**Pros**:
- Simple implementation using Redis `INCR` and `EXPIRE`.

**Cons**:
- Double-capacity burst window spikes possible across window boundaries (e.g. at minute boundaries).

## Decision

**Chosen option**: Option 1: Token Bucket Algorithm in Redis

Implement a FastAPI perimeter dependency that validates tenant capacity against a Redis token bucket before processing protected requests.

**Implementation skills**: `fastapi` (`tiangolo/fastapi`, `pip install fastapi`) · `redis` (`redis/redis-py`, `pip install redis`)

## Rationale

The Token Bucket algorithm provides the optimal balance of burst tolerance and rate enforcement for multi-tenant gateways. Redis sliding window token buckets ensure low-latency checks at the perimeter without allowing boundary spikes.

## Feature design

**Data model sketch**:
The rate limiting state is stored in Redis:
- Key: `rate_limit:{tenant_id}` (Hash or JSON string with `tokens`, `last_updated`).

**API surface**:
Global FastAPI perimeter dependency applied to `/v1/*` routes.

| Endpoint | Method | Key inputs | Key outputs | Auth | Key errors |
|---|---|---|---|---|---|
| /v1/* | ALL | `X-Tenant-ID` or Bearer Token | Headers: `X-RateLimit-*` | Bearer | 429 Too Many Requests |

**Value sourcing**:
| Action | Value produced / displayed | Source |
|---|---|---|
| Rate Limit Check | `tenant_id` | `request.state.tenant_id` from JWT / Auth |
| Remaining Capacity | `X-RateLimit-Remaining` | Redis `rate_limit:{tenant_id}` token count |
| Retry Delay | `Retry-After` | Calculated seconds until next token availability |

**Key invariants**:
- Default Deny on Exhaustion: If capacity is 0, the gateway MUST return 429 immediately without forwarding requests downstream.
- Bypass Unauthenticated: Health and documentation routes bypass rate evaluation.

**Security model**:
Rate limiting isolates tenants from resource exhaustion attacks and prevents noisy-neighbor degradation across tenant boundaries.

**Configuration required**:
- `DEFAULT_TENANT_RPM`: Default requests per minute allowed (e.g., 60).
- `REDIS_URL`: Connection URL for rate limiting storage.

**Critical test scenarios**:
- Happy path: Requests under tenant RPM succeed with valid `X-RateLimit-Remaining` headers, verifies **AC-1**, **AC-2**
- Failure case: Requests over tenant capacity return HTTP 429 with `Retry-After` header and telemetry warning, verifies **AC-3**, **AC-4**
- Bypass case: Requests to `/health` bypass rate check, verifies **AC-5**

## Build plan

1. Implement Redis Token Bucket helper functions in `gateway/rate_limit.py`, satisfies **AC-1**, **AC-2**
2. Create `verify_rate_limit` dependency in `gateway/rate_limit.py`, satisfies **AC-1**, **AC-3**
3. Wire telemetry emission for rate limit failures in `verify_rate_limit`, satisfies **AC-4**
4. Attach `verify_rate_limit` dependency globally to `/v1/*` routes in `gateway/main.py`, satisfies **AC-1**, **AC-5**
5. Add unit tests in `gateway/tests/test_rate_limit.py`, satisfies **AC-1**, **AC-2**, **AC-3**, **AC-4**, **AC-5**

## Consequences

**Positive**:
- Protects downstream services and LLM providers from excessive traffic spikes.
- Ensures fair resource distribution across tenants.

**Negative / tradeoffs**:
- Adds a Redis read/write operation to every protected request path.

## Follow-up

- [ ] Support dynamic per-tenant custom rate limit configurations loaded from tenant settings database.
