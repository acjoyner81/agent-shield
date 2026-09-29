# 0013. Standardize gateway error handling in the Angular portal

**Date**: 2026-09-29
**Status**: Accepted

## Summary

Today every gateway refusal the portal receives is thrown away: services log a console warning and clear their error, and components subscribe with a success handler only, so a budget refusal, a rate limit, or a missing permission changes nothing on screen. This spec sets the one right way to classify a gateway failure (turn it into a named, typed error) and the one right way to decide what the user sees about it (declared per call site, never guessed by a global handler). It also removes the fake telemetry the portal displays when a fetch fails, so a broken gateway can no longer look like a healthy one.

## Decision

**Chosen option**: a pure classifier plus a per call site display policy, with a hand rolled signal store for the banner, the rate limit state, and stale widgets.

Every `HttpErrorResponse` is normalized once into a typed `GatewayError` carrying a user readable message. The call site that issued the request declares, through `HttpContext`, whether the failure is silent, a banner, or a toast. A single app level container renders the result. The classifier never draws anything itself, so it cannot get the placement wrong for a request it did not originate.

## Standard definition

### Canonical pattern

```typescript
// core/errors/gateway-error.ts  (pure, no Angular, fully unit testable)
export type GatewayErrorKind =
  | 'budget_exhausted'   // 402
  | 'rate_limited'       // 429
  | 'guardrail_blocked'  // 400
  | 'scope_denied'       // 403
  | 'session_expired'    // 401
  | 'unexpected';        // 5xx, network, anything unmapped

export interface GatewayError {
  kind: GatewayErrorKind;
  message: string;          // already user readable, never "[object Object]"
  retryAfterSeconds?: number;   // from Retry-After on 429
  remaining?: number;           // from X-RateLimit-Remaining on 429
  budget?: {                    // 402 only
    currentSpendUsd: number;
    maxBudgetUsd: number;
  };
  status: number;               // 0 when there was no response at all
  originalError?: unknown;      // devtools only, never rendered, see below
}
```

**`originalError` is for debugging, never for display.** It retains the raw `HttpErrorResponse`, which can carry the entire response body. Nothing binds it in a template, and no message is ever derived from it. It exists so a developer can inspect what the gateway actually said without a breakpoint. It is typed `unknown` so reading a property off it is a deliberate act.
```

```typescript
// core/errors/classify.ts
export function classifyGatewayError(err: unknown): GatewayError | null
// - not an HttpErrorResponse  -> null, pass through untouched
// - 400 -> guardrail_blocked,   401 -> session_expired
// - 402 -> budget_exhausted,   403 -> scope_denied
// - 429 -> rate_limited,        0 -> unexpected (no response, offline)
// - anything else              -> unexpected
// Always produces a message. Never throws. Never returns undefined.
```

```typescript
// core/errors/surface.context.ts
// The call site declares intent. The interceptor cannot infer it.
export type SurfacePolicy = 'silent' | 'banner' | 'toast';
export const SURFACE = new HttpContextToken<SurfacePolicy>(() => 'toast');
```

```typescript
// core/interceptors/gateway-error.interceptor.ts
// (core/interceptors/ is the repo's existing home for interceptors; the pure
// classifier and the SURFACE token stay in core/errors/ where the pure logic lives)
export const gatewayErrorInterceptor: HttpInterceptorFn = (req, next) => {
  // Only the Python gateway's paths. `/api` is the ingress prefix for all three
  // backends, so a Spring Boot 401 must not be read as a gateway session expiry.
  if (!req.url.startsWith('/api/v1/')) {
    return next(req);
  }
  const policy = req.context.get(SURFACE);
  return next(req).pipe(
    catchError((err: unknown) => {
      const classified = classifyGatewayError(err);
      if (classified === null) {
        throw err;                       // not ours, do not touch it
      }
      // A session expiry is never silent. The dashboard's 60s poll is often the
      // only signal, and no user action is coming to produce another request.
      if (policy !== 'silent' || classified.kind === 'session_expired') {
        inject(NotificationService).report(classified, policy);
      }
      // rethrown so the caller's own error handling still runs
      throw classified;
    })
  );
};
```

Registered **after** `authHttpInterceptorFn` in `withInterceptors([...])`, so it sees responses carrying an `Authorization` header. Reverse this and the Auth0 token failure path is misread as a gateway failure.

```typescript
// A background poll is silent. A click is not.
this.http.get<UsageSummary>('/api/v1/usage/summary', {
  context: new HttpContext().set(SURFACE, 'silent')
});
```

### Classification and surface table

| Status | Kind | Policy at known call sites | Where it renders | Clears when |
|---|---|---|---|---|
| 400 | `guardrail_blocked` | `silent` | Inline at the call site | n/a, no consumer yet |
| 401 | `session_expired` | `banner`, never `silent` | App level, plus one redirect through Auth0 | Redirect completes |
| 402 | `budget_exhausted` | `banner` | App level, one banner | Dismissed by the user, returns on the next 402 |
| 403 | `scope_denied` | `banner` | App level, naming the missing scope | Dismissed by the user |
| 429 | `rate_limited` | `banner` (sticky) | App level, with a countdown | `Retry-After` elapses, or the first success, whichever is first |
| 5xx, 0 | `unexpected` | `toast` | Transient, text only, no retry control | Auto dismisses |

The code default is `toast` for every status. The interceptor reads the policy from the request, never from the status, so a 400 on a call site that declares nothing toasts. Declaring `silent` on a 400 is the call site's job, which is why no surface consumes one yet.

**401 must never trigger re authentication for 403.** A 403 means the session is valid and the account lacks the scope, so signing in again returns the same token with the same missing scope. Redirecting on 403 is an infinite loop, not a re auth. Only 401 redirects, and a 401 reports even from a `silent` poll, because the poll may be the only signal that the session died.

**There is no global retry control.** The `unexpected` toast carries text only. A call site that wants a retry renders one against its own request, because only it knows what to re-fetch and whether the action is safe to repeat. A blanket retry button on the toast would have to guess.

### Message extraction precedence

**Unwrap the envelope first.** `HttpErrorResponse.error` is the parsed response *body*, and every refusal in this gateway is a FastAPI `HTTPException`, serialized as `{"detail": ...}`. The detail is one level down. A classifier that reads `err.error` as the detail sees an object with no `message` key, falls through every step below, and shows generic copy while the gateway's specific explanation sits unread. Any body without a `detail` key passes through untouched, so a non gateway producer still works.

`detail` is not one shape. Resolve in this order and stop at the first hit:

1. `detail` is a string, use it.
2. `detail` is an object with a `message` string, use that.
3. `detail` is a list of validation objects, join their `msg` values with their `loc` field names, for example "messages: Input should be a valid list".
4. `detail` is an object without `message`, fall through.
5. Nothing recognized, use the per status generic copy below.

**402 is the one status whose body is worth reading, and it needs a branch before the fall-through.** `completion_proxy` raises 402 with `{ error, current_spend_usd, max_budget_usd }` (`llm_proxy.py:123-128`), and the portal facing route will be changed to raise the same three keys. There is no `message` key, so step 2 does not match, and step 4 discards a body the gateway populated specifically so the portal could use it. Left as written, the budget banner would render generic copy while the two figures went unread, defeating the reason for enriching the 402 at all.

So for `budget_exhausted` only, before step 4: read `current_spend_usd` and `max_budget_usd` into `GatewayError.budget` and compose the message as "Budget exhausted: $3.20 spent of $2.00 today." If either figure is missing or not a number, the branch falls through to the generic copy rather than printing "spent of $undefined". The `error` key is not added to the general precedence: one endpoint uses it, and this branch already reads that body.

| Status | Generic copy when the body says nothing useful |
|---|---|
| 400 | "That request was blocked by a safety rule." |
| 401 | "Your session expired. Signing you back in." |
| 402 | "Daily budget exhausted. Requests pause until the budget resets." |
| 403 | "Your account does not have permission for this action." |
| 429 | "Rate limit reached. Requests are being throttled." |
| 5xx, 0 | "Something went wrong reaching the gateway. Try again." |

Any body shape that reaches step 5 logs one `console.warn` in development builds naming the keys it saw, so a new shape is noticed instead of silently rendering nothing.

### State model

All state lives in `NotificationService` as signals. No component holds error state.

| State | Enters | Leaves | Rules |
|---|---|---|---|
| `budgetBanner` | A 402 is reported | User dismisses it, or a new 402 | One banner, not keyed by tenant: the portal has no tenant identity source, and a session is a single tenant, so a key would be unavailable exactly when it is needed. Dismissal is per banner, not a mute. A later 402 shows it again, so it is impossible to dismiss once and never learn the budget is still out. |
| `rateLimit` | A 429 is reported | `Retry-After` elapses **or** the first 2xx, whichever is first | A second 429 updates the existing entry, it never stacks. Actions may be dimmed or disabled while set. The countdown is recomputed on every 429. |
| `sessionExpired` | A 401 is reported | Redirect completes | One redirect only, guarded by a flag so a burst of 401s cannot start a redirect storm. |
| `staleWidgets` | A `silent` poll fails | That poll next succeeds | Per widget, holding the last known values and a `lastUpdatedAt`. Never clears values. |

**`Retry-After` is integer seconds in practice.** `rate_limit.py:187` writes it on every 429 and it is always a positive whole number of seconds. When the header is missing or unparseable the classifier omits `retryAfterSeconds` and the banner carries no countdown, clearing on the next success alone. It never guesses a duration.

**Formatting goes through `Intl`, never hand rolled string math.** The 402 figures are formatted with `Intl.NumberFormat` in `en-US` using `style: 'currency'`, `currency: 'USD'`, and the classifier composes the budget message itself, because `message` is part of its output contract and a banner that showed both the generic copy and the figures would say the same thing twice. The staleness note ("last updated N ago") is display layer work and stays out of the classifier, since it is derived from time passing rather than from a response.

**Background polls never interrupt.** A failed `silent` poll marks its widget stale and keeps the last known figures visible with a quiet "last updated N ago" note. It raises no toast, no banner, and no modal. The dashboard polls usage and health every 60 seconds, so an interrupting policy would emit a toast every 30 seconds to a user who did nothing.

### Accessibility

- The app level container is a live region. Banners use `role="status"` and polite announcement, the budget banner uses `role="alert"` because it requires action.
- No notification moves focus. A user typing in a form is not interrupted.
- Every dismiss control has an accessible name, not a bare glyph.
- The rate limit countdown is announced on entry and on clear, never once per second.
- Colour is never the only signal. Each state carries a text label.

### Replaces

- `error: (err) => console.warn(...)` in `keys.service.ts` and `telemetry.service.ts`, which leaves the previous values on screen as if the fetch succeeded.
- `error: () => undefined` in `billing.service.ts`, which discards a 402 from a user who clicked Upgrade.
- `subscribe({ next: ... })` with no error handler in `keys.component.ts`, so a 403 on key creation looks like a no op.
- The seeded `EVT-*` rows in `TelemetryService.logs`, which render as live data when the gateway is unreachable.
- The hardcoded "Events today 18,420", "Avg. latency 184ms", and the non functional "Download CSV" button in `logs.component.ts`.
- The hardcoded "All systems operational" (`app.html:18`) and "Systems normal" (`app.html:21`). These are worse than the seeded rows, because no request sits behind them: the shell asserts a healthy system on every page, unconditionally, while `dashboard.component.ts:117` computes the real status from `/v1/health/services` for the same tenant on the same screen. One of the two is lying and it is the one in the chrome. They become the first consumers of `staleWidgets`, and the shell has three states, not one: checking, healthy or degraded, and out of date once a poll stops succeeding.

### The same lie on the server

`get_telemetry_logs` returned the same five rows as a server-side fallback when Redis had no history. The build found this, not the design. Two problems, and the second is the serious one.

The rows were invented, so the defect was the same false confidence the rest of this section is about. But they also carried **other tenants' identifiers**: `northstar-labs`, `harbor-works`, `pinnacle-care`. A tenant with no telemetry history was served another tenant's events, with their ids, costs, and trace handles, on a page whose whole purpose is tenant-scoped audit. The endpoint filters `tenant_id` on the real history path, so the filter was correct and the fallback bypassed it entirely.

Both the frontend seed and the server fallback are now an honest empty list, and the frontend distinguishes "not loaded yet" from "loaded and genuinely empty" so the two states read differently. The reason it survived is worth naming: the fallback was commented as a placeholder, and a placeholder that returns plausible data in production is not a placeholder. It was a cross-tenant data leak wearing a TODO comment.

A request that fails and leaves the user with a screen that looks healthy is the defect this standard exists to prevent. A failed background poll and a failed click must look different, because the user did different things.

### Enforcement

Compile time, not review convention. `GatewayError` is the only type the `error` callback of a gateway call may receive, and `classifyGatewayError` is exhaustive over `GatewayErrorKind` with no implicit fallback branch, so adding a status without deciding its surface is a compile error. `SURFACE` is a required context token whose default is `toast`, so a new call site is loud by default rather than silent.

No lint rule. The type level contract is stronger than a lint rule and costs no extra tooling.

### Exceptions

Requests that do not target the Python gateway are outside this standard. The interceptor is scoped to the `/api/v1/` prefix, which is exactly where the gateway's `billing`, `metering`, and `keys` mounts land when they are re-registered with `prefix="/api"` (`main.py:73-75`). Every other request passes through untouched.

The prefix is deliberately `/api/v1/` and not `/api`. `/api` is the *ingress* prefix for all three backends: the portal also calls `/api/python/api/v1/protected` and `/api/java/actuator/health` (`auth.service.ts:20,24`), which reach the Java gateway and Spring Boot. Scoping to `/api` would classify a Spring Boot 401 as a gateway session expiry and trigger an Auth0 redirect for an unrelated service's authentication failure, naming the wrong system in front of the user.

There is no exception for background polls. They are in scope, and they are the reason `SURFACE` exists.

### Rollout

Single migration, all in one change. The surface is four small files plus edits to three services and three components, and the parts cannot ship apart: a classifier with no container renders nothing, and a container with no classifier renders nothing.

The backend change is one `raise`. `main.py:626` raises 402 with the bare string `"Tenant daily budget exceeded"`; it becomes the same three keys `llm_proxy.py:123-128` already raises, `error`, `current_spend_usd`, and `max_budget_usd`. Both values are already in scope two lines above at `main.py:624-625` (`current_spend`, `tenant_info["daily_budget_usd"]`), so no new read is introduced. It ships with the portal change so the banner has figures from its first day.

## Consequences

**Positive**:
- A budget refusal, a rate limit, a missing scope, and a guardrail trip are now distinguishable to the user instead of indistinguishable from silence.
- The dashboard can no longer present seeded rows as live telemetry, which was the single largest source of false confidence in the portal.
- The `keys:write` refusal names the scope, so a developer knows what to ask an administrator for.
- The 400 contract is defined before a surface consumes it, so the prompt tester inherits it rather than inventing its own.

**Negative / tradeoffs**:
- Requires a small backend change. The portal facing 402 detail becomes a structured object instead of a string, so any consumer stringifying it, including the public API examples, must be checked.
- A compile time contract does not stop someone from setting `SURFACE` to `silent` on an action a user clicked. That is a code review concern the type cannot express.
- A persistent budget banner that returns on every 402 is mildly more annoying than one that stays dismissed. Chosen deliberately: a tenant that cannot see why it is blocked will simply keep retrying.
- Strictly more code than today for the same number of endpoints. The portal had no error layer to extend, only silence to replace.

**Neutral**:
- No new runtime dependency. No CDK, no Material. The store is signals and the container is a component, matching what `TelemetryService` already does.
- Client side error reporting to Splunk is not part of this. The portal stays as blind to its own errors as it is to the gateway's internals, and a support question of "did the user see the banner" is still unanswerable.
- The `400` path is defined but unexercised, since no portal surface calls the completion route.

## Follow-up

- [ ] Build a prompt tester surface so `guardrail_blocked` has a consumer and the guardrail message is reachable end to end.
- [ ] Emit client error events to `POST /v1/telemetry/logs` so support can confirm a user actually saw a banner. Needs a client event schema and a privacy call on what context may leave the browser.
- [ ] Remove the duplicated Auth0 audience literal in `app.config.ts` by reading it from `environment.auth0.authorizationParams.audience`. A one line fix that does not belong to this decision.
- [ ] Re check the public API examples in spec 0012 for any 402 body that is documented as a bare string.
- [ ] The `keys` page still uses bare `prompt()` and `confirm()` dialogs. Replace with real modal components as part of the container work.
- [ ] `core/interceptors/auth.interceptor.ts` is dead code, never registered in `app.config.ts`. If anyone wires it alongside `authHttpInterceptorFn` every request will make two token calls. Delete it, or reduce it to a documented no-op, so the trap is removed rather than left armed.
- [ ] `logs.component.ts` pulls in ag grid for a 692 kB lazy chunk. Unrelated to this decision, but worth a look when the page is next touched.

## Rationale

Reasoning and options: see rationale.md
