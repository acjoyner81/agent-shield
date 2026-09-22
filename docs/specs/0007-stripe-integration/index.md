# 0007. Hybrid Stripe Integration

**Date**: 2026-09-16
**Status**: In Progress

## Summary

Implement a hybrid monetization model combining base monthly subscriptions with metered billing for token and tool call usage using Stripe. Active tenant entitlements and billing status are cached in Redis to validate requests locally while Stripe webhooks synchronize subscription state asynchronously.

## Requirements

**User stories**:
- As a tenant administrator, I want to subscribe to a base plan and pay for overage usage transparently so my team can access AI gateway tools.
- As a gateway operator, I want tenant entitlements and active subscription status to be verified locally in Redis before servicing requests.

**Acceptance criteria**:
- **AC-1**: Configure Stripe SDK and environment variables (`STRIPE_API_KEY`, `STRIPE_WEBHOOK_SECRET`) in `config/settings.py` and `requirements.txt`.
- **AC-2**: Implement entitlement helper functions in `gateway/billing.py` to cache active tenant subscriptions and tier limits in Redis keyed by `entitlement:{tenant_id}` with a 1-hour TTL.
- **AC-3**: Expose `/v1/billing/checkout` POST endpoint to generate Stripe Checkout sessions for base subscription plans and return a valid checkout URL.
- **AC-4**: Expose `/v1/billing/portal` POST endpoint to create Stripe Customer Portal sessions for self-service subscription management.
- **AC-5**: Implement `/v1/billing/webhook` POST endpoint with signature verification (`stripe.Webhook.construct_event`) to handle `customer.subscription.created`, `updated`, and `deleted` events, updating Redis entitlements instantly.

## Decision

**Chosen option**: Option 1: Hybrid Base + Metered Billing with Redis Entitlement Caching

Implement Stripe SDK integration in FastAPI with Redis entitlement caching, checkout/portal endpoints, and signed webhook event processing.

**Implementation skills**: `fastapi` (`tiangolo/fastapi`, `pip install fastapi`) · `redis` (`redis/redis-py`, `pip install redis`) · `stripe` (`stripe/stripe-python`, `pip install stripe`)

## Feature design

**Data model sketch**:
Entitlement state cached in Redis:
- Key: `entitlement:{tenant_id}` (JSON hash with `status`, `tier`, `stripe_customer_id`, `updated_at`).

**API surface**:
| Endpoint | Method | Key inputs | Key outputs | Auth | Key errors |
|---|---|---|---|---|---|
| /v1/billing/checkout | POST | `price_id: str` | `checkout_url: str` | Bearer | 400 Bad Request, 401 Unauthorized |
| /v1/billing/portal | POST | None | `portal_url: str` | Bearer | 401 Unauthorized, 404 Not Found |
| /v1/billing/webhook | POST | Raw body, `Stripe-Signature` | `{"status": "success"}` | Webhook Signature | 400 Invalid Signature |

**Value sourcing**:
| Action | Value produced / displayed | Source |
|---|---|---|
| Entitlement Check | `is_active` | Redis `entitlement:{tenant_id}` status |
| Checkout Session | `checkout_url` | `stripe.checkout.Session.create()` response |
| Portal Session | `portal_url` | `stripe.billing_portal.Session.create()` response |

**Key invariants**:
- Invalid Webhook Signature: Webhook requests failing `stripe.Webhook.construct_event` MUST be rejected immediately with HTTP 400.
- Fallback Default: Tenants without explicit billing entitlement in Redis default to trial/free status.

**Security model**:
Strict signature validation on webhooks protects against forgery. Stripe customer IDs are securely bound to internal tenant IDs.

**Configuration required**:
- `STRIPE_API_KEY`: Secret key for Stripe API operations.
- `STRIPE_WEBHOOK_SECRET`: Signing secret for webhook verification.
- `STRIPE_SUCCESS_URL`: Redirect URL following successful checkout.
- `STRIPE_CANCEL_URL`: Redirect URL following canceled checkout.

**Critical test scenarios**:
- Happy path: `/v1/billing/checkout` creates valid Stripe checkout session, verifies **AC-3**
- Portal flow: `/v1/billing/portal` returns customer portal URL for valid tenant, verifies **AC-4**
- Webhook signature validation: `/v1/billing/webhook` verifies payload signature and updates Redis entitlement, verifies **AC-5**

## Build plan

1. Add `stripe>=8.0.0` dependency to `requirements.txt` and update `config/settings.py` with Stripe configurations, satisfies **AC-1** ✅
2. Implement entitlement caching helper functions in `gateway/billing.py`, satisfies **AC-2** ✅
3. Implement `/v1/billing/checkout` and `/v1/billing/portal` endpoints in `gateway/billing.py`, satisfies **AC-3**, **AC-4** ✅
4. Implement `/v1/billing/webhook` with signature verification and event handling in `gateway/billing.py`, satisfies **AC-5** ✅
5. Register billing router into main FastAPI app in `gateway/main.py`, satisfies **AC-3**, **AC-4**, **AC-5** ✅
6. Add unit tests in `gateway/tests/test_billing.py`, satisfies **AC-1**, **AC-2**, **AC-3**, **AC-4**, **AC-5** ✅

## Consequences

**Positive**:
- Provides enterprise monetization foundation for base subscriptions and metered usage.
- High-performance entitlement validation in Redis without gateway latency degradation.

**Negative / tradeoffs**:
- Requires maintaining webhook endpoint security and webhook secret environment management.

## Follow-up

- [ ] Connect real-time Redis usage counters to Stripe Metered Usage reporting APIs.
