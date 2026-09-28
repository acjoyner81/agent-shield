# Scope: AgentShield Enterprise

A multi tenant AI gateway and agent platform for small businesses focusing on predictable costs, secure tool access, and measurable answer quality.

**Build approach:** Tracer Bullet (vertical slices ship real value early).
**Workflow:** Beta (after /develop, /check verify then /test). The project default level of rigor. /architect is the recommended first stop for a feature with a real decision, but skippable when you already know the build. Any feature can carry its own tag (e.g. · GA) to do more or less.

_These are recommendations to keep your build orderly, not requirements. Skip anything that does not fit: if you already know how to build a feature, use /develop and skip /architect. You decide when a feature is done._

## At a glance

| # | Feature | Phase | Status |
|---|---------|-------|--------|
| A | Existing Stack | Foundation | existing |
| 1 | Telemetry Standard | Foundation | planned |
| 2 | Redis to Splunk Aggregator | Slice 1 | in-progress |
| 3 | End to End Flow | Slice 1 | planned |
| 4 | Auth0 Integration | Slice 2 | planned |
| 5 | RBAC Enforcement | Slice 2 | planned |
| 6 | Hybrid Stripe Integration | Slice 3 | in-progress |
| 7 | Tenant Rate Limiting | Slice 3 | done |
| 8 | Usage Metering | Slice 3 | done |
| 9 | Admin Dashboard | Slice 4 | in-progress |
| 10 | Public API | Slice 4 | done |
| 11 | API Key Rotation | Slice 4 | in-progress |

## Foundations

### A. Existing Stack · existing
Docker, Redis, Splunk, and Python Gateway scaffold. code in `./`

### 1. Telemetry Standard · done · GA
Define the JSON log schema and trace ID propagation to ensure observability across all services.
**Done when:** a standardized log format is recorded in a spec and implemented in the gateway and MCP server.
- [x] Design it (spec): `/architect telemetry standard`
- [x] Define event schema: `/architect event schema` → Spec 0008
- Spec 0008 · `docs/specs/0008-event-schema.md` · standard for all services

## Slice 1: The Telemetry Loop

### 2. Redis to Splunk Aggregator · in-progress
Build the actual shipping logic that moves telemetry data from Redis to the Splunk HTTP Event Collector.
**Done when:** logs appear in Splunk in real time with correct tenant and trace IDs.
- [x] Design it (spec): `/architect redis to splunk aggregator`
- [x] Build it: `/develop redis to splunk aggregator`
   - [x] Async worker scaffold (asyncio, redis-py)
   - [x] Hybrid batching loop (5s/100 items)
   - [x] Splunk HEC batch client
   - [x] Exponential backoff + DLQ routing
   - [x] NDJSON stdout fallback
- [x] Verify it: `/check verify redis to splunk aggregator`
- [x] Test it: `/test redis to splunk aggregator`
Spec 0002 · code in `gateway/aggregator.py`

### 3. End to End Flow · done
Wire the full path from Angular Portal through the Gateway to the MCP Server and back to Splunk.
**Done when:** a request from the portal results in a tool execution and a corresponding log entry in Splunk without 404s.
- [x] Design it (spec): `/architect end to end flow`
- [x] Build it: `/develop end to end flow`
   - [x] Implement /v1/tools/execute endpoint
   - [x] Build MCP JSON-RPC client
   - [x] Implement echo tool in Java MCP server
   - [x] Wire telemetry logging at each hop
- [x] Verify it: `/check verify end to end flow`
- [ ] Test it: `/test end to end flow`
Spec 0003 · code in `gateway/main.py` and `java-services/mcp-server/`

## Slice 2: Identity & Access

### 4. Auth0 Integration · done · GA
Integrate Auth0 for authentication and map JWT claims to tenant roles (tenant admin, developer).
**Done when:** users can sign in via Auth0 and the gateway recognizes their role from the token.
- [x] Design it (spec): `/architect auth0 integration`
- [x] Build it: `/develop auth0 integration`
   - [x] Install pyjwt and configure Auth0 env vars
   - [x] Implement Auth0 JWKS verification and caching
   - [x] Implement token verification dependency
   - [x] Implement verified tenant binding logic
   - [x] Wire authenticated context to telemetry
- [x] Verify it: `/check verify auth0 integration`
- [ ] Test it: `/test auth0 integration`
Spec 0004 · code in `gateway/main.py`

### 5. RBAC Enforcement · in-progress · GA
Implement gateway level access control to restrict MCP tool execution based on the user role.
**Done when:** developer roles are blocked from admin tools and tenant admins have full access.
- [x] Design it (spec): `/architect rbac enforcement`
- [x] Build it: `/develop rbac enforcement`
   - [x] Implement permission extraction into request state
   - [x] Build the `require_permission` dependency factory
   - [x] Wire 403 responses and authz telemetry
   - [x] Secure `/v1/tools/execute` as the tracer bullet
- [ ] Verify it: `/check verify rbac enforcement`
- [ ] Test it: `/test rbac enforcement`
Spec 0005 · code in `gateway/main.py`

## Slice 3: Billing & Monetization

### 6. Hybrid Stripe Integration · in-progress · GA
Implement a base monthly subscription combined with metered billing for tokens and tool calls.
**Done when:** Stripe successfully charges the base fee and tracks metered usage for a tenant.
- [x] Design it (spec): `/architect hybrid stripe integration`
- [x] Build it: `/develop hybrid stripe integration`
   - [x] Configuration & entitlement helper module
   - [x] Implement /v1/billing/checkout & /v1/billing/portal endpoints
   - [x] Implement /v1/billing/webhook endpoint with signature verification
- [x] Verify it: `/check verify hybrid stripe integration`
- [ ] Test it: `/test hybrid stripe integration`
Spec 0007 · `docs/specs/0007-stripe-integration/index.md` · code in `gateway/billing.py`
Enforce token bucket rate limits in Redis across all `/v1/*` routes per tenant.
**Done when:** requests exceeding tenant capacity return HTTP 429 with Retry-After header and trigger telemetry logs.
- [x] Design it (spec): `/architect tenant rate limiting`
- [x] Build it: `/develop tenant rate limiting`
   - [x] Implement Redis Token Bucket helper functions
   - [x] Build `verify_rate_limit` dependency & 429 response
   - [x] Wire telemetry warning on limit exhaustion
   - [x] Attach perimeter rate limiting dependency to `/v1/*`
- [x] Verify it: `/check verify tenant rate limiting`
- [x] Test it: `/test tenant rate limiting`
Spec 0006 · `docs/specs/0006-rate-limiting.md` · code in `gateway/rate_limit.py`

### 8. Usage Metering · done · GA
Build the logic to count tokens and tool executions per tenant in real time.
**Done when:** usage counts are accurately recorded in Redis/DB and available for the billing engine.
- [x] Design it (spec): `/architect usage metering` → Spec 0009
- [x] Build it: `/develop usage metering`
   - [x] Idempotent event processing & deduplication (`process_token_event`)
   - [x] Roll-up aggregates by tenant, model, and date
   - [x] REST endpoint `/v1/usage/summary`
   - [x] Dead-letter queue routing (`telemetry:dlq`)
- [x] Verify it: `/check verify usage metering`
- [x] Test it: `/test usage metering`
Spec 0009 · `docs/specs/0009-usage-metering-engine/index.md` · code in `gateway/metering.py`

## Slice 4: Tenant Experience

### 8. Admin Dashboard · in-progress
Create Angular components for the portal to display daily token consumption and estimated costs.
**Done when:** a tenant admin can see their usage metrics on the dashboard.
- [x] Design it (spec): `/architect admin dashboard` → Spec 0011
- [x] Build it: `/develop admin dashboard`
   - [x] Add the rate card and a read time cost loader
   - [x] Fold security and request events into per tenant daily counters
   - [x] Return cost, quality, and failure counters on the usage summary
   - [x] Add `/v1/health/services` with a probe per service
   - [x] Wire the dashboard cards to live values on a 60s poll
   - [x] Cover cost, role gate, tenant scope, and probes with tests
- [ ] Verify it: `/check verify admin dashboard` — run 2026-09-28, verdict BLOCKED. All backend criteria (AC-1, AC-2, AC-3, AC-4, AC-6) proven live; the seven UI steps need an Auth0 login, since `/dashboard` is behind the stock `authGuardFn` with no dev bypass. Two findings recorded in `verify.md`.
- [ ] Test it: `/test admin dashboard`
Spec 0011 · `docs/specs/0011-admin-dashboard/index.md` · code in `gateway/pricing.py`, `gateway/metering.py`, `gateway/main.py`, `portal-frontend/src/app/features/dashboard/`

### 9. Public API · done
Expose the FastAPI OpenAPI documentation and provide a way for tenants to generate API keys.
**Done when:** /docs is accessible and API keys allow programmatic access to the gateway.
- [x] Design it (spec): `/architect public api` → Spec 0012
- [x] Build it: `/develop public api`
   - [x] Register Bearer and `X-Tenant-API-Key` security schemes with the precedence rule
   - [x] Exclude health, telemetry ingest, webhook, `/api` aliases, and billing UI routes
   - [x] Add summaries, tags, descriptions, and examples to the four contract routes
   - [x] Document that a caller supplied `X-Tenant-ID` is never authoritative
   - [x] Accept a machine key on all four contract routes
   - [x] Lock the contract with schema and machine access tests
- [x] Verify it: `/check verify public api`
- [x] Test it: `/test public api`
- [ ] Review it: `/check review public api`
Spec 0012 · `docs/specs/0012-public-api/index.md` · code in `gateway/main.py`, `gateway/keys.py`, `gateway/metering.py`

### 10. API Key Rotation · in-progress · GA
Give machine access a real credential lifecycle: hashed storage, rotation with a grace window, revocation tombstones, and permission scopes enforced on RBAC routes.
**Done when:** clients can rotate keys without downtime, revoked or expired keys are rejected at the perimeter, and rotation activity is auditable in Splunk.
- [x] Design it (spec): `/architect api key rotation` → Spec 0010
- [x] Build it: `/develop api key rotation`
   - [x] Hashed key store with digest index (create/list)
   - [x] Rotate endpoint with grace window + tombstone revocation
   - [x] `verify_api_key` dependency wired into tools/chat routes
   - [x] Machine principals enforced by `require_permission`
   - [x] Key rotation telemetry event
- [x] Verify it: `/check verify api key rotation`
- [x] Test it: `/test api key rotation`
- [x] Review it: `/check review api key rotation`
Spec 0010 · `docs/specs/0010-api-key-rotation/index.md` · code in `gateway/keys.py`, `gateway/auth.py`

## Deferred
Out of scope for the current build pass, kept so the plan stays honest.
- **Dynamic Routing**: cost and latency based fallback · needs a decision

## Legend

**The decision box.** Every feature carries exactly one, the sub task whose label ends with (spec). Its wording varies (Design it (spec) normally, Decide the stack (spec) on Stack & architecture), so skills locate it by that (spec) suffix, never by an exact label. Every other box is an execution box and /architect never ticks one.

**Feature lifecycle**: the scope updates as a feature moves; each row is what it shows and who sets it:

| State | Set by | The feature shows |
|---|---|---|
| `planned` · needs a decision | `/scope` | one box: `Design it (spec): /architect <feature>` |
| `in-progress` (designed) | `/architect` at spec capture | `Design it` ticked; spec linked; `Build it: /develop <feature>` + 2 to 5 milestones; the tier's closing boxes (`Verify it` Alpha+, `Test it` Beta+, `Review it` + `Document it` GA); any surfaced follow up enrolled |
| `in-progress` (building) | `/develop` | milestone sub boxes tick one by one; code pointer filled |
| `in-progress` (verified) | `/check verify` | `Build it` + milestones ticked; `Verify it` ticked |
| `done` | you, when you decide it is | boxes you ran ticked, skipped ones marked skipped; the tier's last stage (`Prototype` → after `/develop`; `Alpha` → after `/check verify`; `Beta`/`GA` → after `/test`) is the suggested point to call it done; `/sync` captures conventions |

- **Next step** = the first unticked box (always a command or a tracked milestone).
- **needs a decision** = run `/architect` first; otherwise straight to `/develop` (or `/audit` for standards & tooling). The tag drops once the spec is captured.
- **Atomic build tasks live in the spec's ## Build plan, not here**: the scope carries only the milestone rollup.
- **Status** `planned` → `in-progress` → `done`, plus `existing` (pre workflow) and `dropped` (de scoped, kept for history).
- **Approach tag** beside a heading (e.g. · Facade) overrides the project default for that feature; no tag inherits the default.
- **Workflow tier tag** beside a heading (e.g. · GA, · Prototype) sets that one feature's rigor above or below the project default. It decides the check boxes and each skill's next suggestion.
- **Pointer line** (spec <n> · code in <path>): the spec link added by /architect, the code path by /develop.
