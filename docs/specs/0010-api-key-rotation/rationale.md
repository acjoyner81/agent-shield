# 0010. API Key Rotation — Decision record

**Date**: 2026-09-23
**Status**: Decision record for the build spec in `index.md`.

## Context

The gateway has two credential styles. User access runs through Auth0 JWTs verified at the perimeter with permission scopes enforced by `require_permission` (spec 0005). Machine access runs through API keys, but the key machinery is incomplete. `gateway/keys.py` creates keys, lists them, and deletes them, yet the raw secret is never persisted anywhere, only a masked prefix and an internal key id. A client that presents a key cannot be looked up, so no route can actually authenticate a key. The `require_api_key` dependency in `gateway/auth.py` only checks that the header exists. Two ad hoc key stores built for other flows (`tenant:key:{api_key}` from Stripe webhooks, `apikey:{api_key}` in the end to end test) are never read at request time and store secrets in the clear.

The practical failure is rotation. Rotating a credential today is revoke then create. Every agent and client holding the old key fails instantly, which is exactly when a tenant administrator rotates a compromised or expiring key: in the middle of live traffic. There is no transition path, no grace window, no audit record of when a key stopped being trusted, and no way in Splunk to see a stale key still being used. For SOC 2 readiness (named in the project context) the credential lifecycle and its audit trail should be first class.

## Options considered

### Option 1: Raw secret index (status quo pattern)

Store each secret as a Redis string keyed by its value, the pattern `stripe_webhook.py` already uses. Lookup is a single `GET`.

**Pros**:
- Trivial to build and debug.

**Cons**:
- A Redis leak or backup copy exposes every live credential in the clear.
- No lifecycle states; rotation still means create plus delete with no transition.
- No way to tell "never existed" from "revoked", which breaks audit.

### Option 2: Hashed index with a statused lifecycle (recommended)

Store only a SHA-256 digest of each secret plus a statused meta record (`Active`, `Rotated`, `Revoked`) per tenant, with a verification dependency that enforces the grace window and permission scopes.

**Pros**:
- No raw secret at rest; a leak of the key store does not leak credentials.
- Rotation, grace, and revocation are first class states instead of create plus delete.
- Keys can carry permission scopes, giving machine credentials the same RBAC contract as users.
- Audit trail for SOC 2 via telemetry events.

**Cons**:
- Requires a transactional pair of writes (meta hash plus digest index) on each lifecycle action.
- Two legacy ad hoc key conventions remain in the codebase and need a migration or deprecation note.

### Option 3: Rotation without restraint (immediate only)

Rotation and revocation both take effect instantly; no grace window exists.

**Pros**:
- Simplest mental model and fewest code paths.

**Cons**:
- Rotating a credential breaks every live client the moment it happens, the exact downtime this feature exists to remove.
- Confirmed against scope: the gateway operator explicitly wants a transition window.

### Option 4: Scopes fixed at creation (revoke and recreate to change them)

A key's allowlist is set once when the key is created. Changing access means creating a new key and revoking the old one.

**Pros**:
- The smallest possible surface: one write that sets scopes, no update path to secure.
- No window where a key's reach changes under a caller that already holds the secret.

**Cons**:
- Narrowing a key's access becomes an outage: every client holding the old secret must redeploy with a new one, which is the same downtime this spec exists to remove for rotation.
- The portal has no real "manage" action to offer, so the natural UI is a decorative control, which is what the page shipped before this was added.
- Auditing a key's access history is harder, because the change is a delete plus a create rather than one attributed scope change.

## Decision

**Chosen option**: Option 2: Hashed index with a statused lifecycle.

Implement a SHA-256 hashed key store with `Active` / `Rotated` / `Revoked` lifecycle states, a configurable grace period, a `verify_api_key` dependency wired into live routes, a rotate endpoint, a rescope endpoint, tombstone revocation, and an `agentshield.security.key_rotation` telemetry event. Keys optionally carry a permissions allowlist and are enforced by the existing `require_permission` machinery.

## Rationale

The failure mode that matters is rotation under live traffic: a tenant admin rotates a key exactly when it is in use, so the gateway needs a transition path, not a wall. The grace window handles that. Storing hashes instead of raw secrets removes the single worst breach scenario for a credential store and costs one `hashlib.sha256` call per request, which is negligible against the existing Redis round trip. Making keys carry scopes reuses the RBAC contract from spec 0005 instead of inventing a parallel authorization model; a machine principal is just another principal with permissions. Revocation as a tombstone keeps audit history and lets the verify layer distinguish revoked from unknown, which is what the auditor story needs.

The rescope write (Option 4 weighed against keeping scopes fixed at creation) is the same argument as rotation applied to permissions: narrowing a key is a routine operation a tenant does for exactly the same reason it rotates one, and forcing a revoke plus recreate makes it an outage. The write is safe to add because the constraint that matters already exists. Creation refuses a scope the caller does not hold, and rescoping reuses that exact check, so the one power a key manager gains by editing rather than creating is the power to hand a sibling key reach no greater than its own. A `Revoked` key is a tombstone, so its scopes are frozen rather than editable, which keeps revocation final.
