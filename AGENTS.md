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
- [ ] Implement real billing integration (Stripe)
- [ ] Implement real telemetry aggregation from Redis $\rightarrow$ Splunk
- [ ] Build out the Java Gateway core logic

## Operational Notes
- `gateway-python` bakes its image at build time and has no source volume, so a running container can be many changes behind. Run `docker compose up -d --build gateway-python` before verifying anything against port 8000, or you will be testing stale code.
- A Homebrew `redis-server` already holds `127.0.0.1:6379`, so the local `.env` value `redis://localhost:6379` points at a different Redis than the containers use (`redis://redis:6379`). Seeding or inspecting usage from the host silently touches the wrong store and every reading looks empty. Drive the metering functions from inside `gateway-python` instead.
- The portal dashboard sits behind the stock Auth0 `authGuardFn` with no dev bypass, so driving the UI in a browser needs a real Auth0 account in `dev-zymaiayb0afkpn7n`. The API it calls is reachable without a browser.
- Key writes are the one credential operation that can widen a principal's reach, so `POST /v1/keys` requires the `keys:write` scope *and* only grants scopes the caller already holds. A machine key is a real principal, not a lesser one: it manages keys for its own tenant but can never mint a successor more privileged than itself.
- The rate limiter is an app-level dependency, so it runs before route auth. It therefore resolves the principal itself (`request.state.principal_verified`, memoized by `resolve_active_tenant`) rather than reading `X-Tenant-ID` or a literal key table; a pre-auth caller can neither debit a bucket nor learn a tenant's budget from a status code.
- Metering claims an event in `usage:processed_events` and applies its roll-up in one Redis script, because claiming first and writing second stranded a failed event in the ledger, which made the DLQ entry it was routed to redrive as a duplicate and drop the usage permanently. The scripts type check every key before the claim so a rejected event leaves nothing behind. That means the test suite needs `fakeredis[lua]`; install `requirements-dev.txt` or `eval` fails and every metering test fails with it.

## Project Memory
For quick recall of recent changes, refer to the git history or the `docs/progress.md` (if created).
