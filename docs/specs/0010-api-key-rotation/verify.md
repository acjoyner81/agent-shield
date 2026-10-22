# Verify: API Key Rotation · spec 0010 · updated 2026-10-21
_Steps derived from spec 0010 acceptance criteria. `/check verify` runs these; `/test` locks the durable ones._

## Commands

Run with a live gateway (redis + Python gateway up) and `DEV_MODE=true` (bearer = `dev-mock-token`, tenant `tenant_alpha`).

- [x] `curl -X POST localhost:8000/v1/keys -H "Authorization: Bearer dev-mock-token" -d '{"name":"ci-key","permissions":["tools:execute"]}'` → 201 with `secret_key` shown once, `version: 1` → AC-1, AC-2
- [x] `redis-cli HGET tenant:keys:tenant_alpha <key_id>` → meta JSON: `status:"Active"`, `version:1`, `secret_hash` is the SHA-256 digest (raw secret absent) · `redis-cli GET apikey:<sha256(secret)>` → `{"tenant_id":"tenant_alpha","key_id":"..."}` → AC-2
- [x] `curl -X POST localhost:8000/v1/tools/execute -H "X-Tenant-API-Key: <secret>" -d '{"tool_name":"echo","params":{}}'` → 200 (MCP up) → AC-1
- [x] `curl -X POST localhost:8000/v1/keys/<key_id>/rotate -H "Authorization: Bearer dev-mock-token" -d '{}'` → 200, new `secret_key`, `version:2`, new key_id; present the new secret → 200 immediately → AC-3
- [x] Check old key still inside grace: present the **old** secret after rotation → 200 and an `agentshield.security.key_rotation` event with `data.action=="grace"` lands in Splunk → AC-4
- [x] Expire the window: set old key's `rotated_at` to `now − API_KEY_GRACE_PERIOD_SECONDS − 120` then present the old secret → 401 with "grace period expired" → AC-5
- [x] `curl -X DELETE localhost:8000/v1/keys/<key_id> -H "Authorization: Bearer dev-mock-token"` → 204; present the secret → 401; `redis-cli HGET tenant:keys:tenant_alpha <key_id>` still returns status `Revoked`; `GET /v1/keys` lists the tombstone → AC-6
- [x] Create key with `"permissions":["logs:read"]`, present it on `/v1/tools/execute` → 403; recreate with `["tools:execute"]` → 200 → AC-7
- [x] Present a garbage secret → 401 "Unknown API key"; present a valid secret plus `X-Tenant-ID: tenant_evil` → request still binds `tenant_alpha` (authz/telemetry tenant id) → AC-8
- [ ] Rotating a key twice → second rotate returns 409; rotating a revoked key → 409 (build guard) → API surface
- [x] `curl -X PATCH localhost:8000/v1/keys/<key_id> -H "Authorization: Bearer dev-mock-token" -d '{"permissions":["tools:execute","logs:read"]}'` → 200 with the new `permissions`; present that key on `/v1/tools/execute` → 200 → AC-9
- [x] Impersonate a lesser manager: mint a key holding only `keys:write` and present its secret on `PATCH /v1/keys/<other>`, granting `billing:admin` → 403 naming `billing:admin`; `redis-cli HGET tenant:keys:<tenant> <key_id>` shows the allowlist unchanged → AC-9
- [x] `PATCH` an unknown key_id → 404; `PATCH` a `Revoked` key → 409 with the stored allowlist untouched; `PATCH` without `keys:write` → 403; a successful `PATCH` lands an `agentshield.security.key_rotation` event with `data.action=="scopes_updated"` and `data.permissions` equal to the written set → AC-10

## Acceptance-criteria coverage

- AC-1: create + `X-Tenant-API-Key` on `/v1/tools/execute` → 200 (command 1, 3)
- AC-2: Redis inspection shows digest only, raw secret never written (command 2)
- AC-3: rotate returns version 2 successor; new key works now (command 4)
- AC-4: old key works inside grace + `key_rotation` grace event (command 5)
- AC-5: expired grace → 401 (command 6)
- AC-6: revoke → immediate 401 + `Revoked` tombstone (command 7)
- AC-7: permission allowlist enforced on RBAC route (command 8)
- AC-8: unknown → 401; spoofed tenant header never wins (command 9)
- AC-9: `PATCH` replaces the allowlist and takes effect on a gated route; a caller cannot grant a scope it lacks (commands 11, 12)
- AC-10: revoked 409 with allowlist frozen, unknown 404, and the `scopes_updated` event carries the new scope set (command 13)