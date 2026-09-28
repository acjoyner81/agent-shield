# 0011. Admin Dashboard

**Date**: 2026-09-24
**Status**: In Progress

## Summary

This feature finishes the tenant admin dashboard so it shows real numbers. Usage totals and the model breakdown already render live data from the usage summary endpoint. This slice moves the remaining cards off demo data: estimated cost, quality pass rate, failed and rate limited requests, and system health. Cost is computed at read time from the token rollups against a price list, and the dashboard polls every minute with a manual refresh. Every number stays scoped to the signed in tenant.

## Requirements

**User stories**:
- As a tenant administrator, I want to see my estimated spend and my usage on one screen so that I can keep the LLM budget predictable.
- As a tenant administrator, I want to see failures, rate limits, and service health so that I can react before an outage reaches my agents.
- As a gateway operator, I want the health and usage surfaces to stay tenant scoped so that no plan leaks another tenant's numbers.

**Acceptance criteria** (the contract):
- **AC-1**: `GET /v1/usage/summary` returns `estimated_cost_usd` on the totals and `cost_usd` on each model, computed at read time by multiplying the model's total tokens by that model's rate in `gateway/config/prices.json` (per 1k tokens, defaulting to 0 when a model has no rate).
- **AC-2**: The cost fields are present only for a principal whose permissions include `billing:admin`; for every other authenticated principal they are `null`.
- **AC-3**: `GET /v1/usage/summary` includes the tenant scoped counters `quality_passed`, `failed_requests`, and `rate_limited_requests` for the requested period, aggregated from telemetry events carried on the roll-up keys.
- **AC-4**: `GET /v1/health/services` returns one entry per service (gateway, MCP server, Redis; Stripe when configured) with a status and latency, and exposes no tenant data.
- **AC-5**: The Angular dashboard polls the usage summary and health services endpoints every 60 seconds, supports a manual refresh button, and both signals update reactively.
- **AC-6**: Every dashboard value is tenant scoped; a non `billing:admin` principal sees usage, quality, and failures but never estimated cost.

## Decision

**Chosen option**: Extend the existing usage summary endpoint plus a new health services endpoint, with read time cost estimation and a 60 second poll.

Complete the dashboard in place: extend `GET /v1/usage/summary` with cost, quality, and failure counters; add `GET /v1/health/services`; read prices from `gateway/config/prices.json`; poll every 60 seconds with a manual refresh; gate cost behind `billing:admin`.

**Implementation skills**: no new community skills are required. The build uses the existing Angular, FastAPI, and Redis conventions already in the repository.

## Feature design

**Data model sketch**:

No persistent schema changes. Cost, quality, and failure counters are read time arithmetic over data that already exists.

- `gateway/config/prices.json`: the currency source for AC-1, a map of model name to USD per 1k tokens, e.g. `{"gpt-4o": 0.0025, "claude-3-5-sonnet": 0.003}`. Loaded once at startup into `config/settings.py` (or a small `gateway/pricing.py` helper) with a `PRICES_FILE` env override.
- Extension of the daily roll-up keys written by the metering worker into `usage:daily:{tenant}:{date}:__meta__` (a hash) with `quality_passed`, `failed_requests`, and `rate_limited_requests` counters, incremented as the worker consumes the matching telemetry events.

**State transitions**:
Not applicable, no state machine is introduced.

**API surface**:

| Endpoint | Method | Key inputs | Key outputs | Auth | Key errors |
|---|---|---|---|---|---|
| /v1/usage/summary | GET | start_date (opt), end_date (opt) | existing totals/by_model, plus estimated_cost_usd, cost_usd per model, quality_passed, failed_requests, rate_limited_requests | bearer or API key | 401, 403 |
| /v1/health/services | GET | none | services: [{name, status, latency_ms}] | bearer or API key | 401 |

**Value sourcing** (every value each action produces names its source):

| Action | Value produced / displayed | Source |
|---|---|---|
| Summary | `estimated_cost_usd`, `cost_usd` | derived: per model `total_tokens / 1000 * prices.json[model]`, summed; `prices.json` loaded from `PRICES_FILE` |
| Summary | `quality_passed`, `failed_requests`, `rate_limited_requests` | summed from `usage:daily:{tenant}:{date}:__meta__` hash fields (date filtered by start/end) |
| Summary | gating of cost fields | `request.state.permissions` contains `billing:admin` |
| Health | per service `status` | live probe: Redis `PING`, MCP `/health` over HTTP, gateway self status, Stripe API ping when configured |
| Health | per service `latency_ms` | elapsed wall clock around each probe |
| Dashboard | all card values | `TelemetryService` signals fed by the two GET endpoints |
| Dashboard | auto refresh cadence | `setInterval(60000)` in the service, plus a manual refresh method |

**Key invariants**:
- Cost is computed at read time, never stored; any price list change applies to the next request.
- A model with no entry in `prices.json` contributes 0 cost, never an error.
- The `__meta__` roll-up key is keyed by tenant and date exactly like the model keys, so the existing `scan` based summary filter covers it unchanged.
- The health endpoint probes infrastructure only; no tenant is referenced in a probe or returned in the response.

**Security model**:
Tenant isolation is inherited from `get_verified_tenant`: the summary and health endpoints bind the tenant from the authenticated JWT or API key and return only that tenant's numbers. Cost visibility is the one role gate: it follows the `billing:admin` permission from spec 0005, enforced with the existing `require_permission` machinery at the endpoint. Health is authenticated to any tenant member but returns service level status only, with no tenant data. No compliance scope beyond the existing SOC 2 telemetry posture.

**Configuration required**:
- `PRICES_FILE`: path to the price map JSON, default `config/prices.json`.

**Critical test scenarios**:
- Happy path: a `billing:admin` tenant calls the summary for the current month and sees totals, per model usage, `estimated_cost_usd`, quality, and failure counters populated from seeded roll-ups, verifies **AC-1**, **AC-3**, **AC-6**
- Role gate: a tenant member without `billing:admin` gets `null` cost fields but numeric usage and failure counters, verifies **AC-2**, **AC-6**
- Health: gateway, MCP, and Redis respond, `/v1/health/services` lists each with a status and latency; Redis brought down, its status flips to degraded, verifies **AC-4**
- Tenant scope: tenant A and tenant B tokens both call the summary; each response contains only its own tenant's numbers, verifies **AC-6**
- Unpriced model: a model absent from `prices.json` appears with 0 cost and everything else still works, verifies **AC-1**
- Refresh: the dashboard shows the same card values while the poll cadence is mocked to 60 seconds and manual refresh returns fresh data, verifies **AC-5**

## Build plan

Ordered as thin vertical slices per the project default (Tracer Bullet), standing up one real card of data end to end before the next.

- [x] 1. Add `gateway/config/prices.json` with an initial price map and a singleton loader (honoring `PRICES_FILE`), the currency source for cost, satisfies **AC-1**
- [x] 2. Extend the metering worker (`worker/stream_worker.py` plus `gateway/metering.py`) to consume `agentshield.security.authz_failure`, `agentshield.security.rate_limit_exceeded`, and `agentshield.telemetry.request.completed` events and increment the per tenant per date `__meta__` counters, satisfies **AC-3**
- [x] 3. Extend `get_tenant_usage_summary` and its response model with cost plus the three counters, gating the cost fields on `billing:admin` via `request.state.permissions`, satisfies **AC-1**, **AC-2**, **AC-6**
- [x] 4. Implement `GET /v1/health/services` in `gateway/main.py` that probes Redis, the MCP server, and itself and returns status plus latency per service, satisfies **AC-4**
- [x] 5. Update the Angular `TelemetryService` (summary type, health signal) and the dashboard component so the cost, quality, failure, and health cards read live values, plus a 60 second poll and manual refresh, satisfies **AC-5**, **AC-6**
- [x] 6. Add backend tests (`gateway/tests/test_dashboard_api.py`) covering cost math, the role gate, tenant scope, health probes, and unpriced models, and extend the portal spec with the refresh contract, satisfies **AC-1** through **AC-6**

## Consequences

**Positive**:
- The dashboard becomes a real operating surface instead of a mock.
- Cost appears without new storage; the price list is a simple file change.
- Failures and health surface early, which is the point of a control plane.

**Negative / tradeoffs**:
- The quality pass rate counts only events that carry an eval flag; until an LLM judge exists it reads zero, so that card will look empty.
- A 60 second poll adds a small request every minute per open dashboard tab.
- Two extra roll-up increments run per event in the metering worker.

**Neutral**:
- `prices.json` becomes rate card data a non engineer can edit.
- The billing `billing:admin` permission now gates a read surface, new for this account role.

## Follow-up

- [ ] Design and build an eval judge that stamps `eval_passed` on `request.completed` events; without it the quality pass rate card has no signal source.
- [ ] Consider surfacing the price list in the portal so admins can see the rate card behind the cost figure.

## Rationale

Reasoning, options, and the decision record: see [rationale.md](rationale.md).