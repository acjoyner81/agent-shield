# 0015.2. Java Gateway Core. mcp-server

**Date**: 2026-10-07
**Status**: Proposed
**Parent decision**: 0015 Java Gateway Core

## Summary

mcp-server speaks the MCP protocol the way AI agents expect it: a streamable HTTP transport served by the Spring AI MCP server starter that is already a dependency, with exactly the tools the tool provider registers. The service enforces its own JWT check on its own port, so calling 8081 directly is as safe as calling through the gateway. The hand rolled JSON RPC controller at `/rpc` is retired once Python tool execution migrates to the streamable endpoint.

## Context

mcp-server already depends on `spring-ai-starter-mcp-server-webflux` 1.0.0-M7 and configures a base path of `/mcp/v1`, but the actual protocol surface is a hand rolled `McpRpcController` at `/rpc` that understands a private `tools/call` shape. Python `POST /v1/tools/execute` posts to that `/rpc`, passing tenant headers and traceparent. Spring AI exposes `HealthController` at `/health`, which Python probes for the health surface. The service also carries a stray spring cloud gateway route block in application.yml and a stray MCP client starter in the pom, leftovers that this spec cleans up.

## Requirements

**User stories**

- As an AI agent, I want to discover and call tools over the standard MCP protocol, so any MCP client works without bespoke wiring.
- As a security reviewer, I want no port that serves tools without an identity check, so the agent tool surface is gated wherever it is reached.
- As a platform operator, I want a failed tool call to be a clean JSON-RPC error, never a crash, so the server stays up under bad arguments.

**Acceptance criteria**

- **M-AC-1**: the Spring AI streamable transport serves `tools/list` and `tools/call` over the configured base path `/mcp/v1` for every tool registered in the `ToolCallbackProvider`, which today is `echoTool` and `querySplunkLogs`.
- **M-AC-2**: the hand rolled `/rpc` controller is retired; no client calls it after the migration.
- **M-AC-3**: mcp-server verifies issuer and audience on every route except `/health` and `/actuator/health`. A direct call to 8081 with no bearer, an expired token, or a wrong audience receives 401.
- **M-AC-4**: Python `POST /v1/tools/execute` still succeeds for `echoTool` after the migration, now calling the streamable endpoint and parsing its response envelope instead of `/rpc`.
- **M-AC-5**: a `tools/call` failure returns a JSON-RPC error object with the method's error code and does not crash the server.
- **M-AC-6**: tool execution continues to run on the Java 21 virtual thread executor already configured; nothing migrates back to platform threads.
- **M-AC-7**: only registered tools are callable; `validateFirewallRule` stays out of the registry. The stray spring cloud gateway route block in application.yml and the stray MCP client starter in the pom are removed.
- **M-AC-8**: Python's streamable endpoint call to mcp-server uses a cached Auth0 client credentials token for the audience mcp-server validates; the caller's JWT never reaches mcp-server, and the tenant label header is never treated as authorization.

## Feature design

**API surface**

| Path | Purpose | Auth requirement |
|---|---|---|
| `/health`, `/actuator/health` | liveness and readiness | public |
| `/mcp/v1` streamable POST | `tools/list`, `tools/call`, initialize handshake | bearer, verified |
| `/mcp/v1` SSE GET | streaming event channel for the transport | bearer, verified |

(gateway-java `/mcp/**` continues to forward to `mcp-server:8081`; the streamable path suffixes for starter 1.0.0-M7 get confirmed during develop and pinned in the parent spec.)

**Value sourcing**

- New: JWT verification on the server, active streamable transport, Python `tools/execute` client migration, an Auth0 machine to machine application for the client credentials token.
- Existing: `SplunkMcpToolService` (`echoTool`, `querySplunkLogs`), the `HealthController`, the base path config, the virtual thread executor.
- Kept out: `validateFirewallRule` is not registered or exposed this slice; it needs its own feature decision about which firewall systems it drives.

**Security model**

- The server is its own OAuth2 resource server: `SecurityFilterChain` verifies issuer and audience on every route except health, using the same issuer and audience values as gateway-java, from compose env on the mcp-server service.
- Python authenticates to mcp-server with a client credentials token it mints from an Auth0 machine to machine application and caches, carrying the audience mcp-server validates. The caller's JWT never travels to mcp-server, and a caller that holds only a machine key still gets a token. The calling tenant travels as a label header that mcp-server uses for telemetry and tool logging only; it never authorizes anything and is never forwarded. Authorization scope decisions remain Python's.
- The tool registry is the source of truth for the exposed surface: whatever the `ToolCallbackProvider` wires is what `tools/list` reports, nothing else.

**Prerequisite**: an Auth0 machine to machine application whose token carries the audience mcp-server validates. Its client id and secret become compose env on the gateway-python service.

**Config**

| Name | Current | After |
|---|---|---|
| `spring.ai.mcp.server.name`, base path `/mcp/v1` | set in application.yml | unchanged |
| JWT issuer/audience env | absent on the mcp-server service | added to compose (same values as gateway-java) |
| spring cloud gateway routes block | present in application.yml | removed |
| MCP client starter | present in the pom | removed |

**Implementation skills**

No external community skills apply; this is the Spring AI MCP starter already in the pom and the project's own JWT and telemetry contracts.

## Decision

Activate the Spring AI streamable transport, enforce JWT on the server itself, expose registered tools only, and retire `/rpc` once Python migrates. Full reasoning in `rationale.md`.

## Build plan

Build approach: Tracer Bullet. The thinnest end to end thread is a bearer token that calls a tool through Python over the standard protocol, so auth and the approved transport come first.

**Slice 1, JWT on mcp-server (builds M-AC-3)**

- Add a SecurityFilterChain on mcp-server that verifies issuer and audience, permits `/health` and `/actuator/health`, and returns 401 otherwise. Reuse the gateway-java audience validation pattern.
- Tests: bare direct call to 8081 gets 401; expired and wrong audience tokens get 401; `/health` stays public.

**Slice 2, streamable transport live (builds M-AC-1)**

- Confirm the starter serves `tools/list` and `tools/call` for the registered service over `/mcp/v1`; tune the base path or prefix if the M7 defaults differ.
- Tests: `tools/list` reports `echoTool` and `querySplunkLogs`; `tools/call` for `echoTool` returns its echo over the streamable endpoint.

**Slice 3, migrate Python tool execution (builds M-AC-4, M-AC-8, umbrella AC-4)**

- Change `POST /v1/tools/execute` in gateway-python to call the streamable endpoint instead of `/rpc`. Mint and cache an Auth0 client credentials token for the MCP audience, attach it as the bearer, send the tenant as a label header plus traceparent, parse the new response envelope, and return the same tool result shape to callers. The caller's JWT is never forwarded to mcp-server.
- Tests: `echoTool` success through `/v1/tools/execute`; a failing tool returns the tool error to the caller; `echoTool` succeeds end to end for a machine key caller who has no user JWT; the Python suite stays green.

**Slice 4, retire `/rpc` (builds M-AC-2)**

- Delete `McpRpcController` once `tools/execute` is green on the new path.
- Tests: no reference to `/rpc` remains in the repo; `/v1/tools/execute` still passes.

**Slice 5, harden and clean (builds M-AC-5, M-AC-6, M-AC-7)**

- Map tool failures to JSON-RPC error objects with proper codes.
- Confirm tool execution stays on the Java 21 virtual thread executor.
- Lock the registry to the provider's tools; confirm `validateFirewallRule` is not callable.
- Remove the stray gateway route block in application.yml and the stray client starter in the pom.
- Tests: a thrown tool returns a JSON-RPC error and the server stays up; `tools/list` excludes `validateFirewallRule`; the pom and application.yml carry no MCP client or gateway route leftovers.

## Migration plan

Strangler within the parent 0015 phases: slices 1 and 2 add auth and the real transport while `/rpc` still serves; slice 3 cuts Python over to the streamable endpoint; slice 4 deletes `/rpc`. Revert any slice with one commit and the stack rebuilds to the previous serving shape.

## Consequences

**Positive**: agents speak standard MCP to a JWT gated endpoint; the tool surface is exactly the registered set; Python gets a protocol the ecosystem understands.
**Negative**: tool calls now pay a JWT verification at the server; the hand rolled response envelope shape is lost to callers that never used it.
**Neutral**: mcp-server gains JWT env config, and `/rpc` disappears from the surface listing.

## Follow-up

- [ ] Pin the M7 streamable path suffixes in the parent spec once develop measures them (shared follow up).
- [ ] A later feature decision covers `validateFirewallRule` and which systems it drives.