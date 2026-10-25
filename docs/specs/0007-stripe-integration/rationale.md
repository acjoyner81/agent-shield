# 0007. Hybrid Stripe Integration — Rationale

## Context

AgentShield requires a monetization engine that supports base monthly subscription tiers alongside metered usage for high-volume LLM requests and tool executions. Tenants must be checked for active subscription status and feature entitlements at the API gateway perimeter without incurring real-time network latency from direct Stripe API calls during execution.

We need a flexible integration that handles checkout sessions, customer portal redirection, webhook event processing, and Redis entitlement caching.

The plan cards call the checkout endpoint, but the current path only creates a new Checkout Session. It does not find or update an existing subscription, and the portal gives no immediate feedback when the customer selects another plan. The tenant to customer mapping also expires after one hour, so it cannot reliably support later subscription changes. A prior mapping may already have expired, so plan selection must safely restore the customer identity from the tenant metadata already attached to Stripe customers.

## Options considered

### Option 1: Hybrid billing with cached entitlements and plan updates (Recommended)

Use Stripe Checkout when the tenant has no active or trialing subscription and no unresolved nonterminal subscription. Update the recognized base plan item for one active or trialing subscription, and keep tenant entitlements in Redis through signed webhook synchronization. Resolve the tenant's Stripe customer from a non-expiring Redis mapping, restoring it from Stripe customer metadata when the mapping is missing.

**Pros**:
- Entitlement checks use the local Redis cache rather than calling Stripe during gateway requests.
- Asynchronous webhook processing keeps subscription state synchronized reliably.
- Supports both fixed base monthly recurring fees and variable usage billing.

**Cons**:
- Requires webhook handler security hardening and idempotency checks.

### Option 2: Direct synchronous Stripe verification and plan updates

Call Stripe on every entitlement check and use the same direct API path for plan updates.

**Pros**:
- Always guarantees real-time accuracy without caching layer complexity.

**Cons**:
- Adds Stripe request latency to gateway requests and makes entitlement checks depend on Stripe availability and rate limits.

### Option 3: Always create a new Checkout Session

Keep each plan card as a new subscription purchase and leave existing plan management to Stripe's Customer Portal.

**Pros**:
- The checkout endpoint stays simple and delegates plan management to Stripe.
- The Customer Portal already supports subscription management.

**Cons**:
- Selecting a plan from the portal can create a second subscription instead of changing the current one.
- The plan cards do not give a direct, predictable way to switch tiers.

### Option 4: Send all plan changes to the Customer Portal

Use the Stripe portal for new and existing customers to select and change plans.

**Pros**:
- Stripe owns the subscription selection and update interface.
- Fewer subscription update cases need custom gateway logic.

**Cons**:
- The user leaves the AgentShield plan page for every change.
- The gateway cannot use the chosen plan card as a direct, tenant scoped plan action.

## Rationale

Redis entitlement caching ensures that gateway performance and low latency are preserved for LLM requests. For billing actions, the authenticated tenant's persistent customer mapping identifies the Stripe subscription. If an older mapping has expired, Stripe customer metadata provides a recovery path without relying on a customer email or creating another subscription. The endpoint changes exactly one recognized base plan item, uses Stripe's `create_prorations` behavior, and returns a distinct result for Checkout, update, or no change. It inspects all subscription statuses and pages; ambiguous or unresolved subscriptions fail visibly instead of creating another subscription. Signed webhooks remain the source for Redis entitlement updates and identify the base plan by configured product ID rather than list position.

The tenant to customer mapping must outlive the one hour entitlement cache. Otherwise later plan changes cannot locate the subscription, and a retry may create a new Stripe customer. The existing billing tests mock Stripe, so they cannot prove that a real test mode checkout, subscription change, webhook, and customer portal stay bound to the same tenant. A separate test mode run covers that gap without risking real charges or replacing the fast unit tests.
