# AgentShield Enterprise - Project Context

## Project Overview
AgentShield is a multi-tenant AI gateway and agent platform for small businesses. It focuses on predictable LLM costs, secure tool access, reliable routing, and measurable answer quality.

## Tech Stack
- **Frontend:** Angular (Port 4200)
- **Python Gateway:** FastAPI (Port 8000)
- **Java Gateway:** Spring Boot (Port 8080)
- **MCP Server:** Java/Spring Boot (Port 8081)
- **Infrastructure:** Redis, Splunk, Tripwire, Docker Compose

## Build approach
Tracer Bullet (vertical slices ship real value early).

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

## Agent skills

### Issue tracker

Issues and specs live as GitHub issues on `acjoyner81/agent-shield`, driven with the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context: one `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Portal UI conventions

- [docs/portal-ui-design.md](docs/portal-ui-design.md): current portal layout, headers, CSS tokens, responsive behavior, and interaction conventions for future Angular work.

## Current Progress & Decisions
- [x] Basic Docker Compose stack (Redis, Splunk, Tripwire, Gateways, Frontend)
- [x] Splunk platform architecture fix (ARM64 $\rightarrow$ AMD64)
- [x] Tripwire initialization fix (Key generation logic)
- [x] Implemented `/v1/telemetry/logs` (GET/POST) with Splunk HEC integration
- [x] Implemented `/v1/billing/checkout` (POST mock); note the checkout route needs a Stripe *price* id (`price_...`), never a product (`prod_...`), and returns 503 "Billing is not configured" until `STRIPE_PRICE_STARTER/PRO/ENTERPRISE` are set
- [x] Implemented Granular RBAC Enforcement with Auth0 permissions verification (`require_permission` dependency, Spec 0005), verified 2026-10-08 against a real Auth0 access token: the namespaced permissions claim is read, an unheld scope returns 403 with the missing scope named, and the Spec 0005 AC-4 gap is closed so an `authz_failure` event now carries `required_permission` at the top level (it was emitted without it before)
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
- [x] Closed the Spec 0014 verify gaps on 2026-10-07: the idle stack health defect fixed, the lost redrive interruption and collector rejection tests restored, and the spec's AC-2/AC-7 text amended to match the stack (408 backend tests, 172 portal tests)
- [x] Implemented the Java Gateway core (Spec 0015): `gateway-java` is a JWT verifying edge proxy that routes `/api/v1/**` and `/v1/chat/**` to Python and `/mcp/**` to the MCP server, emits telemetry only for its own layer (one `authz_failure` on a rejection, one `request.completed` only when Python did not answer), registers on the health surface, and holds no rate limiter so Python's 429 envelope passes through unchanged; `mcp-server` serves the Spring AI streamable transport at `/mcp/v1` behind its own JWT check with `/rpc` retired, and Python `tools/execute` calls it with a cached Auth0 client credentials token and never forwards the caller JWT. Verified 2026-10-16 against the live stack (every umbrella AC plus each child spec's G and M acceptance criteria)
- [x] Closed the third Spec 0013 defect the 2026-10-19 browser re-verify found (the interceptor counted Angular's send event as a gateway success, so the bounded redirect latch still looped) and passed verification: a persistently rejected token now yields 3 redirects total, stable at 10s, with the `role="alert"` session notice and sign in control, and staleness notes bound on the dashboard usage and health widgets and the keys list (181 portal tests passing)
- [ ] Implement real billing integration (Stripe)

## Operational Notes
- `gateway-python` bakes its image at build time and has no source volume, so a running container can be many changes behind. Run `docker compose up -d --build gateway-python` before verifying anything against port 8000, or you will be testing stale code. `portal-frontend` is the same: it bakes an nginx build, so run `docker compose up -d --build portal-frontend` after any template or CSS change or you will look at the old page.
- The portal calls the gateway through nginx, and nginx does **not** forward `/api/` unchanged: `portal-frontend/nginx.conf` uses `location /api/ { proxy_pass http://gateway-python:8000/; }`, and the trailing slash on `proxy_pass` strips the prefix, so `/api/v1/help/assistant` arrives at the gateway as `/v1/help/assistant` (proven by watching the gateway log the stripped path). The portal therefore reaches the **bare** mounts, not the `/api` ones. `gateway/main.py` still mounts billing, metering, keys, and the help assistant twice (bare and under `prefix="/api"`) so a direct caller on port 8000 can use either, and `gateway/tests/test_portal_integration.py` pins the aliases — but the `/api` mounts are not what the containerized portal exercises. Do not "fix" a portal 404 by assuming the `/api` prefix survives the proxy; check which path the gateway actually logged.
- The Help Center artifact (`docs/help-center/build.py`) holds each section twice: once under `documents[].sections` for the portal to render, and once in the flat `sections[]` for the assistant's retrieval. They must be built from one object, because the two views drifted: `documents[].sections` carried the raw parsed shape (a line-list `body`, no `blocks`), so the portal painted headings with empty bodies while the assistant answered fine from the flat list — and the nav links resolved to real anchors, so nothing looked broken. Rebuild with `.venv/bin/python docs/help-center/build.py` after any Markdown, manifest, or public-schema change; it writes both `gateway/help_center_content.json` and `portal-frontend/public/help-center-content.json`, and `gateway/tests/test_help_center_build.py` fails if a rendered section has no blocks or the two copies differ.
- A Homebrew `redis-server` already holds `127.0.0.1:6379`, so the local `.env` value `redis://localhost:6379` points at a different Redis than the containers use (`redis://redis:6379`). Seeding or inspecting usage from the host silently touches the wrong store and every reading looks empty. Drive the metering functions from inside `gateway-python` instead.
- `/dashboard`, `/logs`, and `/keys` sit behind the custom guard in `portal-frontend/src/app/core/guards/auth.guard.ts`, which deliberately does not use the stock Auth0 `authGuardFn`: that one answers authentication from a `user` claim a modern ID token does not carry, so it reports a valid session as signed out and restarts the PKCE exchange on every full page load. Driving those routes in a browser does not need a real Auth0 account in `dev-zymaiayb0afkpn7n`: intercept the Auth0 authorize iframe and the token endpoint, return the gateway's dev token `dev-mock-token` as the access token, and echo the request `nonce` in the id token. The frame postMessage target origin has to be the app origin, because `event.origin` is checked against the Auth0 domain. `/pricing` carries no guard at all, so it renders anonymously.
- The authenticated app shell fetches `/api/v1/health/services` immediately and every 60 seconds, so its status badge stays current on routes beyond the dashboard. The dashboard owns the usage refresh timer; the anonymous `/pricing` route does not poll health.
- Key writes are the one credential operation that can widen a principal's reach, so `POST /v1/keys` requires the `keys:write` scope *and* only grants scopes the caller already holds. A machine key is a real principal, not a lesser one: it manages keys for its own tenant but can never mint a successor more privileged than itself.
- The rate limiter is an app-level dependency, so it runs before route auth. It therefore resolves the principal itself (`request.state.principal_verified`, memoized by `resolve_active_tenant`) rather than reading `X-Tenant-ID` or a literal key table; a pre-auth caller can neither debit a bucket nor learn a tenant's budget from a status code.
- Metering claims an event in `usage:processed_events` and applies its roll-up in one Redis script, because claiming first and writing second stranded a failed event in the ledger, which made the DLQ entry it was routed to redrive as a duplicate and drop the usage permanently. The scripts type check every key before the claim so a rejected event leaves nothing behind. That means the test suite needs `fakeredis[lua]`; install `requirements-dev.txt` or `eval` fails and every metering test fails with it.
- The portal's 401 redirect latch re-arms only when a gateway 2xx proves the session works (`NotificationService.acceptedResponses`), never on the Auth0 SDK's own session flag. That flag answers "do I hold a structurally valid token", not "will the gateway accept it", so with an audience or issuer mismatch it is cheerfully authenticated on a token the gateway refuses: the latch re-armed, the re-minted token was refused in turn, and a browser run measured 261 token exchanges in 8 seconds. Bounding the loop is why `sessionNotice` exists: one automatic redirect, then a visible notice with a user pressed "Sign in again", cleared by the first gateway 2xx.
- The interceptor's success counter advances only on a real `HttpResponse` (`event.type === HttpEventType.Response`). Angular also emits an `HttpSentEvent` the moment a request goes out, and `tap`'s `next` fires on it before any status exists; counting that as a success re-armed the 401 redirect latch and cleared the session notice on calls the gateway then refused, which is how the re-auth loop survived its first fix. Locked in `gateway-error.interceptor.spec.ts` by pushing the send event by hand, because the Angular test backend's `flush()` emits only a response.
- The stale-widget map in `gateway-error.interceptor.ts` is keyed on path substrings, so a trailing slash is load-bearing. `/api/v1/keys` has none because the key list is the bare path, and a revoke is a child of it; writing it like the other entries left the keys list with no way to record that its reload had failed, so it kept showing old keys behind no marker. A widget that is tracked but not bound is the silence spec 0013 exists to remove, and `.stale-note` had no CSS at all until 2026-10-05, which is why `/logs` had been rendering it as a bare paragraph.
- A standalone component that lists `HttpClientModule` in its own `imports` gets a second, component scoped `HttpClient` that has none of the app level functional interceptors from `provideHttpClient(withInterceptors(...))` in `app.config.ts`. The Auth0 interceptor never runs, so the request leaves with no `Authorization` header and the gateway answers 401, while calls made from a root service (health, usage, logs) keep working because they receive the configured `HttpClient`. That is how `/evaluations` and `/agent` polled `/v1/evals/recent` and `/v1/agent/executions` with no token while `/v1/health/services` returned 200 in the same browser session. Do not add `HttpClientModule` to a standalone component's `imports`: inject `HttpClient` and let the root provider supply it. `live-tracking-auth.spec.ts` fails if that import is reintroduced.
- The health entry's ship staleness only counts when work is waiting: `_undelivered_events` in `gateway/main.py` sums consumer group lag plus claimed but unacknowledged entries before a stale attempt becomes a failure. `XLEN telemetry:queue` is untrimmed history that only ever grows (1000+ is normal), so it is never a backlog signal, and an idle queue past the 300 second window is healthy by contract (Spec 0014 AC-7).
- A machine to machine (client credentials) token never sees a Post Login Action, so it needs its own Auth0 Action on the Client Credentials Exchange trigger (`auth0/actions/add-m2m-tenant-claims.js`, deployed as `agentshield-m2m-tenant-claims`). That event exposes the app's metadata at `event.client.metadata`, not `client_metadata`; the Action stamps `tenant_id` plus the namespaced `permissions` claim so a machine caller resolves to a real principal. A client credentials token carries no `scope`, so permissions come only from the Action. Edit the Action in `auth0/actions/`, then redeploy it in Auth0.
- Clearing tripwire file integrity violations is `docker compose exec tripwire /approve.sh --yes` after reading the newest report in the container with `twprint -m r -r <report>`. Docker Desktop file sharing hiccups rewrite inodes while preserving content and mtime, which surfaces as roughly 110 violations at once; check the property column before trusting the count, then approve. A policy change is the exception: it invalidates the baseline on purpose and needs `docker compose run --rm -e TRIPWIRE_REBUILD_BASELINE=1 tripwire` (see `docs/runbooks/tripwire-rebaseline-runbook.md`).

## Project Memory
For quick recall of recent changes, refer to the git history or the `docs/progress.md` (if created).


## Meta Hackathon 2026 - Integration Goals
- [ ] Implement Meta Model API Provider Router in `gateway-python` (Muse Spark 1.3, Muse Code, SAM 3.1)
- [ ] Add explicit Splunk HEC event triggers for OWASP LLM prompt injection and PII redaction blocks
- [ ] Connect Tripwire file drift alerts to the agentic tool execution loop (e.g., when Muse Code attempts local edits)
- [ ] Create Red Team testing harness endpoints for live hackathon demonstration
