# Verify: Tenant Rate Limiting & Isolation · spec 0006 · updated 2026-09-16
_Steps derived from spec 0006 acceptance criteria. `/check verify` runs these; `/test` locks the durable ones._

## Commands
- [x] `curl -i -H "Authorization: Bearer dev-mock-token" http://localhost:8000/api/v1/protected` → Returns HTTP 200 with `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `X-RateLimit-Reset` headers → AC-1, AC-2
- [x] Exhaust tenant capacity and send request → Returns HTTP 429 Too Many Requests with `Retry-After` header and `{"detail": "Tenant rate limit exceeded"}` payload → AC-3
- [x] Inspect gateway telemetry log stream when 429 triggered → Contains structured log `event="rate_limit_exceeded"` with tenant_id, user_id, and endpoint → AC-4
- [x] `curl -i http://localhost:8000/health` → Returns HTTP 200 without evaluating rate limits or setting `X-RateLimit-*` headers → AC-5

## Acceptance-criteria coverage
- AC-1 covered by step 1
- AC-2 covered by step 1
- AC-3 covered by step 2
- AC-4 covered by step 3
- AC-5 covered by step 4
