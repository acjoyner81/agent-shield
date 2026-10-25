"""Stripe billing: checkout, portal, and webhook endpoints."""

import json
from datetime import datetime, timezone
from typing import Optional

import redis
import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from config.settings import settings
from gateway.auth import get_verified_tenant
from gateway.entitlements import (
    clear_tenant_entitlements,
    get_tenant_entitlements,
    set_tenant_entitlements,
)
from gateway.telemetry import emit_billing_subscription_changed

stripe.api_key = settings.stripe_api_key
router = APIRouter(prefix="/v1/billing", tags=["billing"])
r = redis.Redis.from_url(settings.redis_url, decode_responses=True)

PRODUCT_TIER_MAP = {
    settings.stripe_product_starter: "starter",
    settings.stripe_product_pro: "pro",
    settings.stripe_product_enterprise: "enterprise",
}

ENTITLEMENT_KEY = "entitlement:{tenant_id}"
ENTITLEMENT_TTL = 3600


def cache_entitlement(tenant_id: str, data: dict) -> None:
    """Cache tenant entitlement in Redis with 1-hour TTL (spec AC-2)."""
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    r.setex(ENTITLEMENT_KEY.format(tenant_id=tenant_id), ENTITLEMENT_TTL, json.dumps(data))


def get_cached_entitlement(tenant_id: str) -> Optional[dict]:
    """Retrieve cached entitlement; returns None if not present."""
    raw = r.get(ENTITLEMENT_KEY.format(tenant_id=tenant_id))
    return json.loads(raw) if raw else None


def ensure_stripe_customer(tenant_id: str, email: Optional[str] = None) -> str:
    """Find or create a Stripe customer bound to this tenant."""
    customer_key = f"tenant:{tenant_id}:stripe_customer_id"
    existing = r.get(customer_key)
    if existing:
        return existing

    # Try to recover customer from Stripe by tenant_id metadata
    try:
        customers = stripe.Customer.list(limit=100)
        matches = []
        for c in customers.auto_paging_iter():
            if getattr(c, "metadata", None) and c.metadata.get("tenant_id") == tenant_id:
                matches.append(c)
        if len(matches) == 1:
            r.set(customer_key, matches[0].id)
            return matches[0].id
        if len(matches) > 1:
            raise HTTPException(status_code=409, detail="Multiple Stripe customers found for this tenant")
    except HTTPException:
        raise
    except Exception:
        pass

    customer = stripe.Customer.create(
        metadata={"tenant_id": tenant_id},
        email=email,
    )
    r.set(customer_key, customer.id)
    return customer.id


class CheckoutRequest(BaseModel):
    price_id: Optional[str] = None
    tier: Optional[str] = None

TIER_TO_PRICE_MAP = {
    "starter": settings.stripe_price_starter,
    "pro": settings.stripe_price_pro,
    "enterprise": settings.stripe_price_enterprise,
}

@router.post("/checkout")
async def create_checkout_session(
    request: Request,
    price_id: Optional[str] = None,
    payload: Optional[CheckoutRequest] = None,
    tenant_id: str = Depends(get_verified_tenant),
) -> dict:
    """AC-3: Generate a Stripe Checkout session and return the URL."""
    target_price = price_id
    if not target_price and payload:
        target_price = payload.price_id or TIER_TO_PRICE_MAP.get(payload.tier or "")
    if not target_price:
        try:
            body = await request.json()
            if isinstance(body, dict):
                target_price = body.get("price_id") or TIER_TO_PRICE_MAP.get(body.get("tier", ""))
        except Exception:
            pass

    if not target_price:
        raise HTTPException(
            status_code=503,
            detail="Billing is not configured. Set STRIPE_PRICE_<TIER> to an active Stripe price id.",
        )

    # Guard against a product id slipping in here: Stripe checkout needs a price
    # (`price_...`), and a `prod_...` value produces an opaque "No such price"
    # rejection that reads like an outage rather than a misconfiguration.
    if not target_price.startswith("price_"):
        raise HTTPException(
            status_code=503,
            detail="Billing is misconfigured. A Stripe price id (price_...) is required, not a product id.",
        )

    try:
        customer_id = ensure_stripe_customer(tenant_id)
        
        # Check existing subscriptions
        subscriptions = stripe.Subscription.list(customer=customer_id, status="all", limit=100)
        all_subs = []
        for s in getattr(subscriptions, "auto_paging_iter", lambda: [])():
            all_subs.append(s)
        if not all_subs and hasattr(subscriptions, "data"):
            all_subs = list(subscriptions.data)
        
        # Check for active/trialing subscriptions
        eligible = [s for s in all_subs if getattr(s, "status", None) in ("active", "trialing")]
        if len(eligible) == 1:
            sub = eligible[0]
            items = getattr(getattr(sub, "items", None), "data", []) or []
            base_item = None
            base_product_id = None
            for item in items:
                price = getattr(item, "price", None)
                product_id = getattr(price, "product", None) if price else None
                if isinstance(product_id, str) and product_id in PRODUCT_TIER_MAP:
                    base_item = item
                    base_product_id = product_id
                    break
            
            if base_item:
                current_tier = PRODUCT_TIER_MAP.get(base_product_id)
                target_tier = None
                for t, p in TIER_TO_PRICE_MAP.items():
                    if p == target_price:
                        target_tier = t
                        break
                
                if current_tier == target_tier and current_tier:
                    return {"action": "unchanged", "tier": current_tier, "subscription_id": getattr(sub, "id")}
                
                try:
                    stripe.SubscriptionItem.modify(
                        base_item.id,
                        price=target_price,
                        proration_behavior="create_prorations",
                    )
                    return {"action": "updated", "tier": target_tier or current_tier, "subscription_id": getattr(sub, "id")}
                except stripe.error.StripeError as e:
                    raise HTTPException(status_code=400, detail=f"Stripe error: {e.user_message}")
        elif len(eligible) > 1:
            raise HTTPException(status_code=409, detail="Multiple active subscriptions")
        
        # If no active subscription or modification failed/wasn't possible, create a new checkout session.
        session = stripe.checkout.Session.create(
            customer=customer_id,
            mode="subscription",
            line_items=[{"price": target_price, "quantity": 1}],
            success_url=settings.stripe_success_url,
            cancel_url=settings.stripe_cancel_url,
            metadata={"tenant_id": tenant_id},
            subscription_data={"metadata": {"tenant_id": tenant_id}},
        )
        return {"checkoutUrl": session.url, "session_id": session.id, "action": "checkout"}

    except HTTPException:
        raise
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=f"Stripe error: {e.user_message}")

    return {"checkoutUrl": session.url, "session_id": session.id, "action": "checkout"}


@router.post("/portal")
async def create_portal_session(
    tenant_id: str = Depends(get_verified_tenant),
) -> dict:
    """AC-4: Create a Stripe Customer Portal session for self-service management."""
    customer_key = f"tenant:{tenant_id}:stripe_customer_id"
    customer_id = r.get(customer_key)
    if not customer_id:
        raise HTTPException(status_code=404, detail="No Stripe customer found for this tenant")

    try:
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=settings.stripe_success_url,
        )
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=f"Stripe error: {e.user_message}")

    return {"portal_url": session.url}


@router.post("/webhook", include_in_schema=False)
async def stripe_webhook(
    request: Request,
    stripe_signature: Optional[str] = Header(None, alias="Stripe-Signature"),
) -> dict:
    """AC-5: Verify signature and process subscription events."""
    payload = await request.body()

    # Refuse before touching the library. This route is unauthenticated by
    # design, so an unset secret must not read as a bad signature: an operator
    # would go looking at Stripe's dashboard instead of at the missing setting.
    if not settings.can_verify_webhooks():
        raise HTTPException(
            status_code=503, detail="Webhook signature verification unavailable"
        )

    try:
        event = stripe.Webhook.construct_event(
            payload, stripe_signature, settings.stripe_webhook_secret
        )
    except (ValueError, stripe.error.SignatureVerificationError):
        raise HTTPException(status_code=400, detail="Invalid signature")

    if hasattr(event, "to_dict_recursive"):
        event = event.to_dict_recursive()
    elif hasattr(event, "to_dict"):
        event = event.to_dict()

    event_type = event["type"]
    obj = event["data"]["object"]

    if event_type in ("customer.subscription.created", "customer.subscription.updated"):
        tenant_id = obj.get("metadata", {}).get("tenant_id") or obj.get("customer")
        status = obj.get("status", "unknown")
        items = obj.get("items", {}).get("data", [])
        product_id = items[0]["price"]["product"] if items else None
        tier = PRODUCT_TIER_MAP.get(product_id, "free")

        if status in ("past_due", "unpaid"):
            tier = "free"

        entitlement = {"status": status, "tier": tier, "stripe_customer_id": obj.get("customer")}
        cache_entitlement(tenant_id, entitlement)
        set_tenant_entitlements(tenant_id, tier, status)

        emit_billing_subscription_changed(
            tenant_id=tenant_id,
            user_id=tenant_id,
            trace_id=tenant_id,
            span_id="unknown",
            action=status,
            stripe_customer_id=obj.get("customer"),
            tier=tier,
            status=status,
        )

    elif event_type == "customer.subscription.deleted":
        tenant_id = obj.get("metadata", {}).get("tenant_id") or obj.get("customer")
        r.delete(ENTITLEMENT_KEY.format(tenant_id=tenant_id))
        clear_tenant_entitlements(tenant_id)

        emit_billing_subscription_changed(
            tenant_id=tenant_id,
            user_id=tenant_id,
            trace_id=tenant_id,
            span_id="unknown",
            action="deleted",
            stripe_customer_id=obj.get("customer"),
            tier="free",
            status="canceled",
        )

    return {"status": "success"}
