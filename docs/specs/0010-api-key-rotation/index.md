# 0010. API Key Rotation with Grace Period

**Date**: 2026-09-23
**Status**: Accepted

## Summary

API keys today can be created and deleted, but the gateway never verifies a key at request time and rotating a credential means taking clients offline instantly. This spec adds a real key lifecycle: secrets stored as hashes only, a verify dependency wired into live routes, a rotate endpoint with a 24 hour grace window, immediate revocation with a tombstone, and a typed telemetry event for rotation activity. Keys can carry permission scopes, so machine credentials work on RBAC gated routes under the same rules as user tokens.

A key's scope allowlist is access state that changes over time, so the lifecycle also needs a write that changes it. `PATCH /v1/keys/{key_id}` sets a key's scopes (the portal's "Manage" control), under the same `keys:write` gate and the same no escalation rule as creation, and emits a `key_rotation` event carrying the new scope set.

## Requirements

**User stories**:
- As a tenant administrator, I want to rotate an API key without taking active agents offline so that clients can roll over during a known transition window.
- As a gateway operator, I want every presented API key verified at the perimeter so that revoked, expired, or unknown keys are rejected immediately.
- As a security auditor, I want rotation, grace period use, and revocation recorded as telemetry so that stale or compromised credentials are traceable in Splunk.
- As a tenant administrator, I want to change what a key is allowed to do after it is created, so that access tracks what an integration actually needs rather than forcing a new key on every scope change.

**Acceptance criteria** (the contract):
- **AC-1**: A presented key that exists in the store and is `Active` authenticates the request and binds the verified tenant.
- **AC-2**: Keys are stored as SHA-256 hashes only; the raw secret is returned once at creation and never written to Redis.
- **AC-3**: Rotating an `Active` key marks it `Rotated` with a `rotated_at` timestamp and creates a successor key with an incremented `version`; the new key authenticates immediately.
- **AC-4**: A `Rotated` key used inside the grace period (`API_KEY_GRACE_PERIOD_SECONDS`, default 86400) still authenticates and emits an `agentshield.security.key_rotation` event.
- **AC-5**: A `Rotated` key used after the grace period has elapsed is rejected with 401.
- **AC-6**: A revoked key is rejected immediately with 401, and revocation is recorded as a `Revoked` tombstone rather than a hard delete.
- **AC-7**: Keys carry an optional permissions allowlist; a key authenticated on an RBAC gated route is subject to the exact same `require_permission` enforcement as a JWT principal.
- **AC-8**: Unknown keys are rejected with 401 and the bound tenant always comes from the key store, never from a client supplied header.
- **AC-9**: `PATCH /v1/keys/{key_id}` replaces a key's scope allowlist with the submitted set, requires `keys:write`, and cannot grant a scope the caller does not hold itself (403 naming the scope).
- **AC-10**: A scope change to a `Revoked` key is rejected with 409 and leaves the stored allowlist unchanged; an unknown `key_id` is 404; and a successful change emits an `agentshield.security.key_rotation` event carrying the new `permissions` set.

## Decision

**Chosen option**: Option 2: Hashed index with a statused lifecycle.

Implement a SHA-256 hashed key store with `Active` / `Rotated` / `Revoked` lifecycle states, a configurable grace period, a `verify_api_key` dependency wired into live routes, a rotate endpoint, tombstone revocation, a rescope endpoint, and an `agentshield.security.key_rotation` telemetry event. Keys optionally carry a permissions allowlist and are enforced by the existing `require_permission` machinery.

## Feature design

**Data model sketch**:

All state lives in Redis. No schema migration is needed.

- `tenant:keys:{tenant_id}` hash: `key_id` -> JSON meta
  - `name`: string (human label)
  - `prefix`: masked prefix for display
  - `created_at`: ISO 8601 UTC
  - `status`: `Active` | `Rotated` | `Revoked`
  - `version`: int, starts at 1, increments per rotation
  - `secret_hash`: SHA-256 hex of the raw secret
  - `rotated_at`: ISO 8601 UTC, present only when `Rotated`
  - `superseded_by`: `key_id`, present only when `Rotated`
  - `permissions`: optional list of scope strings

- `apikey:{sha256(secret)}` string: JSON `{"tenant_id": "...", "key_id": "..."}` (the O(1) verification index).

The legacy `tenant:key:{api_key}` and `apikey:{api_key}` conventions are not part of this model and are handled as a follow up.

**State transitions**:
- `Active` -> `Rotated`: on rotate, `rotated_at` set, a successor key created, grace timer starts at `rotated_at`.
- `Active` -> `Revoked`: on revoke, immediate, no grace.
- `Rotated` -> `Revoked`: on revoke during the grace window, immediate.
- `Rotated` -> expired: enforced at verify time by comparing `rotated_at + API_KEY_GRACE_PERIOD_SECONDS` against server UTC; no background sweep needed.
- Scopes change in place for `Active` and `Rotated` keys; a `Revoked` key is a tombstone and its scopes are frozen (409 on write).

**API surface** (extends the existing `/v1/keys` router):

| Endpoint | Method | Key inputs | Key outputs | Auth | Key errors |
|---|---|---|---|---|---|
| /v1/keys | POST | name:string (req), permissions:list (opt) | key_id, secret_key (once), status, version | bearer | 401, 422 |
| /v1/keys | GET | none | list with status, version, rotated_at, permissions | bearer | 401 |
| /v1/keys/{key_id}/rotate | POST | key_id, name:string (opt) | new key response + old rotated_at | bearer | 401, 404, 409 |
| /v1/keys/{key_id} | PATCH | key_id, permissions:list (req, complete set) | key_id, status, version, permissions | bearer | 401, 403, 404, 409 |
| /v1/keys/{key_id} | DELETE | key_id | 204 tombstone | bearer | 401, 404 |
| /v1/tools/execute | POST | header `X-Tenant-API-Key` | existing | bearer or API key | 401, 403 |
| /v1/chat/completions | POST | header `X-Tenant-API-Key` | existing | bearer or API key | 401 |

The rotate endpoint rejects a `Rotated` key with 409 (it already has a successor). Revoke accepts both `Active` and `Rotated` keys. `PATCH` requires `keys:write`, replaces the whole allowlist (no partial merge, so the result never depends on what the key already held), and refuses a scope the caller does not hold.

**Portal alias**: the router is mounted a second time under `/api` for the portal, so every route here is reachable at `/api/v1/keys/...` too. The alias carries the same `keys:write` gate; `gateway/tests/test_portal_integration.py` proves it.

**Value sourcing** (every produced value names its source):

| Action | Value produced / displayed | Source |
|---|---|---|
| Verify key | tenant_id | `apikey:{sha256(secret)}` index value |
| Verify key | status | `tenant:keys:{tenant_id}` -> meta `status` |
| Verify key | grace decision | Derived from meta `rotated_at` + `API_KEY_GRACE_PERIOD_SECONDS` vs server clock |
| Verify key | permissions | meta `permissions` (default empty set) |
| Rotate | successor version | meta `version` + 1 |
| Rotate | rotated_at | server UTC at invocation |
| Rotate | successor key_id | generated secret id |
| Rescope | new allowlist | the request `permissions` list (the complete set), validated against `request.state.permissions` (the caller's own scopes, bound from the token claim or key record by `resolve_active_tenant`) |
| Rescope | granted vs refused scopes | set difference: requested scopes minus `request.state.permissions` |
| Rescope | response status/version/permissions | meta `status`, `version`, and the just written `permissions` |
| Rescope | audit event scopes | the written `permissions` list, passed to `emit_key_rotation` |
| Revoke | tombstone status | meta `status` set to `Revoked`, index deleted |
| Audit event | key_id, action, status | action (rotate, revoke, grace, create, scopes_updated) + meta |

**Key invariants**:
- The raw secret is never written to Redis; only its SHA-256 digest.
- A key maps to exactly one tenant; the tenant comes from the index, never from request headers.
- Revocation is always immediate. The grace window applies only to `Rotated` keys, never to `Revoked` keys.
- Grace expiry is computed on server UTC at verify time.
- Default deny on RBAC routes: a key whose permissions lack the required scope gets 403, identical to a JWT principal.
- Meta writes and index writes are paired in one Redis pipeline so no orphan index survives a failed create, rotate, or revoke.
- When a request carries both a Bearer token and an API key, the Bearer token wins (user identity beats machine identity).
- No credential can be widened past its manager: the scopes a caller may grant (at create or rescope) are exactly those it holds itself. A machine key is a real principal, not a lesser one, so it manages keys for its own tenant but can never mint a successor more privileged than itself.
- A scope change is read check write under `WATCH` on the tenant key hash, so a concurrent rotation cannot be overwritten by a stale copy of the metadata.

**Security model**:

Keys are tenant scoped machine principals. Secrets are high entropy (`secrets.token_urlsafe(24)`), stored only as digests, and never logged. Permission enforcement reuses `require_permission` from spec 0005 without modification. Every lifecycle action (create, rotate, rescope, revoke) and every grace period use emits a telemetry event, giving Splunk the audit trail SOC 2 expects. The verify path binds the tenant from the store, so `X-Tenant-ID` spoofing by a client is impossible. Rescoping is a key write under the same `keys:write` gate as creation, and it is constrained by the same no escalation rule, so a key that manages keys can never use that power to grant a scope it does not itself hold.

The rescope path must not become a way to widen reach by editing rather than creating. Creation already refuses a scope the caller lacks; if rescoping did not, a principal holding only `keys:write` could raise a sibling key to `billing:admin` and use it, minting reach it never held. The escalation check is therefore stated once as an invariant and enforced on both writes.

**Configuration required**:
- `API_KEY_GRACE_PERIOD_SECONDS`: rotation grace window in seconds, default `86400` (24 hours).

**Critical test scenarios**:
- Happy path: create a key, present it, request authenticates with the bound tenant, verifies **AC-1**, **AC-2**
- Rotation: rotate the key, new key authenticates immediately, old key is `Rotated` with `rotated_at`, verifies **AC-3**
- Grace: present the old key inside the window, request passes and a `key_rotation` event is emitted, verifies **AC-4**
- Expiry: simulate a clock past `rotated_at + grace`, old key returns 401, verifies **AC-5**
- Revocation: revoke a key, present it, 401 immediately, tombstone persists, verifies **AC-6**
- RBAC: key without `tools:execute` hits `/v1/tools/execute` and gets 403; key with the scope succeeds, verifies **AC-7**
- Unknown key or spoofed header: unknown key returns 401 and the bound tenant is the store tenant, not the header value, verifies **AC-8**
- Rescope: `PATCH` a key's allowlist, then present it on a gated route; the new scopes take effect, verifies **AC-9**
- Rescope escalation: holding only `keys:write`, try to grant `billing:admin`; 403 names the scope and the stored allowlist is unchanged, verifies **AC-9**
- Rescope refusals: `PATCH` an unknown key (404), `PATCH` a `Revoked` key (409, allowlist unchanged), and `PATCH` without `keys:write` (403), verifies **AC-10**

## Build plan

Ordered as thin vertical slices per the project default (Tracer Bullet), standing up an end to end lifecycle thread early and wiring it into live routes last.

1. Add `API_KEY_GRACE_PERIOD_SECONDS` to `config/settings.py` (default 86400), satisfies **AC-4** — done
2. Add `agentshield.security.key_rotation` to the telemetry event registry and a `emit_key_rotation` helper in `gateway/telemetry.py`, satisfies **AC-4** — done
3. Refactor `gateway/keys.py`: create stores `secret_hash`, writes the `apikey:{digest}` index, records `version` and optional `permissions`; `GET /v1/keys` returns lifecycle fields; pipeline keeps meta and index atomic, satisfies **AC-1**, **AC-2** — done
4. Implement `POST /v1/keys/{key_id}/rotate` (successor creation, tombstone of old key as `Rotated`, version increment) and change `DELETE /v1/keys/{key_id}` to tombstone `Revoked` instead of `hdel`, satisfies **AC-3**, **AC-6** — done
5. Implement `verify_api_key` in `gateway/auth.py`: digest lookup, status and grace evaluation, permission set binding into `request.state`, emits the rotation event for grace use, satisfies **AC-1**, **AC-4**, **AC-5**, **AC-8** — done
6. Introduce `resolve_active_tenant` (Bearer first, API key fallback, 401 otherwise), wire it into `/v1/tools/execute` and `/v1/chat/completions`, and point the `require_permission` inner dependency at it so machine principals hit the same RBAC path, satisfies **AC-1**, **AC-7** — done
7. Write `gateway/tests/test_key_rotation.py` covering AC-1 through AC-8 and extend `test_rbac.py` with machine principal cases, satisfies **AC-1** through **AC-8** — done
8. Add `PATCH /v1/keys/{key_id}` (replace the scope allowlist under `keys:write`, refuse a caller unheld scope with 403, 404 unknown, 409 revoked, read check write under `WATCH`) and extend `emit_key_rotation` and `KeyRotationData` with an optional `permissions` list so a scope change is auditable, satisfies **AC-9**, **AC-10** — done
9. Cover the new route in `gateway/tests/test_public_api_machine_access.py` (replace, empty, escalation, missing `keys:write`, revoked 409, unknown 404) and add the `/api` portal alias to `test_portal_integration.py`, satisfies **AC-9**, **AC-10** — done
10. Wire the portal keys page: surface the signed-in principal's own token scopes, show each key's scopes on its row, and replace the fabricated roles card and its dead "Manage" button with a real scope editor that calls the new route, satisfies **AC-9** — done

## Consequences

**Positive**:
- Rotating credentials no longer takes live agents offline; clients roll during the grace window.
- A leaked key store leaks digests, not credentials.
- Machine credentials get the same RBAC contract as users, so tool execution can be safely driven by services.
- Every key lifecycle event is auditable in Splunk, supporting SOC 2.
- Revocation is immediate when it must be (compromise response) while rotation is graceful (routine renewal).
- Access can be narrowed or widened for an existing key without issuing a new credential, so a tenant can reduce a key's reach (for example after a scope is no longer needed) without an outage.

**Negative / tradeoffs**:
- A rotated key keeps working for up to 24 hours, which bounds but does not eliminate the exposure window of a compromised key that got rotated rather than revoked.
- Key authenticated requests add one `sha256` plus one Redis read to the path.
- Lifecycle actions need paired writes kept atomic, slightly more care than a single `hset`.
- A rescope replaces the whole allowlist, so a caller that sends a partial set silently drops the rest. There is no read modify write helper in the API; the client must send the set it wants to end with.

**Neutral**:
- Keys now carry lifecycle fields surfaced by `/v1/keys`.
- The legacy clear text key conventions remain and are tracked as a follow up.
- The portal keys screen now lists, creates, revokes, and rescopes keys. The "Manage" control on a key row opens the scope editor; the page no longer renders the fabricated workspace roles card it used to carry.

## Follow-up

- [ ] Migrate or deprecate the legacy key conventions (`tenant:key:{api_key}`, `apikey:{api_key}`) in favor of the hashed store.
- [ ] Surface rotation and revocation in the portal keys screen and the admin dashboard (scope features 9 and 10) with the new status, version, and rotate fields.
- [ ] Consider an optional `expires_at` field for time boxed keys (out of scope for this slice).