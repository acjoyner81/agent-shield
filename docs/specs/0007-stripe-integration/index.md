# 0007. Hybrid Stripe Integration

**Date**: 2026-10-25
**Status**: In Progress

## Summary

Stripe handles base subscriptions, while Redis keeps tenant entitlements available to the gateway. Choosing a plan updates one recognized active or trialing subscription, or starts Checkout when the tenant has no active or trialing subscription and no unresolved subscription. Metered usage reporting remains separate.

## Requirements

**User stories**:
- As a tenant administrator, I want to subscribe to a base plan and pay for overage usage transparently so my team can access AI gateway tools.
- As a tenant administrator, I want to change my current plan without creating a second subscription.
- As a gateway operator, I want tenant entitlements and active subscription status to be verified locally in Redis before servicing requests.

**Acceptance criteria**:
- **AC-1**: Configure Stripe SDK and environment variables (`STRIPE_API_KEY`, `STRIPE_WEBHOOK_SECRET`) in `config/settings.py` and `requirements.txt`.
- **AC-2**: Implement entitlement helper functions in `gateway/billing.py` to cache active tenant subscriptions and tier limits in Redis keyed by `entitlement:{tenant_id}` with a 1-hour TTL.
- **AC-3**: Expose `/v1/billing/checkout` POST endpoint to generate Stripe Checkout sessions for base subscription plans and return a valid checkout URL.
- **AC-4**: Expose `/v1/billing/portal` POST endpoint to create Stripe Customer Portal sessions for self-service subscription management.
- **AC-5**: Implement `/v1/billing/webhook` POST endpoint with signature verification (`stripe.Webhook.construct_event`) to handle `customer.subscription.created`, `updated`, and `deleted` events, updating Redis entitlements instantly.
- **AC-6**: In Stripe test mode, a completed checkout and its signed subscription webhooks update the correct tenant's Redis entitlement with the expected tier and status. The customer portal opens for that same test customer and supports subscription management.
- **AC-7**: Test mode verification uses only Stripe test credentials, test products, and test prices. It does not use production tenant data or create real charges.
- **AC-8**: A plan selection updates the tenant's single active or trialing subscription to the configured recurring price. The update changes exactly one recognized base plan item, preserves its quantity and all other subscription items, and does not create another subscription. Webhook tier resolution uses the recognized base plan item rather than assuming the first subscription item is the base plan.
- **AC-9**: A tenant with no active or trialing subscription and no unresolved nonterminal subscription receives a new Checkout session. A tenant with an unresolved nonterminal subscription, multiple active or trialing subscriptions, or no recognizable base plan item receives a visible error and no new subscription is created.
- **AC-10**: A repeated selection of the current plan makes no Stripe modification and returns an unchanged result. A successful plan change gives the user visible confirmation; a new subscription selection redirects to the returned Stripe Checkout URL.
- **AC-11**: The tenant to Stripe customer mapping in Redis has no expiry, so it remains available for future plan changes and portal sessions after the entitlement cache expires.

## Decision

**Chosen option**: Option 1: Hybrid Base + Metered Billing with Redis Entitlement Caching

Implement Stripe SDK integration in FastAPI with Redis entitlement caching, checkout and portal endpoints, signed webhook event processing, and a single plan selection endpoint that updates an existing subscription or creates Checkout when no current subscription exists.

## Rationale

Reasoning and options: see [rationale.md](./rationale.md).

## Feature design

**Data model sketch**:
Entitlement state cached in Redis:
- Key: `entitlement:{tenant_id}` (JSON value with `status`, `tier`, `stripe_customer_id`, `updated_at`).
- Key: `tenant:{tenant_id}:stripe_customer_id` (non-expiring Stripe customer ID used to find the tenant's subscription and reopen the same customer's portal).
- A tenant may have at most one active or trialing subscription. Stripe remains the source of subscription state; signed subscription webhooks update the Redis entitlement.

**State transitions**:
- No active or trialing subscription, and no unresolved nonterminal subscription → create a Stripe Checkout session → checkout completion and signed webhook establish the entitlement.
- Missing tenant to customer mapping → search Stripe customers by `metadata.tenant_id`; one match restores the non-expiring Redis mapping, multiple matches return conflict, and no match is treated as no Stripe customer.
- Active or trialing subscription on another configured plan → update its recognized base price item with Stripe proration → signed webhook refreshes the entitlement.
- Active or trialing subscription already on the chosen plan → return unchanged without calling Stripe to modify it.
- A `canceled` or `incomplete_expired` subscription is terminal and does not block a new Checkout session. Any other nonterminal status outside `active` or `trialing`, or ambiguous subscription data → return a conflict and do not create or change a subscription.

**API surface**:
| Endpoint | Method | Key inputs | Key outputs | Auth | Key errors |
|---|---|---|---|---|---|
| /v1/billing/checkout | POST | `tier: starter \| pro \| enterprise`; legacy `price_id` only when it matches a configured plan price | Checkout, updated, or unchanged result | Bearer tenant credential | 400 Invalid input or Stripe error, 401 Unauthorized, 409 Conflict, 503 Not Configured |
| /v1/billing/portal | POST | None | `portal_url: str` | Bearer tenant credential | 400 Stripe error, 401 Unauthorized, 404 No Stripe customer |
| /v1/billing/webhook | POST | Raw body, `Stripe-Signature` | `{"status": "success"}` | Stripe signature | 400 Invalid Signature, 503 Verification unavailable |

`/v1/billing/checkout` accepts `tier` as its canonical input. For compatibility, it also accepts `price_id` only when the value exactly matches a configured plan price, then resolves it to that tier. It returns one of three results: `action: checkout` with `checkoutUrl` and `session_id`, `action: updated` with `tier` and `subscription_id`, or `action: unchanged` with `tier` and `subscription_id`. A 409 response means the existing Stripe subscription cannot be changed safely.

**Value sourcing**:
| Action | Value produced / displayed | Source |
|---|---|---|
| Entitlement Check | `is_active` | Redis `entitlement:{tenant_id}` status |
| Plan Selection | Requested tier and recurring price | Request `tier`, or a configured legacy `price_id` resolved to its tier, then `TIER_TO_PRICE_MAP` and `STRIPE_PRICE_*` configuration |
| Existing Customer | Customer ID | Non-expiring Redis mapping; when absent, Stripe customers searched by `metadata.tenant_id`, with exactly one match restored to Redis |
| Existing Subscription | All subscriptions and their status | Complete Stripe subscription listing for the resolved customer, including all statuses and pages |
| Eligible Subscription | Subscription to update | Exactly one active or trialing subscription; any nonterminal status other than active or trialing blocks Checkout and returns conflict |
| Base Subscription Item | Item to update | Exactly one subscription item whose product matches a configured `STRIPE_PRODUCT_*` value |
| Base Item Quantity | Quantity after price change | Existing Stripe subscription item quantity; the plan update preserves it |
| Checkout Session | `checkoutUrl` | `stripe.checkout.Session.create()` response |
| Plan Change Result | `action`, resulting `tier`, and `subscription_id` | Plan selection branch and Stripe subscription update response; same tier returns `unchanged` |
| Plan Change Proration | Proration behavior | Stripe `create_prorations`; the subscription changes now and invoice adjustments follow Stripe's normal billing cycle |
| Subscription Tenant | `tenant_id` | Authenticated tenant on checkout, carried in Stripe subscription metadata and read by the signed webhook |
| Subscription Tier | `tier` | The subscription item whose product ID matches a configured `STRIPE_PRODUCT_*` value, not the item's position in the list |
| Subscription Status | `status` | Stripe subscription `status` field in the signed webhook event |
| Portal Customer | `customer_id` | Persistent Redis `tenant:{tenant_id}:stripe_customer_id`, created during checkout |
| Portal Session | `portal_url` | `stripe.billing_portal.Session.create()` response |
| User Confirmation | Checkout redirect or visible updated / unchanged result | Discriminated plan selection response handled by the Angular billing service |

**Key invariants**:
- Invalid Webhook Signature: Webhook requests failing `stripe.Webhook.construct_event` MUST be rejected immediately with HTTP 400.
- Fallback Default: Tenants without explicit billing entitlement in Redis default to trial/free status.
- Plan changes update only a recognized base subscription item. The existing item quantity and all other items stay unchanged, including metered or unknown items.
- Exactly one recognized base plan item must exist on the eligible subscription. Multiple recognized base items, multiple eligible subscriptions, or any unresolved nonterminal subscription return a conflict instead of creating or changing a subscription.
- Subscription discovery reads all status values and paginates through the complete Stripe result. `canceled` and `incomplete_expired` are terminal; any other status outside `active` and `trialing` is unresolved and blocks Checkout.
- When the Redis customer mapping is absent, customer recovery uses Stripe customer metadata `tenant_id`. Zero matches permits new customer creation only for the new Checkout flow; multiple matches return conflict.
- The tenant to customer mapping has no TTL. Entitlement cache expiry does not erase the Stripe identity required for later billing actions.
- A plan change uses Stripe proration behavior `create_prorations` and does not create an out of cycle invoice.

**Security model**:
Strict signature validation on webhooks protects against forgery. Stripe customer IDs are securely bound to internal tenant IDs.

**Configuration required**:
- `STRIPE_API_KEY`: Secret key for Stripe API operations.
- `STRIPE_WEBHOOK_SECRET`: Signing secret for webhook verification.
- `STRIPE_SUCCESS_URL`: Redirect URL following successful checkout.
- `STRIPE_CANCEL_URL`: Redirect URL following canceled checkout.
- `STRIPE_PRICE_STARTER`, `STRIPE_PRICE_PRO`, `STRIPE_PRICE_ENTERPRISE`: Price IDs for the selected Stripe mode.
- `STRIPE_PRODUCT_STARTER`, `STRIPE_PRODUCT_PRO`, `STRIPE_PRODUCT_ENTERPRISE`: Product IDs for the same Stripe mode, used to map subscriptions to tiers.

**Critical test scenarios**:
- Happy path: `/v1/billing/checkout` creates valid Stripe checkout session, verifies **AC-3**
- Plan change: an active or trialing subscription changes its recognized base price without creating Checkout, verifies **AC-8**
- Webhook item order: a non-plan or metered item appears first, and the recognized base item still determines the tenant tier, verifies **AC-5**, **AC-8**
- New subscription: no active or trialing subscription creates Checkout, verifies **AC-9**
- Safe conflicts: unresolved status, multiple eligible subscriptions, or unknown plan item changes nothing and is visible to the user, verifies **AC-9**
- Same plan: selecting the current recurring price returns unchanged without a Stripe modification, verifies **AC-10**
- Customer continuity: the customer mapping survives beyond the one hour entitlement TTL, verifies **AC-11**
- Expired mapping recovery: one Stripe customer match is restored, while multiple matches conflict and create no Checkout, verifies **AC-8**, **AC-9**, **AC-11**
- Portal flow: `/v1/billing/portal` returns customer portal URL for valid tenant, verifies **AC-4**
- Webhook signature validation: `/v1/billing/webhook` verifies payload signature and updates Redis entitlement, verifies **AC-5**
- Test mode flow: a dedicated test tenant completes checkout, receives a signed subscription webhook, sees the expected entitlement, and opens the portal for the same Stripe customer, verifies **AC-6**
- Test safety: confirm the gateway uses test credentials and test catalog IDs before checkout, verifies **AC-7**

## Build plan

1. Add `stripe>=8.0.0` dependency to `requirements.txt` and update `config/settings.py` with Stripe configurations, satisfies **AC-1** ✅
2. Implement entitlement caching helper functions in `gateway/billing.py`, satisfies **AC-2** ✅
3. Implement `/v1/billing/checkout` and `/v1/billing/portal` endpoints in `gateway/billing.py`, satisfies **AC-3**, **AC-4** ✅
4. Implement `/v1/billing/webhook` with signature verification and event handling in `gateway/billing.py`, satisfies **AC-5** ✅
5. Register billing router into main FastAPI app in `gateway/main.py`, satisfies **AC-3**, **AC-4**, **AC-5** ✅
6. Add unit tests in `gateway/tests/test_billing.py`, satisfies **AC-1**, **AC-2**, **AC-3**, **AC-4**, **AC-5** ✅
7. Verify the authenticated tenant ID is carried into subscription metadata, configured test price and product IDs reach the gateway, and checkout, signed subscription events, Redis entitlement, and portal access work in Stripe test mode, satisfies **AC-6**
8. Document and run a test mode preflight that confirms the active key is a test secret and every configured product and price exists in that same test account. Do not infer test or live mode from product or price ID prefixes, satisfies **AC-7**
9. Persist and recover the tenant to customer mapping, inspect all subscription statuses, update exactly one recognized base item while preserving other items, and add safe conflict and same plan behavior, satisfies **AC-8**, **AC-9**, **AC-10**, **AC-11**
10. Handle the plan selection response in Angular: redirect only for Checkout, show confirmation for an update or no-op, and surface invalid response or request errors, satisfies **AC-9**, **AC-10**
11. Verify the active subscription change and no subscription Checkout flows with Stripe test mode and signed webhooks, satisfies **AC-6**, **AC-8**, **AC-9**, **AC-11**

## Consequences

**Positive**:
- Provides enterprise monetization foundation for base subscriptions and metered usage.
- High-performance entitlement validation in Redis without gateway latency degradation.

**Negative / tradeoffs**:
- Requires maintaining webhook endpoint security and webhook secret environment management.
- A test mode run needs a Stripe test catalog, a local webhook listener, and a dedicated tenant. The end to end check can expose gaps that mocked unit tests cannot.
- Stripe proration can create invoice adjustments that the customer pays or receives on the normal billing cycle. Multiple or unresolved subscriptions require a customer support path instead of an automatic guess.

## Follow-up

- [ ] Connect real-time Redis usage counters to Stripe Metered Usage reporting APIs.
