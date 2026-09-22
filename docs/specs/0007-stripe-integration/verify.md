# Verify: Hybrid Stripe Integration · spec 0007 · updated 2026-09-22
_Steps derived from spec 0007 acceptance criteria. `/check verify` runs these; `/test` locks the durable ones._

## UI / manual
- [x] POST /v1/billing/checkout {price_id} → returns checkout.stripe.com URL → AC-3
- [x] POST /v1/billing/checkout (no price_id) → 400 "price_id is required" → AC-3
- [x] POST /v1/billing/portal (no customer) → 404 "No Stripe customer" → AC-4
- [x] POST /v1/billing/portal (existing customer) → returns portal_url → AC-4
- [x] POST /v1/billing/webhook (forged signature) → 400 "Invalid signature" → AC-5
- [x] POST /v1/billing/webhook (valid, subscription.created) → Redis entitlement:{tenant_id} has status/tier → AC-2, AC-5

## Commands
- [x] `grep stripe requirements.txt` → stripe>=8.0.0 → AC-1
- [x] `python -m pytest gateway/tests/test_billing.py` → 7 passed → AC-1..5
- [x] `redis-cli TTL entitlement:tenant_alpha` → ~3600 after webhook → AC-2

## Acceptance-criteria coverage
- AC-1 Stripe SDK + env vars · AC-2 entitlement cache (TTL 3600) · AC-3 checkout endpoint · AC-4 portal endpoint · AC-5 signed webhook + event handling
