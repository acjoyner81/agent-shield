# 0005. Implement Granular RBAC Enforcement

**Date**: 2026-09-14
**Status**: Accepted

## Summary

Implement a strict permission checking layer that validates specific scope strings in the Auth0 JWT. The system uses a default deny posture, requiring every protected endpoint to explicitly declare its required permissions. This ensures that authenticated users can only perform actions they are explicitly authorized for, regardless of their tenant role.

## Context

With Auth0 integration complete, the gateway can verify identity and tenant ownership. However, it currently lacks the ability to restrict specific actions based on user permissions. For example, a "Developer" role should be able to execute tools but not modify billing settings.

Without this layer, any authenticated user within a tenant has full access to all that tenant's resources. We need a declarative way to enforce these boundaries at the gateway perimeter to prevent unauthorized tool execution and administrative changes.

## Requirements

**User stories**:
- As a tenant administrator, I want to restrict tool execution to only those users with the "tools:execute" permission.
- As a security auditor, I want every unauthorized access attempt to be logged with the missing permission and the user identity.

**Acceptance criteria**:
- **AC-1**: Endpoints declare explicit required scope dependencies using a `require_permission("...")` helper.
- **AC-2**: The gateway verifies that the decoded JWT `permissions` claim array contains the required target string.
- **AC-3**: If a user is authenticated but missing the required scope, the gateway returns an HTTP 403 Forbidden with the body `{"detail": "Permission denied: missing required scope '...' "}`.
- **AC-4**: Authorization failures are recorded in telemetry with `user_id`, `tenant_id`, and the `required_permission` attributes.

## Options considered

### Option 1: Declarative Route Dependencies (Recommended)

Use FastAPI dependency factories to enforce permissions at the route level.

**Pros**:
- Explicit contracts: the required permission is visible in the route definition.
- High performance: checks are simple set lookups in memory.
- Type safe: leverages FastAPI's dependency injection system.

### Option 2: Centralized Policy Map

Maintain a mapping of endpoints to permissions in a separate configuration file.

**Pros**:
- Centralized audit view of all permissions in one file.

**Cons**:
- Configuration drift: easy to change a route without updating the map.
- Added complexity in loading and parsing the map at runtime.

## Decision

**Chosen option**: Option 1: Declarative Route Dependencies

Implement a dependency factory that validates the presence of a required permission string in the request state.

**Implementation skills**: `fastapi` (`tiangolo/fastapi`, `pip install fastapi`)

## Rationale

Declarative route dependencies are the standard pattern for FastAPI applications. They provide the best balance of visibility and performance. Since Auth0 flattens roles into granular permissions in the JWT, the gateway does not need to manage complex role-to-permission mappings, making a simple set-lookup the most efficient and maintainable approach.

## Feature design

**Data model sketch**:
The authorization state is ephemeral and stored in the FastAPI `request.state`.
- `request.state.permissions`: `Set[str]` (extracted from JWT `permissions` claim).

**API surface**:
Permissions are enforced via dependencies on existing and new routes.

| Endpoint | Required Permission | Auth | Key Error |
|---|---|---|---|
| /v1/tools/execute | `tools:execute` | Bearer | 403 Forbidden |
| /v1/billing/* | `billing:admin` | Bearer | 403 Forbidden |

**Value sourcing**:
| Action | Value produced / displayed | Source |
|---|---|---|
| Permission Check | `has_permission` | `required_scope` in `request.state.permissions` |
| Authz Log | `required_permission` | Route dependency argument |

**Key invariants**:
- Default Deny: any route using `require_permission` must explicitly find the scope in the token to allow access.
- Order of Operations: Authentication (`get_verified_tenant`) must run before Authorization (`require_permission`).

**Security model**:
The system enforces a Zero Trust model. Even if a user is a valid member of a tenant, they are denied access to specific functionality unless their token contains the exact permission string required for that resource.

**Critical test scenarios**:
- Happy path: User with `tools:execute` in token successfully calls `/v1/tools/execute`, verifies **AC-1**, **AC-2**
- Failure case: User with valid token but missing `tools:execute` receives 403 and telemetry records the failure, verifies **AC-3**, **AC-4**
- Edge case: User with empty permissions array is denied access to all protected routes, verifies **AC-2**, **AC-3**

## Build plan

The build follows a Tracer Bullet approach, starting with a single protected route before rolling out the pattern.

1. Update `get_verified_tenant` in `gateway/auth.py` to extract the `permissions` claim and store it as a `set` in `request.state`, satisfies **AC-2**
2. Implement the `require_permission(scope: str)` dependency factory in `gateway/auth.py`, satisfies **AC-1**, **AC-2**
3. Implement the 403 Forbidden response and detailed error message for missing scopes, satisfies **AC-3**
4. Integrate `log_telemetry` into the permission check to record authorization failures, satisfies **AC-4**
5. Apply the `@require_permission("tools:execute")` dependency to the `/v1/tools/execute` endpoint to verify the full loop, satisfies **AC-1**, **AC-2**, **AC-3**, **AC-4**

## Consequences

**Positive**:
- Granular access control allows for "Least Privilege" user assignments.
- Zero-trust boundary at the gateway prevents lateral movement between tools.
- Audit trail for all unauthorized access attempts.

**Negative / tradeoffs**:
- Slight increase in route definition verbosity.
- Token size increases as more permissions are added to a user's identity.

## Follow-up

- [ ] Audit all `/v1/*` endpoints and assign appropriate required permissions.
- [ ] Implement a "Super Admin" override for internal maintenance.
