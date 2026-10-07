# Scope: AgentShield Enterprise

A multi tenant AI gateway and agent platform for small businesses focusing on predictable costs, secure tool access, and measurable answer quality.

**Build approach:** Tracer Bullet (vertical slices ship real value early).
**Workflow:** Beta (after /develop, /check verify then /test). The project default level of rigor. /architect is the recommended first stop for a feature with a real decision, but skippable when you already know the build. Any feature can carry its own tag (e.g. · GA) to do more or less.

_These are recommendations to keep your build orderly, not requirements. Skip anything that does not fit: if you already know how to build a feature, use /develop and skip /architect. You decide when a feature is done._

## At a glance

| # | Feature | Phase | Status |
|---|---------|-------|--------|
| A | Existing Stack | Foundation | existing |
| 1 | Telemetry Standard | Foundation | done |
| 2 | Redis to Splunk Aggregator | Slice 1 | done |
| 3 | End to End Flow | Slice 1 | done |
| 4 | Auth0 Integration | Slice 2 | done |
| 5 | RBAC Enforcement | Slice 2 | in-progress |
| 6 | Hybrid Stripe Integration | Slice 3 | in-progress |
| 7 | Tenant Rate Limiting | Slice 3 | done |
| 8 | Usage Metering | Slice 3 | done |
| 9 | Admin Dashboard | Slice 4 | in-progress |
| 10 | Public API | Slice 4 | done |
| 11 | API Key Rotation | Slice 4 | done |
| 12 | UI Error Handling | Slice 4 | done |
| 13 | Land Telemetry in Splunk | Slice 1 | done |
| 14 | Unambiguous API Key Prefix | Slice 2 | planned |
| 15 | Permission Audit Across Routes | Slice 2 | planned |
| 16 | Eval Judge for Quality Scoring | Slice 4 | planned |
| 17 | Stripe Metered Usage Reporting | Slice 3 | planned |
| 18 | Prompt Tester for Guardrails | Slice 4 | planned |

## Foundations

### A. Existing Stack · existing
Docker, Redis, Splunk, and Python Gateway scaffold. code in `./`

### 1. Telemetry Standard · done · GA
Define the JSON log schema and trace ID propagation to ensure observability across all services.
**Done when:** a standardized log format is recorded in a spec and implemented in the gateway and MCP server.
- [x] Design it (spec): `/architect telemetry standard`
- [x] Define event schema: `/architect event schema` → Spec 0008
- [ ] Document it: `/document telemetry standard`
- Spec 0008 · `docs/specs/0008-event-schema.md` · standard for all services

## Slice 1: The Telemetry Loop

### 2. Redis to Splunk Aggregator · done
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
Spec 0002 · code in `gateway/aggregator.py` — CORRECTION 2026-10-05: this feature is `done` for the code, which was genuinely built and tested, but its Done when line above was NEVER met. Spec 0002 AC-1 (ships events to the Splunk collector) was ratified Accepted on 2026-09-14 without ever being true: the aggregator posts plain HTTP to a collector that only answers TLS, so no event has ever arrived. Spec 0014 proves it and feature 13 below now owns the real delivery. Read this row as "the aggregator code exists", not "telemetry lands in Splunk".

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
- [ ] Test it: `/test end to end flow` — SKIPPED deliberately at the done decision, not an oversight. `/test end to end flow` was never invoked as its own stage, but coverage does exist: `gateway/tests/e2e/test_full_saas_lifecycle.py` runs in the standing suite. This box therefore records a skip, not a stage run.
Spec 0003 · code in `gateway/main.py` and `java-services/mcp-server/`

### 13. Land Telemetry in Splunk · in-progress
Make events actually arrive in Splunk, make the setup reproducible from the repo, and surface a broken pipeline on the health page instead of letting it hide.
**Done when:** a fresh `docker compose up` ships telemetry into a searchable Splunk index with no hand edit, Splunk reports healthy on `/v1/health/services`, and the stranded events can be replayed to zero.
- [x] Design it (spec): `/architect land telemetry in splunk` → Spec 0014
- [x] Build it: `/develop land telemetry in splunk`
   - [x] Persist Splunk config and data, enable the collector from a repo owned file, publish its CA (AC-3, AC-4, AC-2)
   - [ ] Ship over verified TLS and prove one event lands in the index (AC-1, AC-2, AC-11) — the ship is built and the tests pass; the live proof against a fresh `docker compose up` is what keeps this open
   - [x] Collapse to one shipping path, read the collector response body, scrub personal data (AC-5, AC-12, AC-6)
   - [x] Cap the dead letter queue and count drops in one atomic script on a single push end (AC-8, AC-10)
   - [x] Add the health entry, the `telemetry:admin` scope, atomic redrive, and drain the stranded events (AC-7, AC-8, AC-9)
- [ ] Verify it: `/check verify land telemetry in splunk`
- [ ] Test it: `/test land telemetry in splunk`
Spec 0014 · `docs/specs/0014-land-telemetry-in-splunk/index.md` · code in `docker-compose.yml`, `gateway/aggregator.py`, `gateway/main.py`, `gateway/metering.py`

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
- [x] Test it: `/test auth0 integration` — run 2026-10-01. Added `gateway/tests/test_auth0.py` (32 tests) with real RSA-signed tokens, so `jwt.decode` runs for real instead of being handed claims. Covers signature, expiry, audience, issuer, the 403 missing-claim branch, the pre-rename claim name, and `permissions` normalisation. Suite is 328 passing with 6 live e2e tests.
- [ ] Document it: `/document auth0 integration`
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
- [ ] Verify it: `/check verify rbac enforcement` — record exists at `docs/specs/0005-rbac-enforcement/verify.md` (2026-09-23) and every step in it is ticked, but it is STALE and must be re run. It predates `c1c4fa3` (2026-10-02), which namespaced the Auth0 permissions claim because the bare `permissions` name collided with a reserved claim and was silently dropped. That build could not read a real user's permissions at all, so AC-2 was proven only against synthetic tokens. RBAC was later confirmed working for a real browser session by hand (201 on `POST /v1/keys`), which is not the same as a verify run.
- [ ] Test it: `/test rbac enforcement`
- [ ] Document it: `/document rbac enforcement`
Spec 0005 · code in `gateway/main.py`

### 14. Unambiguous API Key Prefix · planned · needs a decision
Move AgentShield issued keys off the `sk_live_` prefix, which is Stripe's live secret format and trips secret scanners.
**Done when:** issued keys carry an unmistakable platform prefix, and the decision for keys already in circulation is written down and applied.
- [ ] Design it (spec): `/architect api key prefix`

### 15. Permission Audit Across Routes · planned · needs a decision · GA · from spec 0005
Assign an explicit required permission to every `/v1/*` route, so access control stops depending on which routes somebody remembered to wire.
**Done when:** every route in the public surface declares the scope it requires, the default for an unwired route is deny, and a test fails when a new route ships without one.
- [ ] Design it (spec): `/architect permission audit`

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

### 7. Tenant Rate Limiting · done
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

### 17. Stripe Metered Usage Reporting · planned · needs a decision · GA · from spec 0007
Report the usage counters Redis already keeps to Stripe, so a tenant is actually billed for what they consumed instead of only for their base fee.
**Done when:** a tenant's token and tool call counters reach Stripe's metered billing and appear on their invoice, with a replay that cannot double count.
- [ ] Design it (spec): `/architect stripe metered usage`

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
- [ ] Document it: `/document usage metering`
Spec 0009 · `docs/specs/0009-usage-metering-engine/index.md` · code in `gateway/metering.py`

## Slice 4: Tenant Experience

### 9. Admin Dashboard · in-progress
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
- [x] Test it: `/test admin dashboard` — `dashboard.component.spec.ts` covers the live cards, the cost role gate, and the manual refresh. Tick synced 2026-10-02; the timer specs moved to `app.spec.ts` when the shell took the poll.
Spec 0011 · `docs/specs/0011-admin-dashboard/index.md` · code in `gateway/pricing.py`, `gateway/metering.py`, `gateway/main.py`, `portal-frontend/src/app/features/dashboard/`

### 10. Public API · done
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
- [x] Review it: `/check review public api` — run 2026-09-28, verdict Blocked. 2 blockers, 3 major. Findings in `docs/reviews/2026-09-28-public-api.md`.
Spec 0012 · `docs/specs/0012-public-api/index.md` · code in `gateway/main.py`, `gateway/keys.py`, `gateway/metering.py`

### 11. API Key Rotation · done · GA
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
- [ ] Document it: `/document api key rotation`
Spec 0010 · `docs/specs/0010-api-key-rotation/index.md` · code in `gateway/keys.py`, `gateway/auth.py`

### 12. UI Error Handling · in-progress
Make every gateway refusal visible and correctly placed, and stop the portal from rendering invented telemetry when a fetch fails.
**Done when:** a 402, 429, or 403 is named on screen with the right surface, a failed background poll marks its widget stale instead of interrupting, and no seeded rows or hardcoded metrics remain.
- [x] Design it (spec): `/architect ui error handling` → Spec 0013
- [x] Build it: `/develop ui error handling`
   - [x] Add the pure classifier and the typed `GatewayError` model, exhaustive over the six kinds
   - [x] Add the signal store and the app level container with live region announcements
   - [x] Wire the interceptor after Auth0, scoped to `/api/v1/`, and declare `SURFACE` on every call site
   - [x] Replace the `console.warn` and `error: () => undefined` handlers, drop the seeded rows and hardcoded log metrics
   - [x] Enrich the portal facing 402 with the spend and cap figures
   - [x] Fix the 2 defects the runtime check found: bound the re-auth redirect by gateway evidence instead of the SDK's session flag, add the session notice it obliges, and bind the staleness note on every widget including the keys list
- [ ] Verify it: `/check verify ui error handling` — run 2026-10-02, verdict Partial, findings closed 2026-10-05. Every classification, surface, precedence, accessibility and honesty rule proven against real gateway refusals. The 2 unmet spec statements (a persistently rejected token looping re-auth without limit, and the "last updated N ago" note bound only on `/logs`) are fixed and covered by tests that reproduce their conditions; the fixes have not yet been driven through a browser, so the tick stays open. Findings and method in `docs/specs/0013-ui-error-handling/verify.md`.
- [x] Test it: `/test ui error handling` — `classify.spec.ts`, `error-state.service.spec.ts`, `gateway-error.interceptor.spec.ts`, and `notification-center.component.spec.ts` cover the classifier, the store, the interceptor, and the live region, plus the dashboard and keys staleness bindings. 172 portal tests passing. Tick synced 2026-10-05.
Spec 0013 · `docs/specs/0013-ui-error-handling/index.md` · code in `portal-frontend/src/app/core/errors/`, `portal-frontend/src/app/core/services/`, `gateway/main.py`

### 16. Eval Judge for Quality Scoring · planned · needs a decision · from spec 0011
Stamp a pass or fail on completed requests so the dashboard's quality pass rate card has a real signal source instead of a metric nothing feeds.
**Done when:** the quality pass rate on the dashboard is computed from judged requests rather than an empty set, and the judge is measurable, costed, and safe to run on every request.
- [ ] Design it (spec): `/architect eval judge`

### 18. Prompt Tester for Guardrails · planned · from spec 0013
Give the 400 guardrail path a surface that exercises it end to end, so a blocked prompt is reachable in the product instead of only in tests.
**Done when:** an operator can submit a prompt in the portal, see it blocked by the guardrail with the reason named on screen, and see the telemetry event that followed.
- [ ] Build it: `/develop prompt tester for guardrails`

## Deferred
Out of scope for the current build pass, kept so the plan stays honest.
- **Dynamic Routing**: cost and latency based fallback · needs a decision
- **Splunk searches, dashboards and alerts**: decide what the platform actually needs, once events land. Shipping to a searchable index nobody queries is the next version of the same problem · from spec 0014
- **Aggregator as a gateway background task**: with one collector call site left, whether it still needs its own container is now a real question · from spec 0014
- **Certificate authority refresh procedure**: Splunk can regenerate its CA, and consumers stop shipping until the shared file is refreshed. Write it down so the recovery is not folklore · from spec 0014

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
