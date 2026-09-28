# Review, main, 2026-09-28

**Reviewed by**: big-pickle (author model not recorded; commit author acjoyner81)
**Scope**: 7 files, commit `74d977d` vs parent `1e00ffb` (scoped to spec 0012, Public API)
**Verdict**: Blocked

## Summary

The change publishes a curated OpenAPI contract for six paths, registers both auth schemes, and adds a
Quick start panel. The curation machinery itself is sound and I could not fault it for leaking a
credential, leaking an internal route, or producing an invalid path item. What blocks it is elsewhere:
a machine key with **zero** scopes can mint itself a new key with **any** permissions and receive the
live secret, and a caller-supplied `X-Tenant-ID` header is still authoritative for the app-level rate
limiter *before* authentication, which is the exact guarantee the newly published schema tells
external developers the product provides. The test suite that was supposed to lock AC-2 and AC-3 has
one test that cannot fail, two no-op Redis patches, and the recorded verification result (222 passed)
no longer reproduces (218 passed, 4 failed).

## Blockers

### 🔴 A scopeless machine key can mint itself any permission, `gateway/keys.py:175`

**Problem**: `create_api_key` (`keys.py:175-218`) takes its tenant from `Depends(resolve_active_tenant)`
and never checks `request.state.permissions`. Spec 0012 widened every `/v1/keys` route from
`get_verified_tenant` (JWT only) to `resolve_active_tenant` (Bearer *or* key), so an `X-Tenant-API-Key`
principal with `permissions: []` can `POST /v1/keys {"name": "...", "permissions": ["tools:execute"]}`
and receive a fresh `sk_live_...` secret in the response. Proven end to end against the real router:

1. key with `permissions: []` → `POST /v1/tools/execute` → **403** `missing required scope 'tools:execute'`
2. same key → `POST /v1/keys {"permissions": ["tools:execute"]}` → **201**, `secret_key: "sk_live_aCY…"`
3. that minted key → `POST /v1/tools/execute` → **502** (MCP unreachable, i.e. authz *passed*)

The same path escalates the spend gate: `metering.py:348` populates cost only for
`"billing:admin" in request.state.permissions`, and permissions are chosen by the caller in the
`POST /v1/keys` body, so a key can grant itself `billing:admin` and read cost. `DELETE /v1/keys/{key_id}`
also lets any machine key revoke its siblings (tenant is the caller's own, so no cross-tenant effect,
but a compromised low-privilege key can lock out the tenant's real integration keys).
`POST /v1/keys/{key_id}/rotate` similarly mints a successor with attacker-chosen `name` and inherited
scopes.

This is not merely an implementation slip. The spec's stated acceptance of the widening is
"a key already grants the tenant's own data plus any scopes it carries" (`index.md:55-58`) — that
reasoning covers *reading* with existing scopes, not *choosing new* ones. The same paragraph asserts
"key creation remains the portal's single surface", which the code does not honour. Worse,
`main.py:38-39` puts the escalation in the public contract verbatim: "Create keys from the portal, or
with `POST /v1/keys`."

**Why it matters**: any leaked low-privilege key — a CI token, a read-only reporting key, a key in a
public repo or a support ticket — converts into a full-privilege tenant credential in one request, and
the raw secret is returned to the caller. The scope system that spec 0005 shipped stops meaning
anything once the principal can issue its own scopes. Nothing in the 0012 tests exercises this.

**Suggested fix**: keep the key-mutating routes (`POST /v1/keys`, `POST /v1/keys/{key_id}/rotate`,
`DELETE /v1/keys/{key_id}`) on a JWT-only dependency so key management stays the portal surface, or
gate them behind a `keys:manage` scope that a machine key can never self-assign, and make the scope
check compare the *requested* permissions against the caller's. Whatever the call, remove the
"or with `POST /v1/keys`" line from `API_DESCRIPTION` and add a regression test asserting a
scopeless key gets 403 on key creation and cannot mint a `billing:admin` key.

### 🔴 A spoofed `X-Tenant-ID` is authoritative before authentication, and the schema denies it, `gateway/main.py:45`

**Problem**: the app is constructed with `dependencies=[Depends(verify_rate_limit)]` (`main.py:60`).
App-level dependencies are prepended by FastAPI, so `verify_rate_limit` runs **before** any route's
`resolve_active_tenant`. It resolves the tenant via `gateway/rate_limit.py:95-116`, which checks
`request.state.tenant_id` (empty at that point), then `X-Tenant-API-Key` against the hardcoded
`TENANT_CONFIG` literal keys, then falls through to `request.headers.get("X-Tenant-ID")` at
`rate_limit.py:108-110`. Proven:

```
GET /v1/usage/summary   (no Authorization, no API key, X-Tenant-ID: tenant_someone_else)
  -> 401, but check_token_bucket was called with tenant_id = "tenant_someone_else"
GET /v1/usage/summary   (no headers at all)
  -> 401, check_token_bucket never called
```

So an unauthenticated caller selects which tenant's token bucket is debited, and when that bucket is
exhausted the limiter raises 429 *before* the 401. That is unauthenticated cross-tenant resource
consumption and a pre-auth oracle for a victim's rate-limit state. Tenant ids are not secret — the
published usage example literally contains `"tenant_id": "tenant_alpha"` (`metering.py:76`).

Meanwhile the newly public contract makes the opposite promise in three places: `API_DESCRIPTION`
(`main.py:45-47`) "A caller supplied `X-Tenant-ID` header is **never authoritative** and is ignored",
and the same sentence in both `bearer` and `apiKey` security scheme descriptions
(`main.py:284-285`, `main.py:294-295`). The spec names this as a key invariant — "The precedence rule
documented in the schema matches the runtime, otherwise the docs lie" (`index.md:75`) — and as AC-3.

**Why it matters**: this publishes a false security guarantee to every external developer the feature
targets, which is worse than not publishing one: a tenant will build an integration on the assumption
that the header is inert. The isolation break itself is independently exploitable.

**Suggested fix**: delete the `X-Tenant-ID` fallback in `rate_limit.py:108-110` and the
`TENANT_CONFIG`-by-literal-key branch at `rate_limit.py:101-106`; the limiter must no-op until
`request.state.tenant_id` is populated by a verified principal, and the tenant's tier must come from
the store. Then add a test that an unauthenticated request carrying `X-Tenant-ID` debits nobody's
bucket, and one asserting the limiter cannot emit 429 before auth. Do not ship the docs change
without the code change.

## Major

### 🟠 `test_bearer_wins_over_api_key` cannot fail, `gateway/tests/test_public_api_machine_access.py:120`

**Problem**: two independent defects make the assertion vacuous.

1. Lines 138-140 assert `not any(str(call).startswith("apikey:") for call in store.get.call_args_list)`.
   A `unittest.mock.call` stringifies to `call('apikey:…')`, so it never starts with `apikey:`. The
   generator is always empty and the assertion is always true. Verified:
   `str(m.get.call_args)` → `"call('apikey:deadbeef', 'sentinel')"`, `startswith("apikey:")` → `False`.
2. Line 136 asserts `response.json()["tenant_id"] == "tenant_alpha"`. `dev-mock-token` binds
   `tenant_alpha` (`auth.py:47`) and the seeded store also binds `tenant_alpha`
   (`test_public_api_machine_access.py:40`). The fixture gives both credentials the same tenant, so
   this assertion passes identically whether the Bearer token or the key wins.

**Why it matters**: AC-2's runtime half — the precedence rule the schema and the verify record both
claim — has no effective test. Flipping `resolve_active_tenant` to check the key first would leave
this test green.

**Suggested fix**: seed the store with a tenant distinct from the JWT's (`tenant_beta` vs
`tenant_alpha`) so the response `tenant_id` discriminates between the two sources, and check the
store call list with `any(c.args and str(c.args[0]).startswith("apikey:") for c in …)`. Add the
inverse case the record claims but no test covers: a garbage `Bearer` token plus a valid key must 401
(fail closed, never downgrade to the key).

### 🟠 The keys/metering Redis patches are no-ops; the suite reads the developer's real Redis, `gateway/tests/test_public_api_machine_access.py:89`

**Problem**: `keys.py:150,179,234,320` and `metering.py:105` declare `r_client: redis.Redis = Depends(get_redis_client)`.
FastAPI captures the dependency callable at decoration time, so `patch("gateway.keys.get_redis_client", …)`
in the test has no effect on the already-registered route. Only `gateway.auth.get_redis_client` and
`gateway.main.r` are resolved through module globals at call time and are genuinely patched.
Demonstrated: with the mock primed to return a `FAKE_ONLY` sentinel from `hgetall`, `GET /v1/keys`
returned real data and `store.hgetall.called` was `False`.

**Why it matters**: the `/v1/keys` and `/v1/usage/summary` cases in
`test_contract_route_accepts_a_machine_key` run their handlers against the host Redis at
`redis://localhost:6379` — the exact hazard AGENTS.md documents ("Seeding or inspecting usage from the
host silently touches the wrong store"). Results depend on whatever is in that store, the suite reads
real key metadata, and it will not behave the same in CI or in the container. `store.hgetall.return_value`
and `store.hgetall.side_effect` at lines 57-59 are dead code.

**Suggested fix**: override the dependency through `app.dependency_overrides[get_redis_client]`, or
have `get_redis_client()` read a module-level client that the test can swap. Add a guard that fails
the suite if a real Redis connection is attempted.

### 🟠 The recorded verification result does not reproduce: 4 tests fail, `docs/specs/0012-public-api/verify.md:28`

**Problem**: `verify.md:28` records `222 passed`. On the current tree the suite is
`4 failed, 218 passed`. `test_chat_completions_budget_exceeded` and the three
`test_prompt_injection_tripwire_triggers` cases fail, all with `402` where `400` is expected.
Cause: this commit added `dependencies=[Depends(verify_tenant_quota)]` to `/v1/chat/completions`
(`main.py:561`). `dependencies.py:58-63` reads a sticky `tenant:over_limit:{tenant}` flag, and
`test_billing.py:403` leaves `tenant:over_limit:tenant_alpha = "true"` in the host Redis with no
cleanup. Every later `tenant_alpha` chat completion therefore 402s in the quota dependency, which runs
*before* the handler's `scan_prompt_injection`, so the guardrail never gets a chance to answer 400.

**Why it matters**: the suite is red, so the gate the team relies on is not green, and the record of
what was verified is inaccurate. The tripwire tests' guarantee is now conditional on quota state, which
is a behaviour change nobody signed off on. `verify.md:23` also claims `POST /v1/tools/execute` with a
scopeless key returns 403 — true — but `:24` cheerfully records that a machine key can rotate keys as
a pass, which is the blocker above rather than a verification win.

**Suggested fix**: make the quota dependency's test isolation explicit (override
`verify_tenant_quota`, or clear `tenant:over_limit:*` in a fixture) and re-run the full suite before
re-asserting a pass count. Reconsider whether a 402 for exhausted quota should preempt the 400
prompt-injection response at all.

## Minor

### 🟡 The security scheme loop stamps `security` onto non-operation path-item keys, `gateway/main.py:301`

`for method in path.values(): if isinstance(method, dict): method["security"] = …` treats every dict
value in a Path Item Object as an operation. `parameters`, `summary`, `description`, `servers` and
`$ref` are also dicts, so the first route with a path-level `parameters` block or a `$ref` would get a
stray `security` key and produce a schema no validator accepts. No live route triggers it today (I
checked every published path-item: they contain only HTTP methods). It also silently overwrites any
operation-level `security` FastAPI generated. Iterate `openapi_schema["paths"][path].items()` against
an explicit method allowlist.

### 🟡 Lifted examples silently drop the `description` the author wrote, `gateway/main.py:350`

`_media_examples` copies `summary` and `value` and discards `description`. The published media-type
example has keys `['summary', 'value']` while the component has
`['description', 'summary', 'value']`. Every "why" written into `json_schema_extra` — `main.py:98`,
`main.py:107`, `keys.py:38`, `keys.py:43`, `keys.py:59`, `keys.py:88`, `metering.py:70` — never reaches
Swagger UI, which is the only place a developer reads it. Carry `description` through.

### 🟡 Excluded routes leave orphaned components in the published schema, `gateway/main.py:270`

The path filter deletes paths but never prunes `components.schemas`. The published contract contains
`CheckoutRequest`, the request model of the excluded `/v1/billing/checkout`, and nothing references it.
`AC-5` and the invariant "the public schema never contains an internal route" are not literally held.
Both exclusion tests (`test_operational_routes_excluded:125`, `test_published_paths_are_exactly_the_contract:135`)
inspect `paths` only, so neither can catch it. Prune unreferenced components after the path filter, and
assert on `components` in the test.

### 🟡 Dead security objects mislead the next reader, `gateway/main.py:78`

`api_key_header` and `bearer_scheme` are constructed, set to `auto_error=False`, and then never used as
a dependency anywhere in the repo (grep confirms: only their definitions). They read as the wiring for
the published `apiKey` scheme; the scheme is in fact hand-written JSON at `main.py:288`. Delete both,
or use them and derive the scheme from them.

### 🟡 The published `key_prefix` example does not match the runtime shape, `gateway/keys.py:94`

The example says `"key_prefix": "<masked>"`, but `_mask_prefix` (`keys.py:117-118`) returns
`sk_live_••••••••••••unqb` at runtime (confirmed from a live `POST /v1/keys` response). A developer
writing a client against the documented shape gets the wrong string. Use the real masked shape, and
note that example values are never validated against the model, so this class of drift is silent.
Related: `test_key_example_does_not_return_a_live_secret:220` asserts that same literal, so it can
only ever confirm the fixture, never the runtime.

### 🟡 The Quick start hardcodes the gateway origin, `portal-frontend/src/app/features/keys/keys.component.ts:94`

`${window.location.protocol}//${window.location.hostname}:8000` is correct only in the default local
compose topology (`portal-frontend` publishes `4200:80`, `gateway-python` publishes 8000). In any
staging or production deployment behind nginx, TLS termination, or a different port, both the
"Open API reference" link and the copy-paste curl point at an unreachable host — and `nginx.conf:30-36`
shows the canonical same-origin route to the gateway is `/api/`, not `:8000`. The panel's entire
purpose is a command developers paste, so a wrong host is a failed deliverable. Drive it from a
configured base URL (the `API_URL` env the compose file already provides) or the same-origin `/api/`
prefix.

### 🟡 Bearer detection is case-sensitive where the HTTP standard is not, `gateway/auth.py:200`

`auth_header.startswith("Bearer ")` reimplements `HTTPBearer` by hand and rejects the legal
`Authorization: bearer <token>` form. Proven: lowercase `bearer dev-mock-token` plus a valid key
returns 401 `Unknown API key` — the JWT was ignored and the key path was taken. The fail-closed case
is correct and should stay: a present-but-invalid `Bearer` token yields 401 and does **not** fall back
to the key, so the precedence rule cannot be used as a downgrade. But a non-Bearer scheme
(`Authorization: Basic …`) or a bare `Authorization: Bearer` with no space is silently ignored and the
API key is used instead, which contradicts the published wording "Used only when no
`Authorization: Bearer` header is present". Parse the scheme case-insensitively and reject a malformed
`Authorization` header outright rather than ignoring it.

## Nits

- ⚪ `gateway/auth.py:208`, `verify_api_key(request, api_key)` passes the key into the
  `x_tenant_api_key` parameter positionally, which reads like a mistake and would silently re-read the
  header if the signature ever shifts. Pass by keyword, or drop the parameter and keep the header read
  in one place.
- ⚪ `gateway/auth.py:122`, `r_client: Optional[redis.Redis] = None` is not a constructible FastAPI
  parameter. `verify_api_key` works only because it is awaited directly rather than used as
  `Depends(...)`; if anyone ever wires it as a dependency the route fails to build.
- ⚪ `gateway/main.py:376-393`, the request-body and response loops in `_lift_schema_examples` are
  identical apart from the container key; one loop over `(operation["requestBody"], *responses)`
  would do.
- ⚪ `gateway/tests/test_public_api_schema.py:74` and `:135` overlap: the subset assertion in
  `test_contract_routes_present` is fully subsumed by the set-equality assertion.
- ⚪ `gateway/main.py:45`, "A caller supplied `X-Tenant-ID`" and the comments at `main.py:268`, `:284`
  read as run-ons; "a caller-supplied" is what was meant. Worth fixing in whichever change closes the
  blocker, since these strings are the public contract.

## Strengths

- The curation itself holds. I generated the schema independently: `paths` is exactly the six contract
  entries, `securitySchemes` is exactly `bearer` and `apiKey`, and the `/api/*` alias tree plus the
  billing UI routes are all gone. I checked the areas most likely to break and found none of them
  real: `get_openapi` returns fresh component dicts on every call (verified by identity check), so
  `_lift_schema_examples` cannot leak across generations; no live path-item has a non-method dict, so
  the `security` loop is not currently producing an invalid schema; and the cached
  `app.openapi_schema` is only handed to `JSONResponse`, so nothing mutates it per request.
- No credential-shaped string reaches the published schema. I scanned the serialized document for
  `sk_live_`, `whsec_`, `eyJ`, `AKIA`, the hardcoded `key_alpha_123` / `key_beta_456` tenant-config
  literals, and connection strings — all absent. `test_no_secrets_in_examples` is a real check over
  the whole document, not a tautology.
- `security: [{"bearer": []}, {"apiKey": []}]` is the semantically correct OpenAPI encoding for two
  alternative credentials, and `_resolve_ref` correctly propagates the array flag so
  `_media_examples` wraps a single example in a list for `GET /v1/keys`. Getting the list-wrapping
  right is a common miss.
- `resolve_active_tenant` fails closed: a present-but-invalid `Authorization: Bearer` raises 401
  instead of falling through to the key, so the precedence rule cannot be used to downgrade a caller
  to a weaker credential. Worth preserving through any fix to the header parsing.
- `test_spoofed_tenant_header_is_ignored:117` asserts on the actual `hget` key the store was queried
  with, which does prove the store tenant is the one bound (this one is patched correctly, because
  `verify_api_key` resolves its client through the module global). `test_revoked_machine_key_is_rejected`
  and `test_machine_key_without_scope_is_denied_tools` assert real status codes through the real auth
  path rather than asserting on mocks.

## Test coverage

Adequate on curation, thin on the security invariants it is supposed to lock.

Covered and effective: path exclusion and the exact published inventory; both scheme registrations; the
precedence and spoof *text*; presence of examples per operation; the secret-shaped-string scan; key
revocation (401 with `revoked` in the body); `tools:execute` scope enforcement producing 403 rather
than 401.

Not covered, or covered by a test that cannot fail:

- **Bearer-over-key precedence** — `test_bearer_wins_over_api_key:120` is vacuous on both its
  assertions (see Major). AC-2's runtime half is unlocked.
- **The garbage-Bearer + valid-key fail-closed case** that `verify.md:22` reports as verified is not
  in the suite. The code is correct; nothing keeps it that way.
- **The `X-Tenant-ID` invariant** — the one thing the public schema asserts most emphatically is not
  tested against the rate limiter at all, and the rate limiter honours the header (see Blocker).
- **Key management with a machine key** — `POST /v1/keys`, `POST /v1/keys/{key_id}/rotate` and
  `DELETE /v1/keys/{key_id}` are never driven with a key in the suite, which is why the escalation
  shipped. `verify.md:24-25` records them as manually verified and treats the result as a pass.
- **Billing-admin gating under a machine principal** — `metering.py:348` is exercised only through a
  JWT, so the `billing:admin` branch is untested for key callers.
- **Hermeticity** — see Major on the no-op Redis patches; the suite's result is a function of the
  developer's local Redis.
- **Ordering** — no test pins the 429-before-401 behaviour, which is how the spoofed-header
  rate-limit debit is currently invisible.
