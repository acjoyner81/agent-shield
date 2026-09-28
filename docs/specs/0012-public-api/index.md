# 0012. Public API

**Date**: 2026-09-24
**Status**: In Progress

## Summary

This feature makes the gateway a documented surface that external developers can integrate against. The two halves the scope promised are mostly shipped already: tenants can generate API keys in the portal, and those keys authenticate machine calls to the gateway. What is left is the public contract: a curated OpenAPI schema at `/docs` that describes the machine access routes, both authentication modes, the permission scopes, and real examples, with operational routes kept out of the schema.

## Requirements

**User stories**:
- As an external developer, I want to read the API reference at `/docs` and see how to call the gateway with an API key so that I can integrate our agents without asking the product team.
- As a tenant administrator, I want the machine access contract to be explicit so that I know which routes accept keys and what permissions they require.
- As a security reviewer, I want the schema to hide internal routes and show no example secrets so that the published surface invites safe use.

**Acceptance criteria** (the contract):
- **AC-1**: `/docs` and `/openapi.json` are publicly accessible and describe the four machine contract routes (chat completions, tools execute, usage summary, keys) with per operation summaries, tags, and request or response examples.
- **AC-2**: The OpenAPI schema declares both authentication modes: the Bearer JWT for user principals and the `X-Tenant-API-Key` header for machine principals, and documents that when both are present the Bearer token wins.
- **AC-3**: The schema documents that a caller supplied `X-Tenant-ID` header is never authoritative; the tenant always comes from the verified JWT claim or the key store.
- **AC-4**: Machine access on the four contract routes works end to end with `X-Tenant-API-Key` and permission scopes enforced, locked by the existing key rotation and RBAC tests.
- **AC-5**: Operational and internal routes are excluded from the public schema (health, telemetry logs ingest, billing webhook, and the `/api` portal aliases), and no OpenAPI example contains a real secret or token.

## Decision

**Chosen option**: Curate the existing FastAPI OpenAPI surface and lock the machine contract with schema tests.

Publish the API by polishing, not rebuilding: register the expected machine contract and example payloads, mark operational routes `include_in_schema=False`, declare the Bearer and API key security schemes with the precedence rule, and add a regression test that asserts the generated schema.

**Implementation skills**: no new community skills are required. The build uses the existing FastAPI and Angular conventions in the repository, plus the key and permission machinery from specs 0005 and 0010.

## Feature design

**Data model sketch**:
No data model changes. This feature affects the documentation and schema metadata of existing routes and the OpenAPI test surface only.

**State transitions**:
Not applicable.

**API surface**:

The published machine contract is these four existing routes, already verified by specs 0010 and 0005:

| Endpoint | Method | Auth modes | Required scope | Existing |
|---|---|---|---|---|
| /v1/chat/completions | POST | Bearer, X-Tenant-API-Key | none (quota gate) | yes |
| /v1/tools/execute | POST | Bearer, X-Tenant-API-Key | tools:execute | yes |
| /v1/usage/summary | GET | Bearer, X-Tenant-API-Key | none (tenant scoped; cost fields need billing:admin) | yes |
| /v1/keys | GET | Bearer, X-Tenant-API-Key | none (tenant scoped) | yes |
| /v1/keys | POST, DELETE; /v1/keys/{key_id}/rotate | Bearer, X-Tenant-API-Key | keys:write | yes |

Operational routes excluded from the schema, via `include_in_schema=False` and a path filter in `custom_openapi`: `/health`, the `/api/v1/*` portal aliases, `/v1/billing/webhook`, `/v1/billing/checkout`, `/v1/billing/portal`, and the telemetry logs ingest surface.

**Machine access on all four routes**: the contract table promises a key on every route, so
`/v1/keys` and `/v1/usage/summary` authenticate through `resolve_active_tenant` (Bearer, else key)
rather than `get_verified_tenant` (JWT only). This is a deliberate widening: a machine key can now
read its own usage. Key management is narrower than the rest, because writing credentials is the
one operation that can widen a principal's own reach:

- `POST /v1/keys`, `POST /v1/keys/{key_id}/rotate`, and `DELETE /v1/keys/{key_id}` require the
  `keys:write` scope, and
- `POST /v1/keys` only grants scopes the caller already holds, so no credential can mint a
  successor more privileged than itself (rejected with `403 Cannot grant scopes you do not hold`).

A key that holds `keys:write` therefore manages keys for its own tenant, within its own scopes. It
can never widen them. Key creation is no longer "the portal's single surface"; it is the portal
plus any credential holding `keys:write`, which the published schema now states.

**Value sourcing** (every value the schema produces names its source):

| Action | Value produced / displayed | Source |
|---|---|---|
| Publish schema | route list, methods, request and response models | FastAPI introspects registered routers |
| Publish schema | operation summary and description | documented per endpoint (`summary=`, `description=`) |
| Publish schema | request and response examples | `examples=` on pydantic request models and response models |
| Publish schema | Bearer security scheme | FastAPI `HTTPBearer` already registered |
| Publish schema | API key security scheme | `APIKeyHeader` (`X-Tenant-API-Key`) registered in the schema components |
| Publish schema | Bearer wins precedence note | OpenAPI description in the security section, mirroring `resolve_active_tenant` in `gateway/auth.py` |
| Publish schema | excluded routes | `include_in_schema=False` on operational endpoints |
| Schema test | assertions on routes, schemes, examples | the generated `app.openapi()` dict at test time |

**Key invariants**:
- The public schema never contains an internal route, an unreferenced component model, or a real credential.
- The precedence rule documented in the schema matches the runtime, otherwise the docs lie.
- The `X-Tenant-ID` rule holds at the limiter too: no bucket is debited and no `429` is raised for a request that has not authenticated.
- Every contract route works with the API key at runtime, not only in the schema, which the regression tests keep true.
- No credential can grant a scope it does not already hold.

**Security model**:
The schema is public and read only. It exposes endpoint shapes and error codes, no tenant data and no secrets. Authentication stays exactly as shipped: JWT for users, hashed API keys for machines, default deny permission checks on routed scopes. Making `/docs` public is the scope's own acceptance bar and accelerates developer onboarding; the blast radius of a public schema is an endpoint inventory, which the operational exclusions keep minimal.

**Configuration required**:
None. No new environment variables or credentials.

**Critical test scenarios**:
- Happy path: `GET /openapi.json` returns a schema whose paths are exactly the contract routes and exclude health, the webhook, and the `/api` aliases, verifies **AC-1**, **AC-5**
- Auth schemes: the schema components declare both `bearer` and the `X-Tenant-API-Key` apiKey scheme, and the precedence note is present, verifies **AC-2**
- Examples: every contract operation carries a summary, tags, a description, and a request or response example, verifies **AC-1**
- Machine access: a key with `tools:execute` reaches each contract route without a 401; a key without the scope gets 403 on `/v1/tools/execute`, verifies **AC-4**
- Precedence: a request with both a valid Bearer token and a key binds the JWT tenant and never consults the key store, verifies **AC-2**, **AC-3**
- Tenant source: a valid key plus a spoofed `X-Tenant-ID` still binds the store tenant, verifies **AC-3**
- Lifecycle: a revoked machine key is rejected on the contract routes, verifies **AC-4**
- Secrets: scanning the generated schema finds no example that looks like a live secret or token, verifies **AC-5**
- Escalation: a key with no scopes gets `403` on `POST /v1/keys`, and a key holding only `keys:write` gets `403` when it asks for a `billing:admin` key, verifies **AC-4**
- Limiter: an anonymous request carrying a spoofed `X-Tenant-ID` writes no `rate_limit:*` state, and an exhausted victim bucket still yields `401` rather than `429`, verifies **AC-3**

## Build plan

Ordered as thin vertical slices per the project default (Tracer Bullet), getting a generatable, testable schema early and only then curating content.

1. [x] Register the Bearer and `X-Tenant-API-Key` security schemes into the FastAPI schema components and keep the existing `HTTPBearer` and `APIKeyHeader` wiring, satisfies **AC-2**
2. [x] Mark operational routes `include_in_schema=False` (health, webhook, telemetry ingest, `/api` aliases, billing checkout and portal) so the public paths are the contract routes only, satisfies **AC-5**
3. [x] Add per endpoint operation summaries and descriptions plus request and response examples on the four contract routes, satisfies **AC-1**
4. [x] Document the Bearer wins precedence and the spoofed header rule in the schema security description, matching `resolve_active_tenant`, satisfies **AC-2**, **AC-3**
5. [x] Add `gateway/tests/test_public_api_schema.py` that asserts route inclusion, scheme registration, precedence text, the spoof rule text, and the absence of secret shaped examples, satisfies **AC-1** through **AC-5**
6. [x] Add `gateway/tests/test_public_api_machine_access.py` proving a key reaches all four contract routes, that a spoofed `X-Tenant-ID` is ignored, that Bearer beats a key, and that scopes and key lifecycle still apply, satisfies **AC-2**, **AC-3**, **AC-4**
7. [x] Add a developer quick start to the portal (a short `/developers` note or the keys screen helper) linking to `/docs` with a curl example, optional smoke polish for the feature, satisfies **AC-1**

## Consequences

**Positive**:
- External developers get a self serve reference instead of asking for integration help.
- The machine contract is explicit and regression locked, so docs and runtime cannot drift.
- Internal routes disappear from the published surface, a real security cleanliness win.

**Negative / tradeoffs**:
- A public schema exposes the endpoint inventory to anyone who looks; the operational exclusions shrink but do not remove that surface.
- Maintainers must keep operation descriptions current or the docs stale again.
- Schema tests add a small maintenance surface to every endpoint change.

**Neutral**:
- `/docs` was already reachable; this feature makes it intentional.
- Key management is no longer portal-only: any credential holding `keys:write` can manage keys, bounded by its own scopes.

## Follow-up

- [ ] Consider a programmatic key onboarding endpoint later; deliberately deferred so key creation stays behind `keys:write`.
- [ ] Add a developer docs page in the portal for larger integrations; the quick start note is the MVP seed.

## Rationale

Reasoning, options, and the decision record: see [rationale.md](rationale.md).