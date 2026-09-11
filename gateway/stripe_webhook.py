import os
import json
import secrets
import redis
import stripe
from fastapi import FastAPI, Request, HTTPException, Header

app = FastAPI()
r = redis.Redis(host=os.getenv("REDIS_HOST", "localhost"), port=6379, db=0, decode_responses=True)

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "sk_test_mock_key")
endpoint_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "whsec_mock_secret")

TIER_LIMITS = {
    "starter": {"rpm": 20, "daily_budget": 10.0},
    "pro": {"rpm": 100, "daily_budget": 75.0},
    "enterprise": {"rpm": 1000, "daily_budget": 500.0}
}

@app.post("/api/v1/billing/webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None)):
    payload = await request.body()

    try:
        event = stripe.Webhook.construct_event(payload, stripe_signature, endpoint_secret)
    except (ValueError, stripe.error.SignatureVerificationError):
        raise HTTPException(status_code=400, detail="Invalid Stripe signature or payload")

    # Handle Successful Checkout
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        tenant_email = session.get("customer_details", {}).get("email")
        tier_selected = session.get("metadata", {}).get("tier", "starter").lower()

        # Generate Secure Tenant API Key
        api_key = f"ak_live_{secrets.token_urlsafe(24)}"
        tenant_id = f"tenant_{secrets.token_hex(6)}"

        tier_config = TIER_LIMITS.get(tier_selected, TIER_LIMITS["starter"])

        # Persist Tenant Record in Redis
        tenant_data = {
            "tenant_id": tenant_id,
            "email": tenant_email,
            "tier": tier_selected,
            "rate_limit_rpm": tier_config["rpm"],
            "daily_budget_usd": tier_config["daily_budget"],
            "stripe_customer_id": session.get("customer")
        }

        r.set(f"tenant:key:{api_key}", json.dumps(tenant_data))
        r.set(f"tenant:email:{tenant_email}", api_key)

    return {"status": "success"}