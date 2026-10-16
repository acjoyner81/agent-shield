# 0015.1. Java Gateway Core. gateway-java

**Date**: 2026-10-07
**Status**: Accepted
**Parent decision**: 0015 Java Gateway Core

## Summary

gateway-java is the platform edge for traffic that enters through port 8080. It verifies every JWT itself with the project issuer and audience, routes to the Python gateway, the MCP server, and the portal, and emits telemetry only for what happens at its own layer: rejected tokens and routing failures. It holds no scope logic and no rate budget of its own; Python is the single authority for both. This spec builds that role, removes the Spring Cloud request rate limiter the gateway inherited, and registers the gateway on the Python health surface.

## Context

Today gateway-java already authenticates (issuer and audience in SecurityConfig) and reads the correct routes from application.yml. What it does not do yet: any telemetry, any health registration with the platform, and it still carries a Spring Cloud RequestRateLimiter on the `/v1/chat/**` route that competes with the Python tenant token bucket. Python is the metering authority and the aggregator ships `telemetry:queue` to Splunk; the edge must write into that same stream so its failures are visible, and source its budget so a tenant is limited once, not twice.

## Requirements

**User stories**

- As a platform operator, I want an edge auth rejection to produce a telemetry event on the same stream as everything else, so a token that fails at the gate shows up in Splunk without any Python involvement.
- As a tenant administrator, I want my rate budget to be the same number whichever gateway I send from, so no path lets me burst past what I paid for.
- As an engineer, I want the health surface to include the Java gateway, so a silent Java outage is visible on the same dashboard as the other services.

**Acceptance criteria**

- **G-AC-1**: gateway-java verifies issuer and audience on every request except `/health` and `/actuator/health`, and never adds a principal header for a downstream service to trust.
- **G-AC-2**: no Spring Cloud RequestRateLimiter remains in gateway-java. A request that Python would throttle returns Python's 429 envelope to the caller.
- **G-AC-3**: an authentication rejection at the edge emits one `agentshield.security.authz_failure` event to `telemetry:queue` with source `agentshield-gateway-java`, the token's tenant attribute when present, and `unknown` otherwise. Served requests emit nothing from the edge.
- **G-AC-4**: a routing failure (no route matched, or the downstream returns 502/504) emits one `agentshield.telemetry.request.completed` event with the status and latency. Served requests emit nothing from the edge.
- **G-AC-5**: `/health` (plain JSON) and `/actuator/health` report the edge up, and stay up while the Python gateway is down; a route to Python still returns 502 while it is down.
- **G-AC-6**: `GET /v1/health/services` lists `gateway-java` as healthy and flips it to unhealthy when the container is stopped.

## Feature design

**API surface**

| Path | Target | Auth requirement |
|---|---|---|
| `/health`, `/actuator/health` | self | public |
| `/api/v1/**` | gateway-python:8000 | bearer, verified |
| `/v1/chat/**` | gateway-python:8000 | bearer, verified |
| `/mcp/**` | mcp-server:8081 | bearer, verified |
| `/**` fallback | portal-frontend:80 | public (frontend holds its own session) |

**Value sourcing**

- New: edge telemetry to `telemetry:queue` (authz failures, routing failures), `/health` plain controller, Python health probe entry for `gateway-java`.
- Existing: SecurityConfig issuer and audience, the JwtAuthenticationConverter role extraction is not needed and stays out, the route table, the Redis connection for the actuator Redis health indicator.

**Telemetry contract**

- Stream: `telemetry:queue` on the same Redis the aggregator reads.
- Envelope: the 0008 CloudEvents shape from `gateway/telemetry.py`, with `source` set to `agentshield-gateway-java`.
- Emitter: a reactive Redis `XADD` via Lettuce, `MAXLEN ~` like the Python emitter, matching the Python `QUEUE_MAXLEN`. Append only, no reader side. Emission is best effort and fire and forget: on an emitter error the edge WARN logs the event type and never blocks or changes the auth or routing outcome, and a Redis total outage loses edge events, which is accepted.
- Types: auth rejections use `agentshield.security.authz_failure`; routing failures use `agentshield.telemetry.request.completed`. A routing failure event is emitted only when Python produced no response at all: no route matched yields 404, a downstream connect or read failure yields 502 or 504. For any request Python answered, Python owns the single `request.completed` event and the edge emits nothing.

**Security model**

- Every non public route requires a valid JWT against issuer `https://dev-zymaiayb0afkpn7n.us.auth0.com/` and audience `https://api.agentshield.local`. The token is verified again at Python; double verification is intended defense in depth.
- The edge never emits a scope decision and never forwards credentials downstream; it forwards the original `Authorization` header unchanged so Python can verify.
- The proxied path never injects `X-User-ID`, `X-Tenant-ID`, or role headers; a header is never a principal on this platform.
- For a served request the JwtAuthenticationConverter reads the tenant from the same claim Python reads (`tenant_id` under the Auth0 namespace), only to tag telemetry.
- On a rejection the JWT is not available (Spring hands back no `Jwt` on failure), so the edge does a best effort unverified read of the bearer payload: split on dots, ignore the signature, look for the same `tenant_id` claim, default to `unknown` on a malformed or missing token. That read is telemetry only and is never used for authorization.

**Config**

| Name | Current | After |
|---|---|---|
| `AUTH0_ISSUER`, `AUTH0_AUDIENCE` | set in compose | unchanged |
| `SPRING_DATA_REDIS_HOST` | set in compose | kept for the actuator Redis indicator and the new emitter |
| gateway routes | includes RequestRateLimiter on chat | limiter key removed |

**Implementation skills**

No external community skills apply; this is Spring Cloud Gateway and WebFlux with the project's own Redis and telemetry contract.

## Decision

Complementary edge proxy: identity and edge events only, no scope logic, no own rate budget, health registered with the platform. Full reasoning in `rationale.md`.

## Build plan

Build approach: Tracer Bullet. The thinnest end to end thread is an edge auth rejection that lands as a Splunk visible event, so telemetry comes first; health and limiter removal thicken the slice.

**Slice 1, edge telemetry emitter (builds G-AC-3)**

- Add a reactive CloudEvents emitter on Lettuce that `XADD`s to `telemetry:queue`.
- Wire the authentication failure handler in SecurityConfig to emit `authz_failure` with tenant from the JWT claim or `unknown`.
- Tests: an unauthenticated request to a gated route returns 401 and leaves exactly one `authz_failure` entry on `telemetry:queue` with source `agentshield-gateway-java`; a valid request emits nothing.

**Slice 2, routing failure events (builds G-AC-4)**

- A small global filter writes `telemetry:queue` only when no route matched or the downstream status is 502/504, including latency. Latency is measured at the edge filter; the tenant for the event comes from the validated JWT when auth already passed, else the same best effort read, `unknown` on any failure.
- Tests: a bad host returns 404 and emits one event; duplicate emission is excluded for served responses.

**Slice 3, health surface (builds G-AC-5, G-AC-6, umbrella AC-3)**

- Add a plain JSON `/health` controller returning `{"status":"UP"}`; `/actuator/health` stays the probe source and the Python probe timeout stays the 2s default.
- Add `gateway-java` to the Python `GET /v1/health/services` probe list at `http://gateway-java:8080/actuator/health`.
- Tests: `GET /v1/health/services` includes `gateway-java` healthy; stopping the gateway container flips it to unhealthy; gateway health stays green while Python is stopped.

**Slice 4, rate limiter removal (builds G-AC-2, umbrella AC-6)**

- Strip the RequestRateLimiter filter and the limiter options from the `RouteLocator`; keep Redis for the actuator indicator.
- Only `/v1/chat/**` carried the RequestRateLimiter; removal touches that route alone and adds no other rate limiting filter.
- Tests: a tenant throttled by the Python token bucket gets the same 429 envelope through gateway-java as directly into Python; the 429 body and headers pass through unmodified; no independent limiter window exists.

## Migration plan

No path cutover for served traffic: the telemetry, health, and routing work is additive. The only behavioral change is removing the Java limiter, done in slice 4 after the 429 passthrough test passes. Revert this spec with one commit per slice.

## Consequences

**Positive**: edge failures become visible on the platform's single telemetry stream; the Java gateway cannot enforce a different budget; health dashboards include the edge.
**Negative**: the double decode costs one extra JWT parse per request at the origin.
**Neutral**: gateway-java now depends on Redis for more than health.

## Follow-up

- [x] Python probe test coverage for the new `gateway-java` entry lands with Slice 3. Covered by `gateway/tests/test_dashboard_api.py` (present in the service set, `degraded` on probe failure).
- [x] The Spring AI streamable path suffixes get pinned in the parent spec once develop measures them (shared follow up). Measured: the single mounted path is `/mcp/v1` (resolved in `0015-mcp-server.md`).
- [ ] Bound the downstream connect failure. Verify on 2026-10-16 measured a routing failure to a stopped `gateway-python` at ~30s before the edge returned 504 — the connect surfaced as a timeout rather than a fast refusal, so an unreachable downstream blocks an edge thread for the full default. Decide whether an explicit connect timeout should bring this in line with the 2s probe window.