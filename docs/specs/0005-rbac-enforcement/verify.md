# Verify: Granular RBAC Enforcement · spec 0005 · updated 2026-09-15
_Steps derived from spec 0005 acceptance criteria. `/check verify` runs these; `/test` locks the durable ones._

## Commands
- [x] `pytest gateway/tests/test_rbac.py` → 2 passed (verifies happy path with scope & 403 response on missing scope) → AC-1, AC-2, AC-3, AC-4

## Acceptance-criteria coverage
- AC-1: Endpoints declare explicit required scope dependencies using `require_permission("...")` helper · covered by test `test_rbac_execute_tool_success` & `gateway/main.py`
- AC-2: Gateway verifies decoded JWT `permissions` claim array contains target scope string · covered by `gateway/auth.py::get_verified_tenant` & `require_permission`
- AC-3: Missing scope returns HTTP 403 Forbidden with exact detail message · covered by `test_rbac_execute_tool_forbidden`
- AC-4: Authorization failure is recorded in telemetry with `user_id`, `tenant_id`, and `required_permission` · covered by `gateway/auth.py::require_permission`
