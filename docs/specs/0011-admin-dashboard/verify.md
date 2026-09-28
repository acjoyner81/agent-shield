# Verify: Admin Dashboard · spec 0011 · updated 2026-09-27
_Steps derived from spec 0011 acceptance criteria. `/check verify` runs these; `/test` locks the durable ones._

## Prerequisite
The metering worker is not a compose service, so the counters only move when it runs:
`python -m worker.stream_worker` (or import `TelemetryWorker` and drive it). The running
containers are on pre-change code, so rebuild the gateway first.

## UI / manual
- [ ] Open the dashboard as a tenant admin → "Estimated spend" shows a real dollar figure and the Model Breakdown table has a Cost column → AC-1
- [ ] Open the dashboard as a tenant member without `billing:admin` → the spend card reads "Spend hidden / billing:admin required", the Cost column is gone, but tokens, quality, and failures are still populated → AC-2, AC-6
- [ ] Leave the dashboard open for over 60 seconds with the network tab open → exactly one `GET /api/v1/usage/summary` and one `GET /api/v1/health/services` per minute, no manual action → AC-5
- [ ] Click "Refresh" → both endpoints fire immediately and every card updates without a page reload → AC-5
- [ ] Change a roll-up value in Redis, then click "Refresh" → the affected card shows the new number (signals are reactive, not snapshotted at init) → AC-5
- [ ] Navigate away from the dashboard and back → the network tab shows no accumulating requests, the poll was torn down on destroy → AC-5
- [ ] With the dashboard open, stop Redis (`docker compose stop redis`) → the System health card shows a red dot on redis and the footer reads "Degraded: one or more services need attention", then start Redis and click "Refresh" → AC-4

## Commands
- [ ] `curl -s localhost:8000/v1/health/services -H "Authorization: Bearer $TOKEN" | jq` → one entry per service with `status` and `latency_ms`, no tenant field anywhere → AC-4, AC-6
- [ ] `curl -s localhost:8000/v1/health/services | jq` → 401 without a credential → AC-4
- [ ] `curl -s localhost:8000/v1/usage/summary -H "Authorization: Bearer $TOKEN" | jq .totals` → `estimated_cost_usd` numeric for a billing admin; `null` for a principal without the permission, while `total_tokens` and `total_requests` stay numeric → AC-1, AC-2
- [ ] `curl -s localhost:8000/v1/usage/summary -H "Authorization: Bearer $ADMIN_TOKEN" -H "X-Tenant-ID: tenant_other" | jq .tenant_id` → still the JWT tenant, never the header value → AC-6
- [ ] `python -m pytest gateway/tests/test_dashboard_api.py -q` → 32 passed → AC-1 through AC-6
- [ ] `cd portal-frontend && npx ng test --watch=false --browsers=ChromeHeadless` → 13 passed, none pending a periodic timer → AC-5

## Value sourcing (each value's source, exercised by varying the input)
- [ ] Summary cost ← `prices.json` via `PRICES_FILE`: set `PRICES_FILE` to a card with `"gpt-4o": 0.01`, restart, re-read the summary → the gpt-4o cost doubles with no redeploy of the price logic → AC-1
- [ ] Summary cost, unpriced model: add a model to the usage roll-ups that is absent from the card → it appears in `by_model` with `cost_usd: 0` and `estimated_cost_usd` still sums the priced models, no 500 → AC-1
- [ ] Summary cost, price edit live: edit the card in place, re-read the summary without restarting → the figure changes, proving cost is computed at read time and not stored → AC-1
- [ ] Summary counters ← `__meta__` hash: set the period bounds to a window that excludes the counter's date → `quality_passed`, `failed_requests`, and `rate_limited_requests` drop to 0 while token totals stay correct → AC-3
- [ ] Summary counters, `__meta__` not a model: `jq '.by_model[].model'` → the list never contains `__meta__` → AC-3
- [ ] Summary counters ← real events: drive one `authz_failure` and one `rate_limit_exceeded` through the worker, then read the summary → `failed_requests` and `rate_limited_requests` each rise by exactly one; replay the same `event_id` → no further rise → AC-3
- [ ] Quality counter ← eval flag only: send a `request.completed` with no `eval_passed` → `quality_passed` unchanged; the same event with `eval_passed: true` → rises by one → AC-3
- [ ] Cost gating ← `request.state.permissions`: same tenant, two principals differing only by `billing:admin` → identical usage numbers, cost present for one and `null` for the other → AC-2, AC-6
- [ ] Health status ← live probes: bring Redis down → redis is `degraded` and `overall` is `degraded` while gateway and mcp-server stay `healthy` → AC-4
- [ ] Health latency ← elapsed wall clock: read `latency_ms` for a healthy and a degraded service → the degraded one is visibly larger (it waited for the timeout) → AC-4
- [ ] Health, no tenant data: grep the raw response for `tenant`, `tenant_id`, `tokens`, and `usage` → no match → AC-4, AC-6
- [ ] Dashboard cadence ← `setInterval(60000)`: mock or observe the timer and assert the interval is registered at 60000ms, not 6000 or 600000 → AC-5
- [ ] Tenant scope, same Redis: two tenants with seeded roll-ups, one admin token each → each summary contains only its own totals, and `__meta__` counters never cross over → AC-6

## Acceptance-criteria coverage
- AC-1 covered by "Summary cost ← prices.json", the unpriced model step, the live price edit step, and `TestUsageSummaryCost.test_cost_math_is_total_tokens_over_one_thousand_times_rate`
- AC-2 covered by the admin versus non-admin UI step and the cost gating sourcing step
- AC-3 covered by the counter sourcing steps and `TestMetaCounters`
- AC-4 covered by the health commands, the Redis down UI step, and the probe sourcing steps
- AC-5 covered by the poll cadence UI steps and the dashboard cadence sourcing step
- AC-6 covered by the role gate UI step, the spoofed header command, the tenant scope sourcing step, and the health no-tenant-data step
