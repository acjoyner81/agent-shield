from fastapi import APIRouter, Request, HTTPException, Header
import stripe
import logging
from config.settings import settings
from gateway.entitlements import set_tenant_entitlements, clear_tenant_entitlements

router = APIRouter()
logger = logging.getLogger("gateway.webhooks")

# Update the dictionary mapping to use lowercased setting attributes
PRODUCT_TIER_MAP = {
    settings.stripe_product_starter: "starter",
    settings.stripe_product_pro: "pro",
    settings.stripe_product_enterprise: "enterprise",
}

@router.post("/v1/webhooks/stripe")
@router.post("/api/v1/billing/webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None)):
    payload = await request.body()
    
    try:
        event = stripe.Webhook.construct_event(
            payload, stripe_signature, settings.stripe_webhook_secret
        )
    except (ValueError, stripe.error.SignatureVerificationError):
        raise HTTPException(status_code=400, detail="Invalid signature")

    event_type = event["type"]
    data = event["data"]["object"]

    if event_type in ["customer.subscription.created", "customer.subscription.updated"]:
        tenant_id = data.get("metadata", {}).get("tenant_id") or data.get("customer")
        status = data.get("status")
        items = data.get("items", {}).get("data", [])
        
        product_id = items[0]["price"]["product"] if items else None
        tier = PRODUCT_TIER_MAP.get(product_id, "free")

        if status in ["past_due", "unpaid"]:
            logger.warning(f"Subscription {status} for tenant {tenant_id}. Applying soft downgrade.")

        set_tenant_entitlements(tenant_id, tier, status)

    elif event_type == "customer.subscription.deleted":
        tenant_id = data.get("metadata", {}).get("tenant_id") or data.get("customer")
        clear_tenant_entitlements(tenant_id)

    return {"status": "success"}