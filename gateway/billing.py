"""Stripe billing: checkout, portal, and webhook endpoints."""

import json
from datetime import datetime, timezone
from typing import Optional

import redis
import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request

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

    customer = stripe.Customer.create(
        metadata={"tenant_id": tenant_id},
        email=email,
    )
    r.set(customer_key, customer.id, ex=ENTITLEMENT_TTL)
    return customer.id


@router.post("/checkout")
async def create_checkout_session(
    price_id: Optional[str] = None,
    tenant_id: str = Depends(get_verified_tenant),
) -> dict:
    """AC-3: Generate a Stripe Checkout session and return the URL."""
    if not price_id:
        raise HTTPException(status_code=400, detail="price_id is required")

    try:
        customer_id = ensure_stripe_customer(tenant_id)
        session = stripe.checkout.Session.create(
            customer=customer_id,
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=settings.stripe_success_url,
            cancel_url=settings.stripe_cancel_url,
            metadata={"tenant_id": tenant_id},
        )
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=f"Stripe error: {e.user_message}")

    return {"checkout_url": session.url, "session_id": session.id}


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


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: Optional[str] = Header(None, alias="Stripe-Signature"),
) -> dict:
    """AC-5: Verify signature and process subscription events."""
    payload = await request.body()

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
