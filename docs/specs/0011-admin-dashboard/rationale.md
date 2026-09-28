# 0011. Admin Dashboard — Rationale

## Context

The original scope feature intent is that a tenant administrator can see their daily token consumption and estimated costs on the dashboard. The Angular dashboard already ships a full visual shell: metric cards for total tokens, requests, quality pass rate, failed or rate limited requests, a token usage by model chart, a system health list, and a model breakdown table.

Part of that shell is real and part is demo. The token totals, request counts, and model breakdown already render live values from `GET /v1/usage/summary`. The rest is stubbed: the cost card does not exist, quality pass rate and failed or rate limited are computed from five hardcoded demo log rows, and the system health list is a fixed table of percentages. A dashboard that claims to be an operator surface but shows invented numbers erodes the product's credibility for the small businesses that rely on it.

Three gaps are load bearing. First, there is no cost surface anywhere in the platform even though the scope names estimated costs as the feature's reason to exist. Second, failures and rate limits are measurable in telemetry but nothing aggregates them for a tenant view. Third, service health is asserted, not probed. Filling these gaps turns the dashboard into the predictable cost and answer quality view the product promises.

## Options considered

### Option 1: Extend the existing surfaces (Recommended)

Add cost, quality, and failure counters to `GET /v1/usage/summary`, add a new `GET /v1/health/services` for the health card, and poll both every 60 seconds from the Angular service.

**Pros**:
- Reuses the summary call the dashboard already makes; one fetch drives the whole screen.
- No new storage; cost and counters derive from the roll-ups that exist.
- Smallest surface that completes every card.

**Cons**:
- The summary response grows; cost carries a new role gate on an existing endpoint.
- The quality counter depends on an eval signal that does not exist yet.

### Option 2: Separate dashboard specific endpoints

Add dedicated `GET /v1/dashboard` and `GET /v1/health/services` endpoints that aggregate whatever the cards need.

**Pros**:
- Keep the usage summary API stable and focused.
- A dedicated endpoint can shape the payload exactly for the UI.

**Cons**:
- Duplicates the aggregation work that `/v1/usage/summary` already does.
- Two more endpoints to secure and document for marginal gain.

### Option 3: Read quality and failures client side from logs

Keep the current approach: fetch the last 50 log rows and compute pass rate and failures in the component.

**Pros**:
- No backend work for two cards.

**Cons**:
- Client side math over a 50 row window is not an accurate period total.
- Repeats the current stub's weakness with slightly realer inputs.

## Rationale

Option 1 wins because the whole feature is about making an existing screen real, and its data is one phone call away. The summary endpoint already does the tenant scoped, period filtered aggregation the cards need; adding the counters and cost to it keeps one contract for the whole screen. Cost is read time arithmetic over the token roll-ups, which matches the existing "compute at read time" stance in spec 0009 and keeps the price list a plain file edit. The role gate on cost follows the `require_permission` convention from spec 0005, so the dashboard does not invent an auth model.

The health card is the one genuinely new surface, and a tiny one. A dedicated services probe endpoint is the right shape because health is infrastructure level, not tenant scoped, and it must not be conflated with the tenant usage contract. The 60 second poll is the boring default for a control panel; streaming would add an SSE endpoint for a screen that does not need sub minute truth.

The quality counter is the honest weak spot. There is no real eval signal in the platform today, so the counter can only count events that happen to carry an eval flag. The spec fronts this as a follow up feature rather than pretending the judge exists.

## References

**Project sources**:
- The scope feature intent: `docs/scope/scope.md`, low level question 8 Admin Dashboard
- Spec 0005 RBAC `require_permission` for the cost gate
- Spec 0008 event schema for the telemetry event types (authz failure, rate limit exceeded, request completed)
- Spec 0009 usage metering for the roll-up keys the counters extend
- The Angular dashboard and telemetry service in `portal-frontend/src/app/features/dashboard/`

**Practices & standards**:
- Read time derived values instead of stored computed values
- Periodic polling of a signal with manual refresh as the control panel default