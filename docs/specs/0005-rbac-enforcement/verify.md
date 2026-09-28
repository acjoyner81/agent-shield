# Verify: Granular RBAC Enforcement · spec 0005 · updated 2026-09-23
_Steps derived from spec 0005 acceptance criteria. `/check verify` runs these; `/test` locks the durable ones._

## Commands
- [x] `pytest gateway/tests/test_rbac.py` → 16 passed (route-level success/403, exact-match scope semantics, 401 auth failures, tenant-binding override, authz telemetry) → AC-1, AC-2, AC-3, AC-4
- [x] `pytest gateway/tests/ --ignore=gateway/tests/e2e` → 163 passed

## Acceptance-criteria coverage
- AC-1: Endpoints declare explicit required scope dependencies using `require_permission("...")` helper · covered by `test_rbac_execute_tool_success` & `gateway/main.py`
- AC-2: Gateway verifies decoded JWT `permissions` claim array contains target scope string (exact match, default-deny; no wildcards/implicit roles) · covered by `gateway/auth.py::get_verified_tenant` & `require_permission` and `test_scope_present_passes` / `test_scope_absent_denied`
- AC-3: Missing scope returns HTTP 403 Forbidden with exact detail message · covered by `test_rbac_execute_tool_forbidden`, `test_rbac_tenant_binding_overrides_header`, `test_scope_absent_denied`
- AC-4: Authorization failure is recorded in telemetry with `user_id`, `tenant_id`, and `missing_scope` · covered by `test_rbac_execute_tool_forbidden` (asserts `emit_authz_failure` payload) & `gateway/auth.py::require_permission`

## Adjacent contracts locked
- Missing `Authorization` header → 401 `Not authenticated` (spec 0004 AC-1, HTTPBearer `auto_error=False`) · `test_rbac_missing_bearer_rejected`
- Invalid/expired token → 401 before any permission evaluation · `test_rbac_invalid_token_rejected`, `test_authz_never_runs_without_verified_tenant`
- Verified JWT `tenant_id` overrides untrusted `X-Tenant-ID` (spec 0004 AC-4) · `test_get_verified_tenant_binds_context`
