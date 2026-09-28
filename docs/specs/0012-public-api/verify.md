# Verify: Public API · spec 0012 · updated 2026-09-28
_Steps derived from spec 0012 acceptance criteria. `/check verify` runs these; `/test` locks the durable ones._

## Prerequisite
`gateway-python` bakes its image at build time with no source volume, so a running container can be
several specs behind. Rebuild first or every result below is from stale code:
`docker compose up -d --build gateway-python`

## Schema surface
- [x] `curl -s localhost:8000/openapi.json | jq -r '.paths | keys[]'` → exactly `/v1/chat/completions`, `/v1/keys`, `/v1/keys/{key_id}`, `/v1/keys/{key_id}/rotate`, `/v1/tools/execute`, `/v1/usage/summary` → AC-5
- [x] Same command, looking for `/v1/health`, `/v1/telemetry`, `/v1/billing`, `/v1/webhooks`, or any `/api/` prefix → none present → AC-5
- [x] `curl -s localhost:8000/openapi.json | jq '.components.securitySchemes | keys'` → `bearer` and `apiKey`, not the bare `HTTPBearer`/`APIKeyHeader` names → AC-2
- [x] Open `/docs` in a browser → every contract operation shows a summary, a description, and at least one request or response example, with no console errors and no failed network requests → AC-1
- [x] Read the security section text → it states the Bearer wins precedence over a key and that a caller supplied `X-Tenant-ID` is never authoritative → AC-2, AC-3
- [x] Read every example value → none look like a real credential, no `sk_live_` shape, no long opaque secret → AC-1

## Machine access (needs a real key from Redis)
Create one as a tenant, then use the returned secret on each contract route.
- [x] `GET /v1/usage/summary`, `GET /v1/keys`, `POST /v1/chat/completions`, `POST /v1/tools/execute` with `X-Tenant-API-Key: $KEY` → all 200, none 401 → AC-4
- [x] `GET /v1/usage/summary` with the key plus `X-Tenant-ID: tenant_someone_else` → the response `tenant_id` is the store tenant from the key, not the header value → AC-3
- [x] `GET /v1/usage/summary` with a valid `Authorization: Bearer` and a garbage `X-Tenant-API-Key` → 200, the key was never consulted → AC-2
- [x] `GET /v1/usage/summary` with a garbage `Authorization: Bearer` and a valid key → 401, the header is checked first and the key is not used as a fallback → AC-2
- [x] `POST /v1/tools/execute` with a key that has no `tools:execute` permission → 403, not 401 (authenticated but unauthorized) → AC-4
- [x] `POST /v1/keys/{key_id}/rotate` with a key → 200 and a new secret; the old key still works inside the grace window and the new key works immediately → AC-4
- [x] `DELETE /v1/keys/{key_id}` with a key, then reuse the revoked secret on `/v1/usage/summary` → 401 → AC-4

## Commands
- [x] `python -m pytest gateway/tests/ tests/ worker/tests/ -q` → 222 passed → AC-1 through AC-5
- [x] `cd portal-frontend && npx ng build` → succeeds, only the pre-existing initial bundle budget warning → AC-1
- [x] `python -m pytest gateway/tests/test_public_api_schema.py gateway/tests/test_public_api_machine_access.py -q` → 23 passed (15 schema, 8 machine access) → AC-1 through AC-5

## Result
All acceptance criteria verified against the rebuilt deployment, not only against the test
suite. The machine key path and the precedence rule were confirmed on the live stack with a real
key in real Redis, which is the check the unit tests cannot make. Two throwaway keys were created
and revoked during this run; revoked tombstones are expected to remain per spec 0010.
