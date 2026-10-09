# 0015. Java Gateway Core, reasoning

Decision record. `/develop` does not read this file; the build spec is in `index.md`, and each half is buildable from its own child spec.

## Context

Two Java services already sit in the stack, and neither is finished. `gateway-java` is a Spring Cloud Gateway reverse proxy. It verifies a JWT on every non public route with the same issuer and audience as Python, it routes `/api/v1/**` and `/v1/chat/**` to `gateway-python:8000`, `/mcp/**` to `mcp-server:8081`, and everything else to the portal, and it carries a Redis `RequestRateLimiter` on the chat route. It emits no telemetry at all, and the Python health surface does not know it exists, so the operator sees a service that can go down silently. `mcp-server` has the Spring AI MCP starter on its classpath but never activates the transport. Instead it serves a hand rolled `POST /rpc` controller that understands exactly one tool, `echoTool`, with no authentication of its own, so port 8081 is an open door to tool execution for anyone who can reach it.

The Python gateway is the production entry and the single authority for access control. `require_permission` (spec 0005) decides scopes, the tenant token bucket (spec 0006) decides rate limits, and the metering engine (spec 0009) counts tokens and tool calls. The CloudEvents envelope and the `telemetry:queue` Redis stream already exist (spec 0008), and the aggregator ships that stream to Splunk (spec 0014). None of that reaches the Java side.

Three forces shape the decision. First, authority must not fork. If the Java edge starts re implementing scopes or a rate budget, the platform has two answers to the same question and they will drift. Second, every entry path has to enforce identity. A tool port that answers an unauthenticated caller is a bypass around every control Python owns. Third, telemetry must not double count. A request that passes through two gateways and produces an event at each looks like two served requests in Splunk, which corrupts the audit trail and the usage numbers the dashboard reads.

The consequence of not deciding is that the Java services stay decorative: they are in the compose stack and in the product story, but they neither protect anything nor report anything, and the MCP protocol the starter already provides is bypassed by a bespoke endpoint.

## Options considered

### Option 1: Complementary edge (Chosen)

`gateway-java` stays a reverse proxy that verifies every JWT itself and emits telemetry only for what happens at its own layer (an auth rejection, a routing failure). It never checks a scope and never owns a rate budget; a served request is counted once, by Python. `mcp-server` activates the Spring AI streamable MCP transport, verifies JWTs on its own port, and Python's `tools/execute` migrates off the hand rolled `/rpc`.

**Pros**:
- One authority for scopes, limits, and metering, so behavior cannot drift between gateways.
- Every Java service enforces its own identity check, so no port bypasses auth.
- One telemetry record per served request, so Splunk and the audit page stay clean.
- Reuses controls the platform already proved instead of inventing parallel ones.

**Cons**:
- Defense level double JWT verification costs one extra decode at the origin.
- The MCP tool surface stays small; a tool that needs another host waits for a later slice.
- Two Java services each gain a Redis telemetry dependency they did not have.

### Option 2: Java as a second full gateway

Move scope checks, the rate budget, and usage counting into `gateway-java` as well, so either gateway can serve a request completely.

**Pros**:
- The Java edge could serve an enterprise deployment without Python in front.
- No cross service call for a policy decision.

**Cons**:
- Two implementations of the same rules is the definition of drift; a scope added in one gateway is forgotten in the other.
- The same request counted by both gateways corrupts the usage roll ups.
- Triples the surface to test and operate for no product capability the platform lacks.

### Option 3: Retire the Java services

Delete `gateway-java` and `mcp-server` and keep Python and the Angular portal alone.

**Pros**:
- The smallest stack to run and document.
- Removes port 8081 and the hand rolled MCP endpoint entirely.

**Cons**:
- Throws away a working edge and a working Spring AI tool host that the product claims.
- Loses the JWT at the edge and the MCP protocol surface for no correctness gain, since the defects here are small and fixable.
- Feature 19 of the scope asks for a complementary Java gateway, so this contradicts the roadmap.

### Option 4: Keep the hand rolled endpoint, add a token check

Leave `POST /rpc` in place and put the JWT resource server in front of it.

**Pros**:
- Least code; one security configuration and the endpoint is closed.

**Cons**:
- Keeps a bespoke protocol that only this server and Python understand, while the standard MCP transport sits unused on the classpath.
- Any future MCP client has to be written against the bespoke shape.
- The starter's tool discovery, logging, and streamable transport are all forfeited.

## Rationale

Option 1 wins because the specific force is authority, not capability. Python already owns scopes, the tenant bucket, and metering, and the platform's whole cost story depends on those being the only answer. A second gateway that also decides them does not add a capability, it adds a second source of truth, and the failure shows up as a tenant whose budget is enforced on one route and not another. Keeping the Java edge to identity and routing, and letting Python count and price the served request, preserves the one authority and makes the "one telemetry event per served request" rule implementable rather than aspirational.

Option 4 was rejected on a concrete detail the code shows: the MCP starter is already a dependency, and its streamable transport is a standard that external MCP clients speak, while `/rpc` is a bespoke shape that understands one tool. Adding auth to the bespoke endpoint closes the port but keeps the wrong protocol, so the first real MCP client would force this work again. Option 2 was rejected because it does not remove any code that exists; it copies it. Option 3 was rejected because the roadmap asks for the complementary gateway and the defects are cheap to fix.

Two points in the chosen option needed a call rather than an assumption. A token rejected at the Java edge still has to yield a tenant for the `authz_failure` event (the umbrella acceptance criterion asks for the tenant when the token carries one, and `unknown` otherwise). Spring's decoder hands back no `Jwt` on failure, so the edge does a best effort unverified read of the bearer's tenant claim for telemetry metadata only, never for authorization, and defaults to `unknown`. And a routing failure at the edge is a request that never reached Python, so it does not collide with Python's single served request event. It reuses the existing `request.completed` type with the downstream status, which keeps the Splunk vocabulary and dashboards unchanged instead of adding an event type nothing else understands.

The MCP half splits off one more decision. Once `mcp-server` requires a bearer, Python has to present one on every tool call, and a caller that authenticated with a machine key has no user JWT to forward. The chosen resolution is an Auth0 client credentials token minted for the MCP audience and cached by Python, so one code path calls the streamable endpoint regardless of how the caller authenticated. It also keeps the user's token from traveling to a second service. The cost is an Auth0 machine to machine application and two secrets, recorded as a prerequisite rather than hidden.

## References

**Project sources**:
- Root `AGENTS.md`, the stack and the tracer bullet build approach
- Spec 0008 event schema, the CloudEvents envelope and the `agentshield.security.authz_failure` and `agentshield.telemetry.request.completed` types
- `gateway/telemetry.py`, the envelope fields and the flat `data` shape the aggregator and metering read
- `gateway/main.py` (`execute_tool`, `_probe_mcp`) and `java-services/`, the current behavior both halves replace
- Spec 0005 RBAC and spec 0006 rate limiting, the authority that stays in Python
- `docker-compose.yml`, the service names and health wiring

**Practices & standards**:
- The strangler pattern for migrating a live path (`/rpc`) behind a proven replacement
- OAuth2 resource server verification with issuer and audience validation
- One event per served request, so the audit trail cannot double count
- Defense in depth, verifying identity at each service rather than trusting an upstream hop
