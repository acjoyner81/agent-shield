# 0007. Hybrid Stripe Integration — Rationale

## Context

AgentShield requires a monetization engine that supports base monthly subscription tiers alongside metered usage for high-volume LLM requests and tool executions. Tenants must be checked for active subscription status and feature entitlements at the API gateway perimeter without incurring real-time network latency from direct Stripe API calls during execution.

We need a flexible integration that handles checkout sessions, customer portal redirection, webhook event processing, and Redis entitlement caching.

## Options considered

### Option 1: Hybrid Base + Metered Billing with Redis Entitlement Caching (Recommended)

Combine Stripe Checkout/Portal for base plan management with Redis entitlement caching and webhook synchronization.

**Pros**:
- Zero network latency overhead during API gateway request execution due to local Redis cache lookups.
- Asynchronous webhook processing keeps subscription state synchronized reliably.
- Supports both fixed base monthly recurring fees and variable usage billing.

**Cons**:
- Requires webhook handler security hardening and idempotency checks.

### Option 2: Direct Synchronous Stripe API Verification

Call Stripe API synchronously on every billing or entitlement check.

**Pros**:
- Always guarantees real-time accuracy without caching layer complexity.

**Cons**:
- Adds 100-300ms latency to gateway requests and exposes system to Stripe API rate limits.

## Rationale

Redis entitlement caching ensures that gateway performance and low latency are preserved for LLM requests. Webhook synchronization ensures tenant subscription state updates take effect immediately when events occur in Stripe.
