# Verify: Public API · spec 0012 · updated 2026-09-28

_Steps derived from spec 0012 acceptance criteria. `/check verify` runs these; `/test` locks the durable ones._
Rewritten after the 2026-09-28 review (see [review](../../reviews/2026-09-28-public-api.md)): the two
blockers are fixed, and every line below was re-run against the rebuilt stack, not inherited from the
previous record.

## Prerequisite
`gateway-python` bakes its image at build time with no source volume, so a running container can be
several specs behind. Rebuild first or every result below is from stale code:
`docker compose up -d --build gateway-python`

## Schema surface
- [x] `curl -s localhost:8000/openapi.json | jq -r '.paths | keys[]'` → exactly `/v1/chat/completions`, `/v1/keys`, `/v1/keys/{key_id}`, `/v1/keys/{key_id}/rotate`, `/v1/tools/execute`, `/v1/usage/summary` → AC-5
- [x] Same command, looking for `/v1/health`, `/v1/telemetry`, `/v1/billing`, `/v1/webhooks`, or any `/api/` prefix → none present → AC-5
- [x] Every `#/components/schemas/*` reference resolves to a published component and nothing is unreferenced; `CheckoutRequest` (the excluded checkout route's model) is gone → AC-5
- [x] `curl -s localhost:8000/openapi.json | jq '.components.securitySchemes | keys'` → `bearer` and `apiKey`, not the bare `HTTPBearer`/`APIKeyHeader` names → AC-2
- [x] A lifted media-type example carries `description` alongside `summary` and `value` → AC-1
- [x] Open `/docs` in a browser → every contract operation shows a summary, a description, and at least one request or response example, with no console errors and no failed network requests → AC-1
- [x] Read the security section text → it states the Bearer wins precedence over a key and that a caller-supplied `X-Tenant-ID` is never authoritative → AC-2, AC-3
- [x] Read the `APIKeyResponse` example → `key_prefix` is the real masked shape `sk_live_••••••••••••unqb`, not a placeholder → AC-1
- [x] Read every example value → none look like a real credential, no `sk_live_` shape, no long opaque secret → AC-1

## Machine access (needs a real key from Redis)
Create one as a tenant, then use the returned secret on each contract route.
- [x] `GET /v1/usage/summary`, `GET /v1/keys`, `POST /v1/chat/completions`, `POST /v1/tools/execute` with `X-Tenant-API-Key: $KEY` → all 200, none 401 → AC-4
- [x] `GET /v1/usage/summary` with the key plus `X-Tenant-ID: tenant_someone_else` → the response `tenant_id` is the store tenant from the key, not the header value → AC-3
- [x] `GET /v1/usage/summary` with a valid `Authorization: Bearer` and a garbage `X-Tenant-API-Key` → 200, the key was never consulted → AC-2
- [x] `GET /v1/usage/summary` with a garbage `Authorization: Bearer` and a valid key → 401, the header is checked first and the key is not used as a fallback → AC-2
- [x] `GET /v1/usage/summary` with `Authorization: Basic …` and a valid key → 401 "Unsupported or malformed Authorization header" → AC-2
- [x] `GET /v1/usage/summary` with `Authorization: bearer dev-mock-token` (lowercase scheme) → 200, not 401 → AC-2
- [x] `POST /v1/tools/execute` with a key that has no `tools:execute` permission → 403, not 401 (authenticated but unauthorized) → AC-4
- [x] `POST /v1/keys/{key_id}/rotate` with a key holding `keys:write` → 200 and a new secret; the old key still works inside the grace window and the new key works immediately → AC-4
- [x] `DELETE /v1/keys/{key_id}` with a key holding `keys:write` → 204, and the revoked secret is then 401 → AC-4

## Privilege escalation (the review blockers)
- [x] A key with `permissions: []` calling `POST /v1/keys` → 403 `Missing required permission: keys:write` (was 201 with a live secret) → AC-4
- [x] The same key calling `POST /v1/keys/{key_id}/rotate` and `DELETE /v1/keys/{key_id}` → 403, and the sibling key it named is still `Active` in Redis → AC-4
- [x] A key holding only `keys:write` requesting `permissions: ["billing:admin"]` → 403 `Cannot grant scopes you do not hold: billing:admin` → AC-4
- [x] The same key requesting `permissions: ["keys:write"]` → 201, so the gate is a subset check and not a blanket ban → AC-4
- [x] `GET /v1/usage/summary` with a key holding `billing:admin` → `totals.estimated_cost_usd` populated; the same route with a key holding only `tools:execute` → null → AC-4

## Tenant isolation at the limiter (the review blocker)
- [x] `GET /v1/usage/summary` with no credential and `X-Tenant-ID: tenant_someone_else` → 401 → AC-3
- [x] The same with `X-Tenant-API-Key: key_alpha_123` (a hardcoded literal in `TENANT_CONFIG`) → 401 → AC-3
- [x] `docker exec agentshield-redis redis-cli keys 'rate_limit:*'` after both → empty, so neither call debited anyone's bucket → AC-3
- [x] Exhaust `rate_limit:tenant_victim`, then call anonymously with `X-Tenant-ID: tenant_victim` → 401, not 429, and the victim bucket is unchanged → AC-3
- [x] An authenticated call still debits: three `Bearer dev-mock-token` calls return `x-ratelimit-remaining` 59, 58, 57 and create `rate_limit:tenant_alpha` → AC-3

## Commands
- [x] `python -m pytest gateway/tests/ tests/ worker/tests/ -q` → 233 passed → AC-1 through AC-5
- [x] Suite is hermetic: `gateway/tests/conftest.py` redirects every Redis path at a per-test fakeredis, and a test that opens a real connection fails the run. The host store still holds a stale `tenant:over_limit:tenant_alpha` from before, which no longer affects any result → AC-3
- [x] `cd portal-frontend && npx ng build` → succeeds, only the pre-existing initial bundle budget warning → AC-1
- [x] `python -m pytest gateway/tests/test_public_api_schema.py gateway/tests/test_public_api_machine_access.py -q` → 33 passed (16 schema, 17 machine access) → AC-1 through AC-5

## Result
All five acceptance criteria verified against the rebuilt deployment, not only against the test
suite. Both blockers from the review are closed and re-proven live: no key can mint a credential more
privileged than itself, and no unauthenticated caller can spend another tenant's rate limit budget or
read its state from a status code. Five throwaway keys were created during this run and revoked;
revoked tombstones are expected to remain per spec 0010.
