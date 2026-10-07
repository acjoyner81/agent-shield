# AgentShield Enterprise - Project Context

## Project Overview
AgentShield is a multi-tenant AI gateway and agent platform for small businesses. It focuses on predictable LLM costs, secure tool access, reliable routing, and measurable answer quality.

## Tech Stack
- **Frontend:** Angular (Port 4200)
- **Python Gateway:** FastAPI (Port 8000)
- **Java Gateway:** Spring Boot (Port 8080)
- **MCP Server:** Java/Spring Boot (Port 8081)
- **Infrastructure:** Redis, Splunk, Tripwire, Docker Compose

## Agent Skills & Workflow
We use a structured workflow to ensure technical consistency:

1. `/scope` $\rightarrow$ Define boundaries in `docs/scope/`
2. `/architect` $\rightarrow$ Design decision and write spec in `docs/specs/`
3. `/develop` $\rightarrow$ Implement the feature based on the spec
4. `/check` $\rightarrow$ Verify implementation
5. `/sync` $\rightarrow$ Update this file (AGENTS.md) and project state

### Installed Workflow Skills
- `architect`: Design, decision making, and spec ownership.
- `audit`: Codebase audits and pattern reviews.
- `check`: Implementation verification.
- `debug`: Structured problem solving.
- `develop`: Build guidance and execution.
- `document`: Changelogs and documentation.
- `scope`: Feature scoping and planning.
- `sync`: Context and AGENTS.md management.
- `test`: Testing strategy and writing.

## Current Progress & Decisions
- [x] Basic Docker Compose stack (Redis, Splunk, Tripwire, Gateways, Frontend)
- [x] Splunk platform architecture fix (ARM64 $\rightarrow$ AMD64)
- [x] Tripwire initialization fix (Key generation logic)
- [x] Implemented `/v1/telemetry/logs` (GET/POST) with Splunk HEC integration
- [x] Implemented `/v1/billing/checkout` (POST mock)
- [x] Implemented Granular RBAC Enforcement with Auth0 permissions verification (`require_permission` dependency, Spec 0005)
- [x] Implemented Tenant Token Bucket Rate Limiting & Isolation (`verify_rate_limit` dependency, Spec 0006)
- [x] Implemented Usage Metering Engine (`/v1/usage/summary`, roll-up aggregation, and DLQ routing, Spec 0009)
- [x] Comprehensive test suites for proxy routing, guardrails filters, rate limiting, and metering (237 tests passing, hermetic against fakeredis)
- [x] Implemented API Key Rotation (hashed store, digest index, rotate with grace window, revocation tombstones, Spec 0010)
- [x] Implemented Admin Dashboard (per service health probes, tenant quality/failure/rate counters, Spec 0011)
- [x] Implemented the curated Public API surface (six contract paths at `/docs`, Bearer over key precedence, machine keys on all four contract routes, Spec 0012)
- [x] Remediated the Blocked 0012 review: key writes need `keys:write` and can only grant scopes the caller holds, and the rate limiter no longer trusts a pre-auth `X-Tenant-ID`
- [x] Made the metering DLQ redrivable: the dedup claim and the roll-up are one atomic Redis script, so a failed event no longer stays claimed and dies as a false duplicate (237 tests passing)
- [x] Closed both defects the spec 0013 runtime check found: the 401 redirect latch is bounded by gateway evidence instead of the SDK session flag, with a session notice beside it, and the staleness note is bound on every widget including the keys list (172 portal tests passing)
- [x] Implemented real telemetry aggregation from Redis $\rightarrow$ Splunk (Spec 0014): verified TLS with a collector certificate bearing SANs for `splunk` and `localhost`, health surface with reachability/ship-age/metrics, atomic redrive with 409 lock, DLQ capped with drop counter, and the CA deviation (Splunk's own encrypted CA key is unimplementable; the wrapper generates its own CA with explicit SANs); newline-delimited `{"event":{...}}` envelopes on `/event` index each event individually
- [ ] Implement real billing integration (Stripe)
- [ ] Build out the Java Gateway core logic

## Operational Notes
- `gateway-python` bakes its image at build time and has no source volume, so a running container can be many changes behind. Run `docker compose up -d --build gateway-python` before verifying anything against port 8000, or you will be testing stale code.
- A Homebrew `redis-server` already holds `127.0.0.1:6379`, so the local `.env` value `redis://localhost:6379` points at a different Redis than the containers use (`redis://redis:6379`). Seeding or inspecting usage from the host silently touches the wrong store and every reading looks empty. Drive the metering functions from inside `gateway-python` instead.
- `/dashboard`, `/logs`, and `/keys` sit behind the custom guard in `portal-frontend/src/app/core/guards/auth.guard.ts`, which deliberately does not use the stock Auth0 `authGuardFn`: that one answers authentication from a `user` claim a modern ID token does not carry, so it reports a valid session as signed out and restarts the PKCE exchange on every full page load. Driving those routes in a browser does not need a real Auth0 account in `dev-zymaiayb0afkpn7n`: intercept the Auth0 authorize iframe and the token endpoint, return the gateway's dev token `dev-mock-token` as the access token, and echo the request `nonce` in the id token. The frame postMessage target origin has to be the app origin, because `event.origin` is checked against the Auth0 domain. `/pricing` carries no guard at all, so it renders anonymously.
- Key writes are the one credential operation that can widen a principal's reach, so `POST /v1/keys` requires the `keys:write` scope *and* only grants scopes the caller already holds. A machine key is a real principal, not a lesser one: it manages keys for its own tenant but can never mint a successor more privileged than itself.
- The rate limiter is an app-level dependency, so it runs before route auth. It therefore resolves the principal itself (`request.state.principal_verified`, memoized by `resolve_active_tenant`) rather than reading `X-Tenant-ID` or a literal key table; a pre-auth caller can neither debit a bucket nor learn a tenant's budget from a status code.
- Metering claims an event in `usage:processed_events` and applies its roll-up in one Redis script, because claiming first and writing second stranded a failed event in the ledger, which made the DLQ entry it was routed to redrive as a duplicate and drop the usage permanently. The scripts type check every key before the claim so a rejected event leaves nothing behind. That means the test suite needs `fakeredis[lua]`; install `requirements-dev.txt` or `eval` fails and every metering test fails with it.
- The portal's 401 redirect latch re-arms only when a gateway 2xx proves the session works (`NotificationService.acceptedResponses`), never on the Auth0 SDK's own session flag. That flag answers "do I hold a structurally valid token", not "will the gateway accept it", so with an audience or issuer mismatch it is cheerfully authenticated on a token the gateway refuses: the latch re-armed, the re-minted token was refused in turn, and a browser run measured 261 token exchanges in 8 seconds. Bounding the loop is why `sessionNotice` exists: one automatic redirect, then a visible notice with a user pressed "Sign in again", cleared by the first gateway 2xx.
- The stale-widget map in `gateway-error.interceptor.ts` is keyed on path substrings, so a trailing slash is load-bearing. `/api/v1/keys` has none because the key list is the bare path, and a revoke is a child of it; writing it like the other entries left the keys list with no way to record that its reload had failed, so it kept showing old keys behind no marker. A widget that is tracked but not bound is the silence spec 0013 exists to remove, and `.stale-note` had no CSS at all until 2026-10-05, which is why `/logs` had been rendering it as a bare paragraph.

## Project Memory
For quick recall of recent changes, refer to the git history or the `docs/progress.md` (if created).
