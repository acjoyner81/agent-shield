# 0013. UI error handling, reasoning

Decision record. `/develop` does not read this file; the build spec is in `index.md`.

## Context

The Angular portal makes seven calls to the Python gateway across three services. None of them surfaces a failure to the user. `telemetry.service.ts` (3) and `keys.service.ts` (2 of 3) pass an `error` callback that writes a `console.warn` and returns, leaving the previous values on screen; `billing.service.ts` passes `error: () => undefined`; `keys.service.ts` returns the key creation observable unwrapped, and `keys.component.ts` subscribes to it with a `next` handler only; and the key delete call discards the response entirely, so it has no error path at all. The two calls in `auth.service.ts` reach the Java gateway and Spring Boot rather than this gateway, which is why they are out of scope for this standard.

The net effect is that a tenant whose daily budget is exhausted, or whose token lacks `keys:write`, sees an interface that behaves exactly as though the request succeeded.

Two properties of this gateway make that worse than an ordinary missing error handler.

First, the rate limiter is an app level dependency (`dependencies=[Depends(verify_rate_limit)]` on the `FastAPI` constructor in `gateway/main.py`), so it runs before route authentication and applies to every path. A 429 can arrive on any call, including the dashboard's background poll of usage and health, which runs every 60 seconds. A policy that surfaces 429 to the user on every response would emit a toast roughly every 30 seconds to someone who did nothing.

Second, `TelemetryService.logs` is initialised with five hardcoded `EVT-*` rows, and a failed fetch leaves them in place. `logs.component.ts` goes further and hardcodes "Events today 18,420" and "Avg. latency 184ms" directly in the template, next to a "Download CSV" button that has no handler. The portal therefore renders a complete, plausible, entirely fictional dashboard whenever the gateway is down. For a product sold on observability and guardrails, that is the most damaging possible default, and it is the same class of defect as the "PII redaction: Enabled" row corrected earlier: a screen asserting a capability the system does not have.

The failure detail is also not one shape, which constrains any mapper. A 400 from the guardrail filter and a 403 from `require_permission` both carry a string `detail`. A 402 raised by the portal facing route carries a bare string, while the same status raised by `completion_proxy` carries a dict of `current_spend_usd` and `max_budget_usd`. A schema failure returns FastAPI's default list of `{loc, msg, type}` objects. These are pinned in `gateway/tests/test_portal_integration.py`, which exists because the public API suite only ever exercised the `/v1/...` contract paths and never the `/api/...` aliases the portal actually calls.

One scope fact limits what this decision can prove. No portal surface calls `/v1/chat/completions` or `/v1/tools/execute`; the routes are dashboard, logs, keys, and pricing. A 400 guardrail trip is therefore unreachable from the user interface today. The most security relevant mapping in the proposed design can be specified and can be unit tested, but it cannot be exercised end to end until a surface exists that sends a model request.

## Options considered

### Option 1: A global interceptor that renders directly

One `HttpInterceptorFn` that inspects every `HttpErrorResponse` and pushes a banner, toast, or modal based on the status code.

**Pros**:
- Fewest files and the least per component code. A new surface inherits handling for free.
- One obvious place to look when asking where errors are handled.
- Fast to sketch, and the smallest conceptual model for a developer new to the app.

**Cons**:
- The interceptor observes a response, not its origin, so it cannot tell a background poll from a click. Placement is a guess, and the guess is wrong precisely for the app level 429 the gateway emits on every route.
- Rendering inside an interceptor couples data transformation to presentation, so the taxonomy is no longer independently testable and the service layer stops being usable without a browser.
- A repeated failure, which is what a gateway outage or a throttled tenant looks like, produces repeated identical interruptions with no state to coalesce them.
- Every future surface silently inherits whatever default the interceptor happened to pick, which is how the seed rows came to look like live data in the first place.

### Option 2: Pure classifier plus a per call site display policy

One pure function normalizes a response into a typed `GatewayError`. The call site declares its policy through `HttpContext`. The interceptor classifies, applies the declared policy, and rethrows so the caller's own handling still runs.

**Pros**:
- The taxonomy is a pure function with no Angular dependency, so it is exhaustively unit testable and cannot draw anything by accident.
- The 400, 402, 403, 429, and 5xx cases are decided once and worded once, instead of drifting per service the way the current `console.warn` strings have.
- Background polls and user actions share one classifier and differ only in declared intent, which is the distinction the app level rate limiter makes unavoidable.
- State coalescing becomes expressible: a second 429 updates the existing banner rather than stacking, which is what a throttled tenant actually needs.
- Adding a new call site fails loudly, because `SURFACE` defaults to `toast` and `GatewayError` is the only type the error callback can receive.

**Cons**:
- More code for the same number of endpoints, because there was no existing layer to extend, only silence to replace.
- The `SURFACE` token can be set to `silent` on a user initiated action by mistake, and no type can catch that. It stays a review concern.
- Two places to read instead of one: the taxonomy in the classifier, the wording in the copy table.

### Option 3: Handle errors inside each service

Each service maps its own failures in its own `error` callback.

**Pros**:
- No new abstractions, nothing to learn, and the logic sits next to the request that produced it.
- A service can react to its own failure without any indirection.

**Cons**:
- The same 402 gets worded four different ways, and the fourth wording is the one a user sees.
- Rate limit and budget state cannot be shared, so the coalescing that a throttled tenant needs has nowhere to live.
- There is no seam for a test that exercises the mapping, so every assertion has to go through a component.

## Rationale

Option 1 was rejected on a specific force rather than a general preference: the rate limiter is an app level dependency and the dashboard polls on a timer, so a display in the interceptor has no way to distinguish a 429 the user caused from a 429 the poller caused. That single fact makes the blueprint's placement rules unimplementable as written. Option 3 was rejected because the current per service `console.warn` handlers are already that design, and they are the source of the problem: four services, four silent behaviors, no shared wording, and no place to put state.

The specific forces that picked Option 2 were the shape of the failure detail and the existence of shared state. Because `detail` arrives as a string, a dict, or a list, the mapping has to be written once and tested against all three shapes, which argues for a pure function rather than a branch inside a display layer. Because a throttled tenant generates a burst of identical 429s from the poller, the response needs somewhere to hold a coalesced sticky state, which argues for a signal store rather than local component state. Together those push the logic into a classifier and a store, and leave only the presentation decision at the call site.

On the 401 and 403 split: the blueprint grouped them and proposed a re authentication check for both. That is a loop, not a recovery. A 403 from `require_permission("keys:write")` means the token verified successfully and the account lacks the scope. Sending the user through Auth0 returns the same token with the same absent claim, so the portal would bounce until the session itself expired. Only 401 indicates a session the gateway stopped accepting. Confirming the split also settled that no other component was competing for the 401: `AuthHttpInterceptor` in `@auth0/auth0-angular` handles token acquisition and attaches the bearer header, and passes responses through untouched, so a 401 was previously unhandled and this spec is adding that handling rather than duplicating it.

On the budget banner: a toast was rejected because the condition persists until the day rolls over or the cap is raised, and a message that vanishes in seconds tells a tenant nothing about why their agents stopped working. Persistent and undismissable was also rejected, because a user who has read it cannot clear it for the rest of the day. The chosen lifecycle is dismissible but not muteable: dismissal applies to the current banner, and the next 402 brings it back. That keeps a tenant from dismissing the explanation once and then repeatedly retrying an action the gateway will keep refusing.

On telemetry: a build time demo flag was considered and rejected as worse than removal. A flag that renders plausible rows in a deployed portal is a live falsehood that only fails when someone notices the flag was left on, and it adds a second code path through the dashboard that nothing tests. The seed rows and the hardcoded figures are removed, and if demo data is ever genuinely needed it returns as an explicit, unmissable, separately labelled state rather than as a fallback.

On the 402 body: the alternative of leaving the backend alone and having the portal derive figures from `/v1/usage/summary` was rejected because `estimated_cost_usd` is `null` for any principal without `billing:admin`, so the figures would silently vanish for most users. Making the portal facing 402 match the dict `completion_proxy` already raises removes an inconsistency between two paths that both mean "budget exhausted", and it is a small change confined to one `raise` statement.

Two RECOMMEND items were settled rather than asked. The notification mechanism is hand rolled because the portal has no CDK and no Material, already uses signals for exactly this kind of derived state in `TelemetryService`, and needs no positioning engine since these surfaces render in fixed regions rather than anchored to a trigger. The accessibility bar was set at announcing properly with no focus theft, because these are enterprise facing screens and the cost is a few attributes, while focus stealing during a background failure is the specific behaviour that makes an error layer feel hostile.

## The other invented data was on the server, and it was a cross-tenant leak

The design named the frontend's seeded `EVT-*` rows, and the build then found the same five rows in `get_telemetry_logs` as a server-side fallback, which the design never knew about. Invented data is bad, as the rest of this document argues at length. But those rows carried `northstar-labs`, `harbor-works`, and `pinnacle-care` in the `tenantId` field, so a tenant with no telemetry history was served another tenant's events, with their ids, costs, and trace handles, on the page whose entire purpose is tenant-scoped audit.

That is a different class of defect from everything else in this spec, and it is worth being precise about why it survived. Every other item here is a display problem: the portal says something untrue about the system. This one is a data isolation failure, and the endpoint's own `tenant_id` filter is correct, so the code around it reads as safe. The fallback returned a literal below that filter, which is why a reviewer scanning for tenancy bugs would find nothing.

The comment above it read `# Fallback demo rows for the portal until real tenant history flows`, which is the tell. A placeholder that returns plausible data in production is not a placeholder; it is shipped behavior with an intention attached, and the intention evaporates while the behavior does not. The honest version is an empty list plus an empty state that says why, and the frontend now distinguishes "not loaded yet" from "loaded and genuinely empty" so the two do not read the same. Both are now that.

## A cross check, and what it corrected

A critique pass ran after the draft was written and found two defects in it that reading the code caught and pure reasoning did not. Both are recorded because they are the kind of error a reviewer should not have to rediscover.

The first was a swallowed session expiry. The draft had the interceptor skip reporting whenever the call site declared `silent`, and separately routed a 401 to a redirect. Combined, those two rules mean a 401 arriving on a `silent` poll was classified, then dropped, then rethrown to a caller that ignores it. Since the dashboard polls on a 60 second timer and a user looking at a healthy dashboard takes no action, that poll is frequently the only request that will ever discover an expired session. Nothing would have redirected. `session_expired` now overrides `silent` and always reports, and the override is the reason the interceptor's condition is not a single equality test.

The second was an over-broad exception. The draft scoped the interceptor to `/api`, on the reasonable reading that `/api` is the gateway's alias prefix. It is not: `/api` is the ingress prefix shared by all three backends, and `auth.service.ts:20,24` uses it to reach the Java gateway and Spring Boot. Under the draft's scoping, a Spring Boot 401 would have been classified as a gateway session expiry and sent the user to Auth0 to fix a service that was never involved, with a banner naming the wrong system. The scope is now `/api/v1/`, which is where the gateway's own mounts land (`main.py:73-75`) and which nothing else in the portal uses.

Two smaller corrections came from the same pass. The budget banner was specified as "keyed by tenant", which is unbuildable: the portal has no tenant identity source, `tenant_id` appears only as a response field on `UsageSummary`, and a session is already a single tenant, so the key would be missing at the moment it was needed. And the classification table's "default policy" column contradicted the `SURFACE` token's `toast` default, because the interceptor reads policy from the request and never from the status; the column is now "policy at known call sites" and the code default is stated once.

A third defect was found afterwards, on a reread rather than in the pass, and it is the most consequential of the three. The spec commits to enriching the 402 so the budget banner can show spend against cap, then defines message extraction as a five step precedence in which step 2 matches an object carrying a `message` key. The actual body is `{ error, current_spend_usd, max_budget_usd }` (`llm_proxy.py:123-128`) with no `message` key, so step 2 misses, step 4 discards the object, and the banner renders the generic copy with both figures unread. The spec would have shipped a backend change whose entire purpose was undone by a rule in the same document. The 402 now has an explicit branch ahead of the fall-through that reads both figures into `GatewayError.budget`, with a numeric guard so a malformed body degrades to the generic copy rather than printing "spent of $undefined".

The check was not independent. The tooling available could not select a different model, so the critique ran on the thread that wrote the spec, which is materially weaker than a fresh reader. It found these because they were verifiable against the source, not because a second perspective corrected a blind spot. That the third and worst one survived the pass and turned up on reread is the clearest evidence of the limitation.

## A fourth defect, found by the build

The build found a bug the critique pass and the reread both missed, and it is the most useful thing in this record.

The classifier read the detail from `HttpErrorResponse.error`. But `error` is the parsed response *body*, and every refusal in this gateway is a FastAPI `HTTPException`, which serializes as `{"detail": ...}`. The detail is one level down. So `{"detail": "Key creation requires keys:write"}` arrived as an object with no `message` key, missed step 2, was discarded at step 4, and every affected refusal rendered the generic copy: the user would have been told "Your account does not have permission for this action" while the gateway's actual reason, which names the scope to request from an administrator, went unread.

What makes this worth recording rather than just fixing is *why* the first classifier test suite passed. It constructed its response bodies as bare strings, `new HttpErrorResponse({ error: 'Key creation requires keys:write' })`, which is a shape no real gateway produces. The tests were green and the feature was broken in the only environment that matters. The first interceptor test to use a realistic envelope failed immediately, which is the sole reason the bug surfaced before the change reached a browser.

The general lesson is that a test fixture is a claim about the producer. A hand-built fixture that is convenient rather than faithful will encode the author's assumption instead of the system's behavior, and every assertion downstream inherits the error. The `unwrapDetail` step and the `fastapiException` fixture exist now so the next reader starts from the real shape.
