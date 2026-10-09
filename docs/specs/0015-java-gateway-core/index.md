# 0015. Java Gateway Core (gateway-java and mcp-server)

**Date**: 2026-10-07
**Status**: Proposed

## Summary

This decision defines what the Java side of AgentShield actually does. `gateway-java` stays a complementary edge gateway: it verifies every JWT itself, routes to the Python gateway, the MCP server, and the portal, and only emits telemetry for its own layer. `mcp-server` adopts the Spring AI MCP protocol that is already on its classpath, secures its own port with the same JWT check, and retires the hand rolled JSON RPC endpoint. Python stays the single authority for scopes, rate limits, and metering, so the two Java services complement it instead of duplicating it.

## Structure

This is an umbrella decision with two buildable halves. Each child spec is self sufficient to build from.

- [0015-java-gateway.md](0015-java-gateway.md): the gateway-java core. JWT at the edge, telemetry for edge events only, no own rate limiter, registration on the platform health surface.
- [0015-mcp-server.md](0015-mcp-server.md): the mcp-server core. Activate the Spring AI streamable MCP transport, JWT on the server itself, retire `/rpc`, migrate Python tool execution.

## Requirements

**User stories**:

- As an enterprise tenant administrator, I want both Java services to enforce the same identity checks as the Python gateway, so no entry path gives an unauthenticated caller tool access.
- As a platform operator, I want one telemetry record per served request, so logs and audit never double count a single call.
- As an engineer, I want the Java services to reuse the platform's proven controls (Auth0, Redis, the telemetry stream) instead of inventing parallel ones, so behavior does not diverge between gateways.

**Acceptance criteria** (the umbrella contract, each independently checkable):

- **AC-1**: A request that enters through gateway-java and reaches a Python route is served with Python enforcing RBAC and rate limiting, and produces exactly one served request telemetry event, which is Python's. The Java edge emits nothing for a served request.
- **AC-2**: A token rejected at the Java edge produces one `agentshield.security.authz_failure` event on `telemetry:queue` from source `agentshield-gateway-java`, with the tenant attribute when the token carries one and `unknown` otherwise.
- **AC-3**: `GET /v1/health/services` reports `gateway-java` and `mcp-server` as healthy entries that go unhealthy when their containers stop.
- **AC-4**: Python `POST /v1/tools/execute` for `echoTool` returns success after the MCP server retires `/rpc`, by calling the Spring AI streamable endpoint instead.
- **AC-5**: A direct call to mcp-server on port 8081 without a valid bearer receives 401, while `GET /health` stays public.
- **AC-6**: A tenant that exceeds its Python token bucket receives 429 for the same call whether it entered through gateway-java or directly into Python, and no independent Java budget exists.

## Cross child contract

These rules hold across both children and bind the slices together.

**Identity**. Both services verify the JWT themselves with the same issuer and audience (`AUTH0_ISSUER` and `AUTH0_AUDIENCE`). No service treats a header as a principal. Server to server calls to mcp-server use a client credentials token that Python mints from an Auth0 machine to machine application and caches, so a machine key caller needs no user JWT and the caller's session token never travels past the Python gateway. A tenant label header may ride along for telemetry and tool logging only; it never authorizes anything. Python verifies the caller again on the proxied path; that double verification is intended defense in depth, not overhead to remove.

**Single authority**. Python alone decides RBAC scopes, tenant token buckets, usage roll ups, and billing. The Java services route and serve; they never re implement a scope check or a rate budget.

**Telemetry**. Both Java services write into the existing `telemetry:queue` stream behind the same Redis, using the 0008 CloudEvents envelope. Only events that happen at the Java layer are emitted (auth rejections, routing failures, health). A request that is served downstream is counted by Python once. Event type names reuse the Python enum values so the aggregator and Splunk dashboards stay unchanged.

**Health**. mcp-server is already probed by Python at `http://mcp-server:8081/health`. gateway-java must register on the same health surface under the name `gateway-java`, probed at `http://gateway-java:8080/actuator/health`.

**MCP path**. Both consumers of the MCP protocol (the gateway route and Python `tools/execute`) call the Spring AI streamable transport mounted under the configured base path `/mcp/v1`. The gateway route `/mcp/**` already forwards to `mcp-server:8081` and continues to do so. The exact message and sse path suffixes for the starter version in the pom (1.0.0-M7) get confirmed during develop against the running server; the contract here is that every client uses the streamable endpoint and none uses `/rpc`.

## Decision

The Java side is a complementary edge layer, not a second implementation of the platform.

**Chosen option**: Complementary edge proxy for gateway-java, Spring AI MCP streamable transport with its own JWT verification for mcp-server.

- gateway-java keeps its role: Spring Cloud Gateway reverse proxy, issuer and audience verified on every non public request, no principal header, no scope logic, no independent rate limiter.
- mcp-server activates the streamable transport that is already on its classpath, verifies JWTs itself, exposes exactly the tools wired into the tool provider, and migrates Python tool execution off the hand rolled `/rpc`.

## Build plan

The build approach is Tracer Bullet: stand up the thinnest end to end threads first, then thicken. Build the two child plans below in this order so each slice is provable with tests inside its own service and against the live stack.

1. Work child [0015-mcp-server.md](0015-mcp-server.md) slices 1 to 3: JWT on mcp-server, the streamable transport working for `echoTool`, and Python `tools/execute` migrated. This is the thinnest vertical thread: a bearer token, a tool call, a result, a Python telemetry event. It satisfies umbrella **AC-4**, **AC-5**, and most of **AC-1**.
2. Work child [0015-java-gateway.md](0015-java-gateway.md) slices 1 to 3: the edge telemetry emitter, routing failure events, and health registration. It satisfies umbrella **AC-2**, **AC-3**, and closes **AC-1**.
3. Thicken both: retire `/rpc` (mcp-server slice 4) and remove the Java request rate limiter (gateway-java slice 4). These satisfy umbrella **AC-6** and finish **AC-4**.

## Migration plan

**Strategy**: strangler. The Python gateway is the production entry. The MCP `/rpc` surface stays live until Python `tools/execute` is migrated and verified, then it is deleted. The Java edge rate limiter is removed after the removal test proves Python 429s pass through unchanged.

**Phases**:
1. Build mcp-server slices 1 to 3; `/rpc` stays, Python still green on its current path.
2. Build gateway-java slices 1 to 3; the edge adds events and health, changes nothing for served traffic.
3. Cut over Python `tools/execute` to the streamable endpoint (mcp-server slice 3).
4. Retire `/rpc` (slice 4) and remove the Java limiter (gateway-java slice 4).

**Rollback**: each phase is one commit behind the stack's normal rebuild. Cutting over Python is the only behavioral switch; revert it and `/rpc` still serves.

**Risks**: the Spring AI 1.0.0-M7 starter may expose path suffixes that differ from this spec's expectation; develop confirms them against the running container before the Python cut over.

## Consequences

**Positive**:
- One authority for scopes, limits, and metering; no drift between gateways.
- Every Java service enforces its own identity check, so no unprotected port bypasses auth.
- One telemetry record per served request; Splunk dashboards and audit logs stay clean.

**Negative / tradeoffs**:
- defense-level double JWT verification costs one extra decode per request at the origin.
- the MCP tool surface is smaller than a reimplementation would be; tools that need a different host stay out until a later slice.

**Neutral**:
- `gateway-java` and `mcp-server` each gain a telemetry dependency on Redis that did not exist before.
- The Python health surface grows one more probe entry.

## Follow-up

- [ ] The Spring AI starter mount path suffixes get pinned in this spec once develop measures them against 1.0.0-M7.
- [ ] The Java services have no project workflow spec of their own; consider whether spring boot conventions belong in a nested `java-services/AGENTS.md` once the build lands.

## Rationale

Reasoning and options: see `rationale.md`.