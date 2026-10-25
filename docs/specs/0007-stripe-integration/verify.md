# Verify: Hybrid Stripe Integration · spec 0007 · updated 2026-10-25
_Steps derived from spec 0007 acceptance criteria. `/check verify` runs these; `/test` locks the durable ones._

## UI / manual
- [x] POST /v1/billing/checkout {price_id} → returns checkout.stripe.com URL → AC-3
- [x] POST /v1/billing/checkout (no price_id) → 503 "Billing is not configured" (was 400 "price_id is required" before 2026-10-22; a price id is `price_...`, and a product id is refused with a misconfiguration 503) → AC-3
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
- AC-6 checkout, tenant entitlement, and portal test mode flow · planned, not yet run
- AC-7 test credentials and catalog safety · planned, not yet run
- AC-8 existing subscription plan change · planned, not yet run
- AC-9 new checkout and safe conflict handling · planned, not yet run
- AC-10 same plan no-op and user feedback · planned, not yet run
- AC-11 non-expiring tenant to customer mapping · planned, not yet run

## Stripe test mode plan · not yet run

The existing unit tests mock Stripe. Keep them as the fast, repeatable check, then run this separate sandbox flow to verify Stripe itself, checkout, portal access, and webhook delivery. Use a dedicated test tenant and no production data.

### Before the run
- [ ] Create or select Starter, Pro, and Enterprise products and recurring prices in Stripe test mode. Record their `prod_...` and `price_...` IDs without putting secrets in this file.
- [ ] Put only the Stripe test secret key in the local environment as `STRIPE_API_KEY`. Confirm it begins with `sk_test_`.
- [ ] Set the test price IDs as `STRIPE_PRICE_STARTER`, `STRIPE_PRICE_PRO`, and `STRIPE_PRICE_ENTERPRISE`. Set the matching test product IDs as `STRIPE_PRODUCT_STARTER`, `STRIPE_PRODUCT_PRO`, and `STRIPE_PRODUCT_ENTERPRISE`.
- [ ] In Stripe Dashboard test mode, confirm each configured price and product exists, is active, and matches the expected tier.
- [ ] Do not infer test or live mode from `prod_...` product IDs or `price_...` price IDs. Verify every ID in the same test account selected by the test secret key.
- [ ] If the key does not begin with `sk_test_`, or an ID is missing from the Stripe test catalog, stop before sending a checkout request.
- [ ] Confirm the test Customer Portal configuration allows subscription management.
- [x] Confirm Docker Compose passes the Stripe API key, webhook secret, price IDs, and product IDs into `gateway-python`.
- [x] Confirm `.env.example` uses the Stripe variable names read by `config/settings.py`.
- [ ] Use a dedicated test tenant credential. Do not use a production tenant or a shared demo tenant.

### Checkout and webhook flow
- [ ] Start the local stack with the test environment values and rebuild the gateway container so it receives the latest configuration.
- [ ] Run `stripe listen --forward-to localhost:8000/v1/billing/webhook` and set the listener's signing secret as `STRIPE_WEBHOOK_SECRET`. Keep that secret local.
- [ ] Request `/v1/billing/checkout` for the dedicated tenant and a configured test tier. Confirm the response contains a Checkout URL and save the returned Stripe customer and subscription IDs for this test only.
- [ ] Open the URL and complete checkout with a Stripe documented test payment method. Confirm no real charge is made.
- [ ] Confirm the listener receives a signed `customer.subscription.created` event and the gateway returns success. Confirm `tenant_id` in the subscription metadata ties the webhook to the dedicated tenant.
- [ ] Inspect Redis and confirm `entitlement:<test tenant>` contains the expected tier and status, and that the key is associated with the test tenant rather than only the Stripe customer ID.
- [ ] Open `/v1/billing/portal` for the same tenant. Confirm the returned URL opens the portal for the same test customer.
- [ ] Change the plan and cancel the test subscription using the test portal or Stripe test dashboard. Confirm the signed `customer.subscription.updated` and `customer.subscription.deleted` events update or clear that tenant's entitlement as expected.

### Plan selection
- [ ] With one active or trialing subscription, select a different configured tier. Confirm the existing base plan item changes with `create_prorations`, no Checkout Session is created, and the signed webhook updates the tenant entitlement.
- [ ] Include a metered or unknown item before the base plan item in the subscription response. Confirm the update and webhook still find the base item by configured product ID and preserve the other item and base quantity.
- [ ] Select the current tier again. Confirm the response is unchanged and Stripe receives no update request.
- [ ] With no active or trialing subscription and no unresolved nonterminal subscription, select a tier. Confirm a Checkout Session is created.
- [ ] For an unresolved nonterminal status, multiple active or trialing subscriptions, or zero or multiple recognized base items, confirm the endpoint returns a visible conflict and creates or changes no subscription.
- [ ] Confirm subscription discovery checks every status and every result page, including terminal subscriptions, before deciding whether Checkout is safe.
- [ ] Confirm the tenant to customer mapping remains in Redis without an expiry after the entitlement key expires.
- [ ] Delete the mapping for a test tenant with one existing Stripe customer. Confirm customer metadata restores the same mapping and no duplicate customer or subscription is created. Confirm multiple matching customers return a visible conflict.
- [ ] Confirm the portal and plan selection continue to use the same Stripe customer after a gateway restart.

### Failure checks and cleanup
- [ ] Send an invalid signature and confirm the webhook returns HTTP 400 without changing Redis.
- [ ] Confirm the test mode preflight stops the run if the key or any catalog ID cannot be verified in the test account.
- [ ] Cancel any remaining test subscriptions and remove the dedicated test tenant's local Redis data when the run is complete.

### Tenant binding
- [ ] Confirm the checkout subscription metadata carries the authenticated tenant ID and the signed webhook writes the entitlement under that tenant ID, not under the Stripe customer ID.
