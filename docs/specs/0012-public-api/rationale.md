# 0012. Public API — Rationale

## Context

The scope feature promises two things: that `/docs` is accessible, and that API keys allow programmatic access to the gateway. Both are already largely true by accident of earlier work. FastAPI serves `/docs` and `/openapi.json` on every deploy, and spec 0010 shipped a real key lifecycle with `X-Tenant-API-Key` verified on `/v1/chat/completions` and `/v1/tools/execute`, with the portal keys screen as the creation surface.

What is missing is intentionality. The schema FastAPI generates is the raw inventory of every route, including operational surfaces a tenant should never see: health probes, the Stripe webhook, the telemetry ingest, and the `/api` aliases that only the portal uses. Nothing documents the two authentication modes, the Bearer over key precedence, or the rule that a client cannot pick its own tenant. The result is an API that works for tenants who already know it and invites misuse from everyone else.

For a platform sold to small businesses and the agencies that build on them, the API reference is onboarding material. A clear, curated, example rich schema turns `/docs` into the thing that sells integration hours, and a schema test turns that contract into something that cannot silently rot.

## Options considered

### Option 1: Curate the existing schema (Recommended)

Keep FastAPI's OpenAPI generation, register the auth schemes, exclude operational routes from the schema, add examples and operation metadata to the four contract routes, and lock it with a schema test.

**Pros**:
- Smallest change; the schema already exists and stays correct with the code.
- No new runtime surface; pure documentation and metadata.
- A generated contract that is regression tested cannot drift from the routes.

**Cons**:
- Maintainers must remember to describe new endpoints; the test catches omission only if written to.
- The public schema still lists the contract routes, which an attacker can enumerate.

### Option 2: Gate the schema behind authentication

Serve `/docs` and `/openapi.json` only to authenticated tenants.

**Pros**:
- Hides the endpoint inventory from anonymous callers.

**Cons**:
- Contradicts the scope's stated acceptance bar that `/docs` is accessible.
- A developer without a tenant yet cannot inspect the API, which is the exact onboarding moment.

### Option 3: A separate hand written documentation site

Build a dedicated developer docs site (or generated docs) decoupled from the live schema.

**Pros**:
- Full control over narrative and examples.

**Cons**:
- A second surface to maintain that drifts from the live routes.
- Heavier than the problem needs; the platform has no docs infra and the portal ships already.

## Rationale

Option 1 is the right size. The schema already exists and tracks the code exactly, so the cheapest way to keep truth is to polish and test it rather than duplicate it. FastAPI's `include_in_schema=False` gives the operational exclusions with one flag per route, and the security schemes are simply registered, matching the auth machinery that already runs. The precedence and tenant source rules are the two pieces of documentation that prevent real misuse, so they go in the schema text and in the regression test.

A hand written site (Option 3) is the classic second source of truth that rots, and gating the schema (Option 2) fights the feature's own acceptance criteria. Public, curated, tested wins for a developer focused small business product where self serve documentation substitutes for sales engineering time.

## References

**Project sources**:
- The scope feature intent: `docs/scope/scope.md`, Public API low level question 9
- Spec 0005 RBAC `require_permission` for the scope enforcement the contract relies on
- Spec 0010 API key rotation for the key store and verification path
- `gateway/auth.py` `resolve_active_tenant` for the Bearer over key precedence and tenant binding
- `gateway/main.py` for the routes and the `/api` portal aliases

**Practices & standards**:
- Live generated API documentation over hand maintained docs
- Regression tests against the published contract so docs cannot drift from behavior