import json
from typing import Dict, Any
import redis
from config.settings import settings
from gateway.telemetry import emit_billing_subscription_changed

redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
ENTITLEMENT_TTL_SECONDS = 86400

TIER_RPM_MAP = {
    "free": settings.default_free_rpm,
    "starter": settings.starter_rpm,
    "pro": settings.pro_rpm,
    "enterprise": settings.enterprise_rpm,
}

def get_tenant_entitlements(tenant_id: str) -> Dict[str, Any]:
    key = f"tenant:{tenant_id}:entitlements"
    cached = redis_client.get(key)
    
    if cached:
        return json.loads(cached)
        
    return {
        "tier": "free",
        "status": "active",
        "rpm_limit": settings.default_free_rpm
    }

def set_tenant_entitlements(tenant_id: str, tier: str, status: str) -> Dict[str, Any]:
    key = f"tenant:{tenant_id}:entitlements"
    
    if status in ["past_due", "unpaid", "canceled"]:
        effective_tier = "free"
    else:
        effective_tier = tier.lower()

    rpm_limit = TIER_RPM_MAP.get(effective_tier, settings.default_free_rpm)
    payload = {
        "tier": effective_tier,
        "status": status,
        "rpm_limit": rpm_limit
    }
    
    redis_client.setex(key, ENTITLEMENT_TTL_SECONDS, json.dumps(payload))
    emit_billing_subscription_changed(
        tenant_id=tenant_id,
        user_id=tenant_id,
        trace_id=tenant_id,
        span_id="unknown",
        action=status,
        stripe_customer_id=None,
        tier=effective_tier,
        status=status,
    )
    return payload

def clear_tenant_entitlements(tenant_id: str) -> None:
    key = f"tenant:{tenant_id}:entitlements"
    redis_client.delete(key)
    emit_billing_subscription_changed(
        tenant_id=tenant_id,
        user_id=tenant_id,
        trace_id=tenant_id,
        span_id="unknown",
        action="deleted",
        stripe_customer_id=None,
        tier="free",
        status="canceled",
    )