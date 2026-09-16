# 0004. Integrate Auth0 for identity and tenant verification

**Date**: 2026-09-14
**Status**: Accepted

## Summary

Secure all API endpoints by requiring a valid Auth0 identity token (a signed JSON Web Token that proves who the user is). The system verifies these tokens using a set of public keys from Auth0 and extracts the tenant ID to ensure users can only access their own data. This prevents users from pretending to be part of another tenant by simply changing a header.

## Context

The current gateway relies on a simple API key for tenant identification, which is insufficient for user level authentication and secure multi tenant isolation. As we move toward a production ready state, we need a standard way to verify the identity of the caller and bind that identity to a specific tenant.

Without this, the system is vulnerable to cross tenant impersonation, where a user with a valid key for one tenant could potentially access data from another tenant by manipulating request headers. We need a cryptographically secure way to tie a user to a tenant that cannot be forged by the client.

## Requirements

**User stories**:
- As a tenant user, I want to authenticate via Auth0 so that I can securely access my organization's AI tools.
- As a system administrator, I want to ensure that users cannot access tools belonging to other tenants.

**Acceptance criteria**:
- **AC-1**: Unauthenticated or invalid requests to `/v1/*` return HTTP 401 Unauthorized.
- **AC-2**: Valid Auth0 Bearer tokens are verified against the Auth0 JWKS endpoint (`/.well-known/jwks.json`).
- **AC-3**: Key Auth0 claims (`sub`, `https://agentshield.com/tenant_id`, `permissions`) are extracted and injected into the FastAPI request state.
- **AC-4**: The verified `tenant_id` from the token replaces any untrusted incoming `X-Tenant-ID` headers to prevent cross tenant impersonation.
- **AC-5**: Telemetry entries for authenticated requests automatically inherit the verified `tenant_id` and `user_id`.

## Options considered

### Option 1: In-memory JWKS Caching (Recommended)

Use PyJWT and PyJWKClient to fetch and cache the public keys from Auth0. Tokens are verified locally against these cached keys.

**Pros**:
- Extremely fast verification with no per request network call.
- Handles key rotation automatically via TTL (time to live).

**Cons**:
- Small memory overhead to store the key set.

### Option 2: External UserInfo Endpoint

Call the Auth0 `/userinfo` endpoint on every request to verify the token and fetch user details.

**Pros**:
- Guaranteed real time status of the user.

**Cons**:
- Adds significant latency (approx 100ms) to every single API call.
- Risks hitting Auth0 API rate limits under high load.

## Decision

**Chosen option**: Option 1: In-memory JWKS Caching

Implement local token verification using cached public keys from the Auth0 JWKS endpoint.

**Implementation skills**: `pyjwt` (`pyjwt/pyjwt`, `pip install pyjwt[crypto]`)

## Rationale

Local verification is the industry standard for high performance APIs. Since the tokens are signed by Auth0, we can trust them once the signature is verified against the public key. Caching the keys for 24 hours balances security (key rotation) with performance, ensuring the gateway does not become a bottleneck.

## Feature design

**Data model sketch**:
No new persistent entities are created. The identity is extracted from the JWT (JSON Web Token) and stored in the FastAPI `request.state` for the duration of the request.

**API surface**:
The authentication layer acts as a dependency for all `/v1/*` endpoints.

| Endpoint | Method | Key inputs | Key outputs | Auth | Key errors |
|---|---|---|---|---|---|
| /v1/* | ANY | Authorization: Bearer <token> | Verified User Context | Bearer | 401 Unauthorized |

**Value sourcing**:
| Action | Value produced / displayed | Source |
|---|---|---|
| Token Verification | `user_id` | `sub` claim from JWT |
| Tenant Resolution | `tenant_id` | `https://agentshield.com/tenant_id` claim from JWT |
| Context Binding | `X-Tenant-ID` | Verified `tenant_id` from JWT (overrides header) |

**Key invariants**:
- A request cannot reach a `/v1/*` handler without a verified `tenant_id`.
- The `X-Tenant-ID` used for telemetry and routing must always come from the token, never the raw request header.

**Security model**:
The system implements a Zero Trust approach at the perimeter. All requests to protected resources must present a valid JWT signed by the configured Auth0 domain. The tenant isolation is enforced by the `tenant_id` claim, which is immutable once the token is issued.

**Configuration required**:
- `AUTH0_DOMAIN`: The Auth0 tenant domain (e.g. `dev-xxx.us.auth0.com`).
- `AUTH0_AUDIENCE`: The intended audience for the token (the API identifier).

**Critical test scenarios**:
- Happy path: Valid token for `tenant_alpha` allows access and logs telemetry as `tenant_alpha`, verifies **AC-2**, **AC-3**, **AC-5**
- Failure case: Expired or malformed token returns 401, verifies **AC-1**
- Auth/permission: Token for `tenant_alpha` attempts to use `X-Tenant-ID: tenant_beta`; request is processed as `tenant_alpha`, verifies **AC-4**

## Build plan

The build follows a Tracer Bullet approach, establishing the security loop before adding complex role checks.

1. Install `pyjwt[crypto]` and configure Auth0 environment variables, satisfies **AC-2**
2. Implement the `Auth0Verifier` utility to fetch and cache JWKS keys, satisfies **AC-2**
3. Create the `get_current_user` FastAPI dependency to verify tokens and extract claims, satisfies **AC-1**, **AC-3**
4. Implement the `tenant_binding` middleware/dependency to override `X-Tenant-ID` with the verified claim, satisfies **AC-4**
5. Update the telemetry logging helper to pull `tenant_id` and `user_id` from the verified request state, satisfies **AC-5**

## Consequences

**Positive**:
- Cryptographically secure tenant isolation.
- Standardized identity management via Auth0.
- High performance verification with negligible latency.

**Negative / tradeoffs**:
- Dependency on Auth0 availability for new token issuance (though existing tokens work via local cache).
- Complexity of managing custom claims in the Auth0 dashboard.

**Neutral**:
- Transition from API keys to Bearer tokens for all `/v1/*` endpoints.

## Follow-up

- [ ] Design and implement RBAC (Role Based Access Control) using the `permissions` claim extracted in this spec.
