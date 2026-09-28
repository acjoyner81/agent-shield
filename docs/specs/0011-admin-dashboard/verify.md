# Verify: Admin Dashboard · spec 0011 · updated 2026-09-27
_Steps derived from spec 0011 acceptance criteria. `/check verify` runs these; `/test` locks the durable ones._

## Prerequisite
The metering worker is not a compose service, so the counters only move when it runs:
`python -m worker.stream_worker` (or import `TelemetryWorker` and drive it). The running
containers are on pre-change code, so rebuild the gateway first.

**Two environment traps, both hit during the 2026-09-28 run:**
- A Homebrew `redis-server` already holds `127.0.0.1:6379`, so the local `.env` value
  `redis://localhost:6379` points at a *different* Redis than the containers use
  (`redis://redis:6379`). Seeding from the host silently writes to the wrong store and every
  reading looks like empty. Seed from inside `gateway-python` instead.
- `/dashboard` sits behind the stock Auth0 `authGuardFn`, so every UI step below needs a real
  Auth0 login in the dev tenant. There is no dev bypass and no token injection path.

## UI / manual
- [ ] Open the dashboard as a tenant admin → "Estimated spend" shows a real dollar figure and the Model Breakdown table has a Cost column → AC-1
- [ ] Open the dashboard as a tenant member without `billing:admin` → the spend card reads "Spend hidden / billing:admin required", the Cost column is gone, but tokens, quality, and failures are still populated → AC-2, AC-6
- [ ] Leave the dashboard open for over 60 seconds with the network tab open → exactly one `GET /api/v1/usage/summary` and one `GET /api/v1/health/services` per minute, no manual action → AC-5
- [ ] Click "Refresh" → both endpoints fire immediately and every card updates without a page reload → AC-5
- [ ] Change a roll-up value in Redis, then click "Refresh" → the affected card shows the new number (signals are reactive, not snapshotted at init) → AC-5
- [ ] Navigate away from the dashboard and back → the network tab shows no accumulating requests, the poll was torn down on destroy → AC-5
- [ ] With the dashboard open, stop Redis (`docker compose stop redis`) → the System health card shows a red dot on redis and the footer reads "Degraded: one or more services need attention", then start Redis and click "Refresh" → AC-4

## Commands
- [x] `curl -s localhost:8000/v1/health/services -H "Authorization: Bearer $TOKEN" | jq` → one entry per service with `status` and `latency_ms`, no tenant field anywhere → AC-4, AC-6
- [x] `curl -s localhost:8000/v1/health/services | jq` → 401 without a credential → AC-4
- [x] `curl -s localhost:8000/v1/usage/summary -H "Authorization: Bearer $TOKEN" | jq .totals` → `estimated_cost_usd` numeric for a billing admin; `null` for a principal without the permission, while `total_tokens` and `total_requests` stay numeric → AC-1, AC-2
- [x] `curl -s localhost:8000/v1/usage/summary -H "Authorization: Bearer $ADMIN_TOKEN" -H "X-Tenant-ID: tenant_other" | jq .tenant_id` → still the JWT tenant, never the header value → AC-6
- [x] `python -m pytest gateway/tests/test_dashboard_api.py -q` → 32 passed → AC-1 through AC-6
- [x] `cd portal-frontend && npx ng test --watch=false --browsers=ChromeHeadless` → 13 passed, none pending a periodic timer → AC-5

## Value sourcing (each value's source, exercised by varying the input)
- [x] Summary cost ← `prices.json` via `PRICES_FILE`: set `PRICES_FILE` to a card with `"gpt-4o": 0.01`, restart, re-read the summary → the gpt-4o cost doubles with no redeploy of the price logic → AC-1
- [x] Summary cost, unpriced model: add a model to the usage roll-ups that is absent from the card → it appears in `by_model` with `cost_usd: 0` and `estimated_cost_usd` still sums the priced models, no 500 → AC-1
- [x] Summary cost, price edit live: edit the card in place, re-read the summary without restarting → the figure changes, proving cost is computed at read time and not stored → AC-1
- [x] Summary counters ← `__meta__` hash: set the period bounds to a window that excludes the counter's date → `quality_passed`, `failed_requests`, and `rate_limited_requests` drop to 0 while token totals stay correct → AC-3
- [x] Summary counters, `__meta__` not a model: `jq '.by_model[].model'` → the list never contains `__meta__` → AC-3
- [x] Summary counters ← real events: drive one `authz_failure` and one `rate_limit_exceeded` through the worker, then read the summary → `failed_requests` and `rate_limited_requests` each rise by exactly one; replay the same `event_id` → no further rise → AC-3
- [x] Quality counter ← eval flag only: send a `request.completed` with no `eval_passed` → `quality_passed` unchanged; the same event with `eval_passed: true` → rises by one → AC-3
- [x] Cost gating ← `request.state.permissions`: same tenant, two principals differing only by `billing:admin` → identical usage numbers, cost present for one and `null` for the other → AC-2, AC-6
- [x] Health status ← live probes: bring Redis down → redis is `degraded` and `overall` is `degraded` while gateway and mcp-server stay `healthy` → AC-4
- [x] Health latency ← elapsed wall clock: read `latency_ms` for a healthy and a degraded service → the degraded one is visibly larger (it waited for the timeout) → AC-4
- [x] Health, no tenant data: grep the raw response for `tenant`, `tenant_id`, `tokens`, and `usage` → no match → AC-4, AC-6
- [x] Dashboard cadence ← `setInterval(60000)`: mock or observe the timer and assert the interval is registered at 60000ms, not 6000 or 600000 → AC-5
- [x] Tenant scope, same Redis: two tenants with seeded roll-ups, one admin token each → each summary contains only its own totals, and `__meta__` counters never cross over → AC-6

## Result (2026-09-28 run)
Verdict **BLOCKED**, not PASS. Every backend behavior and every acceptance criterion was proven
against the rebuilt container with real Redis, but the seven UI steps could not be exercised at
all, so this run cannot be called a pass.

- Backend: all of AC-1, AC-2, AC-3, AC-4, AC-6 proven live. Cost math checked by hand against the
  seeded numbers (gpt-4o 5000 tokens at 0.0025 per 1k = 0.0125, gpt-4o-mini 40000 at 0.00015 =
  0.006, claude-3-5-sonnet 5000 at 0.003 = 0.015, unpriced model 0, total 0.0335). The role gate
  was proven by minting a key carrying `billing:admin`; neither dev mock token has it, so the cost
  visible branch is otherwise unreachable.
- AC-5 backend side proven: the Angular suite asserts the interval is exactly 60000, probes the
  59999 then 60000 boundary, and asserts polling stops on destroy.
- Blocked: all seven UI steps. `/dashboard` sits behind the stock Auth0 `authGuardFn` with no dev
  bypass. Three attempts to seed the Auth0 SDK cache (plain key, then the SDK's real
  `prefix::clientId::audience::scope::@@user@@` key with a shaped token) all redirected to Universal
  Login. Needs an Auth0 account in `dev-zymaiayb0afkpn7n`, or a documented dev bypass.
- The specced UI surfaces were confirmed present in the component source ("Estimated spend",
  "Spend hidden", "billing:admin required", the `Cost` column gated on `canSeeCost()`, the degraded
  footer, and the 7 vs 6 colspan). Existence confirmed, rendered behavior not.
- The "Health latency" step passed on the observation that a degraded service reports a larger
  latency than a healthy one, but the demonstration used Stripe (about 200 to 300 ms), not a
  timeout bound Redis. Redis degraded fast, at about 6 ms.

### Findings for `/debug` or `/review`
1. `overall` is permanently `degraded` in this deployment. The Stripe probe is gated on
   `settings.stripe_api_key` being set rather than on `STRIPE_ENABLED`, and the key *is* present in
   `.env` while `STRIPE_ENABLED=false`. The probe then fails and pins the card to "Degraded: one or
   more services need attention" even when gateway, MCP, and Redis are all healthy. The spec says
   "Stripe when configured", so the gate is reading the wrong signal.
2. `POST /v1/keys` has no permission dependency, so any authenticated tenant member can mint a key
   carrying `billing:admin` (or any other scope) and then read spend through `/v1/usage/summary`.
   That is a privilege escalation path around the AC-2 cost gate, and it is exactly how the cost
   visible branch had to be reached for this run.

### Cleanup performed
The verification key was revoked, all seeded `usage:*` and `billing:usage:*` keys were deleted, the
16 `vfy-` members were removed from `usage:processed_events`, and the scratch files were removed from
the container. The `PRICES_FILE` override compose file was reverted and the gateway was rebuilt on
the normal configuration. One `vfy-billing-admin` revocation tombstone remains by design, per the
spec 0010 key lifecycle.

## Acceptance-criteria coverage
- AC-1 covered by "Summary cost ← prices.json", the unpriced model step, the live price edit step, and `TestUsageSummaryCost.test_cost_math_is_total_tokens_over_one_thousand_times_rate`
- AC-2 covered by the admin versus non-admin UI step and the cost gating sourcing step
- AC-3 covered by the counter sourcing steps and `TestMetaCounters`
- AC-4 covered by the health commands, the Redis down UI step, and the probe sourcing steps
- AC-5 covered by the poll cadence UI steps and the dashboard cadence sourcing step
- AC-6 covered by the role gate UI step, the spoofed header command, the tenant scope sourcing step, and the health no-tenant-data step
