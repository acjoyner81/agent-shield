# Verify: Granular RBAC Enforcement · spec 0005 · updated 2026-10-08
_Steps derived from spec 0005 acceptance criteria. `/check verify` runs these; `/test` locks the durable ones._

## Result (2026-10-08 run) — verdict BLOCKED on AC-2 (AC-1, AC-3, AC-4 met)
Re-ran the stale 2026-09-23 record against the current build (post `c1c4fa3`, the namespaced
permissions claim). Driven live against `http://localhost:8000`, real Redis.

| AC | Verdict | Evidence |
|---|---|---|
| AC-1 | met | `gateway/main.py:723` declares `Depends(require_permission("tools:execute"))`; the route enforces it live. |
| AC-2 | **blocked** | Proven only for the DEV stand-in, whose claims carry the namespaced `https://api.agentshield.local/permissions` claim (`gateway/auth.py:69-97`). The real Auth0 JWKS decode path could not be exercised: no real access token, no `AUTH0_MGMT_TOKEN`, no login credentials exist in this environment. Use `auth0/seed_and_verify.py --access-token <real>` or seed a user to close it. |
| AC-3 | met | `POST /v1/tools/execute` with `dev-unprivileged-token` → `403 {"detail":"Permission denied: missing required scope 'tools:execute'"}` exactly; `dev-mock-token` (scope held) → `200`. |
| AC-4 | met (was FAIL, remediated) | First run found the `agentshield.security.authz_failure` event carried `tenant_id` and `user_id` but dropped the required permission: `require_permission` passed `missing_scope=required_scope` (`gateway/auth.py:317-323`), but `emit_authz_failure` ignored it. Fix: added an optional top-level `required_permission` to `EventEnvelope`, threaded through `emit_event` and `TelemetryEventEmitter.emit`, set from `missing_scope` in `emit_authz_failure` (`gateway/telemetry.py`); the `data.security` object is unchanged. Re-verified live: the event now reads `tenant_id: tenant_alpha, user_id: user_dev_456, required_permission: tools:execute`. Regression tests fail without the fix: `test_telemetry.py::TestTelemetryEmitter::test_emit_authz_failure`, `test_rbac.py::test_authz_failure_event_records_required_permission`. |

Why the suite was green while AC-4 was unmet: the original `test_rbac_execute_tool_forbidden`
asserted only that `emit_authz_failure` was *called* with `missing_scope=REQUIRED_SCOPE`, never that
the emitted event contained it. The two new tests close that: one inspects the emitted envelope
directly, one drives the real route and inspects the event.

Nothing is ticked; AC-2 blocks the run, so the feature stays unverified until a real Auth0 token is
available.

## Commands
- [x] `pytest gateway/tests/test_rbac.py` → 19 passed (route-level success/403, exact-match scope semantics, 401 auth failures, tenant-binding override, authz telemetry emission) → AC-1, AC-2, AC-3, AC-4
- [x] `pytest gateway/tests/ --ignore=gateway/tests/e2e` → 397 passed

## Acceptance-criteria coverage
- AC-1: Endpoints declare explicit required scope dependencies using `require_permission("...")` helper · covered by `test_rbac_execute_tool_success` & `gateway/main.py`
- AC-2: Gateway verifies decoded JWT `permissions` claim array contains target scope string (exact match, default-deny; no wildcards/implicit roles) · covered by `gateway/auth.py::get_verified_tenant` & `require_permission` and `test_scope_present_passes` / `test_scope_absent_denied`
- AC-3: Missing scope returns HTTP 403 Forbidden with exact detail message · covered by `test_rbac_execute_tool_forbidden`, `test_rbac_tenant_binding_overrides_header`, `test_scope_absent_denied`
- AC-4: Authorization failure is recorded in telemetry with `user_id`, `tenant_id`, and `required_permission` · covered by `test_rbac_execute_tool_forbidden` (call payload), `test_authz_failure_event_records_required_permission` (real route → emitted event) & `test_emit_authz_failure` (emission) & `gateway/auth.py::require_permission`

## Adjacent contracts locked
- Missing `Authorization` header → 401 `Not authenticated` (spec 0004 AC-1, HTTPBearer `auto_error=False`) · `test_rbac_missing_bearer_rejected`
- Invalid/expired token → 401 before any permission evaluation · `test_rbac_invalid_token_rejected`, `test_authz_never_runs_without_verified_tenant`
- Verified JWT `tenant_id` overrides untrusted `X-Tenant-ID` (spec 0004 AC-4) · `test_get_verified_tenant_binds_context`
